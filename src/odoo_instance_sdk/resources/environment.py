from __future__ import annotations

import contextlib
import hashlib
import importlib
import re
import shutil
import sqlite3
import subprocess
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, Union, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    DatabaseAlreadyExistsError,
    EnvironmentConflictError,
    EnvironmentNotFoundError,
    EnvironmentResolutionError,
    InstanceConfigurationError,
    MasterPasswordRequiredError,
    NonLocalInstanceError,
    PgAdminDatabaseNotFoundError,
    PgAdminEnvironmentNotFoundError,
    PgAdminError,
    PgAdminNotEligibleError,
    PgAdminUnavailableError,
    PlanError,
    PlanValidationError,
    PostgresClusterError,
    StalePlanError,
)
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.database_preparation import (
    classify_freshness,
    compare_provenance,
)
from odoo_instance_sdk.internal.db_name import validate_db_name, validate_filestore_containment
from odoo_instance_sdk.internal.generated_config import generate_config
from odoo_instance_sdk.internal.locks import (
    environment_lock_path,
    exclusive_lock,
    provisioning_lock_path,
    python_env_lock_path,
)
from odoo_instance_sdk.internal.odoo_config import (
    get_admin_passwd,
    infer_base_url,
    parse_db_names,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.paths import get_environments_root
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.process_env import sanitized_child_environment
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.urls import assert_local
from odoo_instance_sdk.models import (
    Backup,
    BackupFormat,
    BackupFreshness,
    BackupProvenanceComparison,
    BackupProvenanceStatus,
    DatabasePreparationAction,
    DatabasePreparationResult,
    DatabaseRefreshOptions,
    EnvironmentCheckoutPlan,
    EnvironmentCheckoutResult,
    EnvironmentPythonMode,
    PgAdminOpenResult,
    PostgresClusterState,
)
from odoo_instance_sdk.models import (
    DevelopmentEnvironment as _DevelopmentEnvironment,
)
from odoo_instance_sdk.models import (
    EnvironmentDatabaseMode as _EnvironmentDatabaseMode,
)
from odoo_instance_sdk.models import (
    EnvironmentState as _EnvironmentState,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import CopyJournalStage, normalize_db_host

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
    from odoo_instance_sdk.internal.proc import ProcessExecutor, ProcessResult, RunContext, Step
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

EnvironmentSelector = Union[str, "DevelopmentEnvironment"]
type _PlanningError = PlanError | ConfigError | EnvironmentConflictError

# Re-export the dependency-neutral contract for backwards-compatible imports.
EnvironmentState = _EnvironmentState
DevelopmentEnvironment = _DevelopmentEnvironment
EnvironmentDatabaseMode = _EnvironmentDatabaseMode

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


class EnvironmentCheckoutOptions(msgspec.Struct, frozen=True, kw_only=True):
    base_ref: str | None = None
    name: str | None = None
    config_path: Path | None = None
    db_mode: EnvironmentDatabaseMode = EnvironmentDatabaseMode.SHARED
    source_database: str | None = None
    target_database: str | None = None
    odoo_bin: Path | None = None
    python: str | Path | None = None
    create_venv: bool = False
    http_port: int | None = None


@dataclass(frozen=True, slots=True)
class _PythonMode:
    mode: Literal["create", "reuse"]
    interpreter: str | None


@dataclass(frozen=True, slots=True)
class CopyCleanupPlan:
    """Validated COPY ownership retained for one destructive cleanup operation."""

    target_database: str
    backup_id: uuid.UUID | None
    instance: object | None
    backup: Backup | None
    stage: CopyJournalStage


@dataclass(frozen=True, slots=True)
class _CheckoutPlan:
    """Fully resolved immutable checkout inputs; no mutation is allowed while building it."""

    project: ProjectConfig
    env_id: uuid.UUID
    name: str
    repo_root: Path
    git_common_dir: str
    branch: str
    base_ref: str
    base_revision: str
    worktree: Path
    venv: Path
    generated_config: Path
    dependency_lock: Path
    env_root: Path
    python_path: str
    python_owned: bool
    python_selector: str | Path | None
    http_interface: str
    http_port: int
    db_mode: EnvironmentDatabaseMode
    source_database: str | None
    target_database: str | None
    source_config: Path | None
    config_values: Mapping[str, str]
    odoo_bin: str
    runtime_cwd: str
    dependency_inputs: tuple[str, ...]
    worktree_argv: tuple[str, ...]
    created_at: str
    options: EnvironmentCheckoutOptions


@dataclass(frozen=True, slots=True)
class _CheckoutSnapshot:
    """One resolved checkout input set shared by preview and execution."""

    private: _CheckoutPlan
    public: EnvironmentCheckoutPlan
    execution_plan: ExecutionPlan


@dataclass(frozen=True, slots=True)
class _CheckoutPlanningState:
    private: _CheckoutPlan
    provenance: BackupProvenanceComparison
    freshness: BackupFreshness
    warnings: tuple[str, ...]
    public: EnvironmentCheckoutPlan | None = None
    execution_plan: ExecutionPlan | None = None
    snapshot: _CheckoutSnapshot | None = None


@dataclass(frozen=True, slots=True)
class _PlanningOutcome:
    state: _CheckoutPlanningState | None = None
    error: _PlanningError | None = None


# ``Command`` deliberately serializes only its execution plan.  Checkout also
# has a long-standing domain-plan projection, so keep that projection beside
# the private prepared command without adding it (or the callback) to the
# public command model.
_CHECKOUT_PUBLIC_PLANS: dict[int, EnvironmentCheckoutPlan] = {}


def _checkout_public_plan(command: Command[DevelopmentEnvironment]) -> EnvironmentCheckoutPlan:
    """Read the domain projection captured alongside one command."""
    plan = _CHECKOUT_PUBLIC_PLANS.get(id(command))
    if plan is None:
        raise PlanError("checkout command has no captured domain plan")
    return plan


class _ExpressionResult(Protocol):
    def bind(
        self, mapper: Callable[[_CheckoutPlanningState], _ExpressionResult]
    ) -> _ExpressionResult: ...

    def default_with(
        self, getter: Callable[[_PlanningError], _PlanningOutcome]
    ) -> _CheckoutPlanningState | _PlanningOutcome: ...


class _ExpressionApi(Protocol):
    def Ok(self, value: _CheckoutPlanningState) -> _ExpressionResult: ...

    def Error(self, error: _PlanningError) -> _ExpressionResult: ...


_StrList = list[str]


@dataclass(slots=True, kw_only=True)
class EnvironmentResource:
    _client: OdooClient

    def _prepare_checkout(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
        dry_run_paths: bool,
    ) -> _CheckoutPlan:
        if isinstance(project, ProjectConfig):
            project_cfg = project
            project_path = project_cfg.repository_root
        else:
            project_path = Path(project)
            project_cfg = ProjectConfig.load(project_path)

        # A plan must be inspectable without creating the durable catalog or
        # running migrations in user data.
        # Do not open the durable catalog until all COPY preconditions have
        # passed.  Opening it can create/migrate user state, which must not be
        # the observable result of a rejected checkout.
        catalog = None

        from odoo_instance_sdk.internal.git_worktree import (
            local_branch_exists,
            remote_branches,
            rev_parse_git_common_dir,
            rev_parse_toplevel,
        )

        repo_root = rev_parse_toplevel(project_path)
        git_common = rev_parse_git_common_dir(repo_root)
        git_common_str = str(git_common)

        self._verify_tools()

        base_ref = options.base_ref or project_cfg.default_base_ref or "HEAD"
        from odoo_instance_sdk.internal.git_worktree import rev_parse_verify

        base_revision = rev_parse_verify(repo_root, base_ref)

        if local_branch_exists(repo_root, branch):
            worktree_mode: Literal["local", "remote", "new"] = "local"
        elif remote_branches(repo_root, branch):
            worktree_mode = "remote"
        else:
            worktree_mode = "new"

        source_config = self._resolve_source_config(options, project_cfg, repo_root)
        if source_config is not None and not source_config.is_file():
            raise ConfigError(f"Source config not found: {source_config}")

        python_mode = self._resolve_python_mode(options, project_cfg, repo_root)

        cfg_dict = parse_odoo_config(source_config) if source_config is not None else {}

        db_mode = options.db_mode
        source_db, target_db = self._resolve_dbs(
            options, project_cfg, cfg_dict, db_mode, branch, repo_root
        )

        http_interface = cfg_dict.get("http_interface", "127.0.0.1") or "127.0.0.1"
        http_port = self._allocate_port(
            options.http_port, project_cfg, catalog, http_interface, repo_root
        )

        env_id = uuid.uuid4()
        key = repo_key(repo_root, git_common)
        env_root = get_environments_root(ensure_exists=not dry_run_paths) / key / str(env_id)
        worktree = env_root / "worktree"
        venv = env_root / "venv"
        generated_cfg = env_root / "odoo.conf"
        lock_file = env_root / "requirements.lock"
        if worktree_mode == "local":
            worktree_argv: tuple[str, ...] = (
                "git",
                "-C",
                str(repo_root),
                "worktree",
                "add",
                str(worktree),
                branch,
            )
        elif worktree_mode == "remote":
            worktree_argv = (
                "git",
                "-C",
                str(repo_root),
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree),
                f"refs/remotes/origin/{branch}",
            )
        else:
            worktree_argv = (
                "git",
                "-C",
                str(repo_root),
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree),
                base_ref,
            )

        if options.create_venv:
            python_path = str(venv)
            python_owned = True
        else:
            assert python_mode.interpreter is not None
            python_path = python_mode.interpreter
            python_owned = False

        name = options.name or f"{repo_root.name}:{branch}"

        now = datetime.now(UTC).isoformat()
        odoo_bin = self._resolve_odoo_bin(options, project_cfg, repo_root)
        runtime_cwd = self._resolve_runtime_cwd(project_cfg, repo_root, worktree)
        dependency_paths = list(project_cfg.requirements)
        odoo_requirements = _find_odoo_requirements(repo_root)
        if odoo_requirements is not None and str(odoo_requirements) not in dependency_paths:
            dependency_paths.append(str(odoo_requirements))
        dependency_inputs = tuple(_rebase_requirement_paths(dependency_paths, repo_root, worktree))

        return _CheckoutPlan(
            project=project_cfg,
            env_id=env_id,
            name=name,
            repo_root=repo_root,
            git_common_dir=git_common_str,
            branch=branch,
            base_ref=base_ref,
            worktree=worktree,
            venv=venv,
            generated_config=generated_cfg,
            dependency_lock=lock_file,
            env_root=env_root,
            python_path=python_path,
            python_owned=python_owned,
            python_selector=(options.python or project_cfg.python),
            http_interface=http_interface,
            http_port=http_port,
            db_mode=db_mode,
            source_database=source_db,
            target_database=target_db,
            source_config=source_config,
            config_values=MappingProxyType(cfg_dict),
            odoo_bin=odoo_bin,
            runtime_cwd=runtime_cwd,
            dependency_inputs=dependency_inputs,
            base_revision=base_revision,
            worktree_argv=worktree_argv,
            created_at=now,
            options=options,
        )

    def _plan_checkout(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> _CheckoutPlan:
        return self._prepare_checkout(project, branch, options=options, dry_run_paths=True)

    def refresh_database(
        self,
        project: ProjectConfig | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
    ) -> DatabasePreparationResult:
        """Prepare the configured project database through the shared coordinator."""
        from odoo_instance_sdk.internal.database_preparation import (
            DatabasePreparationCoordinator,
        )

        return DatabasePreparationCoordinator(self._client).refresh_database(
            project, options=options
        )

    def _refresh_checkout_if_stale(
        self,
        project: ProjectConfig | Path,
        options: EnvironmentCheckoutOptions,
        *,
        freshness: BackupFreshness,
    ) -> ProjectConfig | Path:
        if isinstance(project, ProjectConfig):
            project_config = project
            root = project.repository_root
        else:
            root = Path(project)
            project_config = ProjectConfig.load(root)
        if project_config.refresh_after_hours is None:
            return project

        if freshness in (
            BackupFreshness.MISSING,
            BackupFreshness.UNAVAILABLE,
            BackupFreshness.STALE,
        ):
            if project_config.test_instance is None:
                raise ConfigError(
                    "configured database freshness requires [test_instance] preparation settings"
                )
            from odoo_instance_sdk.internal.database_preparation import (
                DatabasePreparationCoordinator,
            )

            DatabasePreparationCoordinator(self._client).prepare(
                project_config,
                options=DatabaseRefreshOptions(restore=True),
                coalesce=True,
            )
            manifest = root / ".odcli" / "project.toml"
            if manifest.is_file():
                return ProjectConfig.load(root)
        return project

    def _audit_checkout_plan(
        self,
        project: ProjectConfig | Path,
        branch: str,
        options: EnvironmentCheckoutOptions,
    ) -> tuple[BackupProvenanceComparison, BackupFreshness, tuple[str, ...]]:
        """Resolve provenance and freshness without creating checkout artifacts."""
        project_config = (
            project if isinstance(project, ProjectConfig) else ProjectConfig.load(project)
        )
        root = project_config.repository_root
        source_config = self._resolve_source_config(options, project_config, root)
        config_values = parse_odoo_config(source_config) if source_config is not None else {}
        source_database, _ = self._resolve_dbs(
            options,
            project_config,
            config_values,
            options.db_mode,
            branch,
            root,
        )
        effective_base = (
            options.base_ref
            if options.base_ref is not None
            else project_config.default_base_ref or "HEAD"
        )
        provenance_backup = _restore_audit_backup(
            self._client,
            config_values,
            source_database,
            available=False,
        )
        available_backup = _restore_audit_backup(
            self._client,
            config_values,
            source_database,
            available=True,
        )
        recorded_branch = (
            provenance_backup.source_git_branch if provenance_backup is not None else None
        )
        comparison = compare_provenance(effective_base, recorded_branch)
        if comparison.status is BackupProvenanceStatus.MISMATCHED:
            raise EnvironmentConflictError(
                "backup_provenance_mismatch",
                "backup source branch does not match checkout base ref",
                details={
                    "expected_base_ref": comparison.expected_base_ref,
                    "recorded_branch": comparison.recorded_branch,
                },
            )
        warnings: tuple[str, ...] = ()
        if comparison.status is BackupProvenanceStatus.UNKNOWN:
            if options.source_database is not None and source_database is not None:
                warnings = (
                    f"Backup provenance is unknown for explicit source database "
                    f"{source_database!r}; branch compatibility could not be verified.",
                )
            else:
                raise EnvironmentConflictError(
                    "backup_provenance_unknown",
                    "backup provenance is unknown; pass --source-db explicitly or refresh a "
                    "provenance-bearing backup",
                    details={"source_database": source_database},
                )
        freshness = classify_freshness(available_backup, project_config.refresh_after_hours)
        if (
            project_config.refresh_after_hours is not None
            and freshness is not BackupFreshness.FRESH
            and project_config.test_instance is None
        ):
            raise ConfigError(
                "configured database freshness requires [test_instance] preparation settings"
            )
        return comparison, freshness, warnings

    def _build_checkout_snapshot(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions,
    ) -> _CheckoutSnapshot:
        """Compose pure checkout stages around one read-only input capture."""
        captured = self._collect_checkout_inputs(project, branch, options=options)
        expression_api = cast("_ExpressionApi", importlib.import_module("expression"))
        if captured.error is not None:
            result = expression_api.Error(captured.error)
        elif captured.state is None:
            result = expression_api.Error(
                PlanValidationError("checkout planning captured no resolved inputs")
            )
        else:
            result = expression_api.Ok(captured.state)
        for stage in (
            self._resolve_checkout_snapshot,
            _validate_checkout_stage,
            _normalize_checkout_stage,
            _capture_checkout_stage,
        ):

            def apply_stage(
                state: _CheckoutPlanningState,
                stage: Callable[[_CheckoutPlanningState], _PlanningOutcome] = stage,
            ) -> _ExpressionResult:
                return _planning_result(expression_api, stage(state))

            result = result.bind(apply_stage)
        resolved = result.default_with(_planning_error_outcome)
        if isinstance(resolved, _PlanningOutcome):
            if resolved.error is not None:
                raise resolved.error
            resolved_state = resolved.state
        else:
            resolved_state = cast("_CheckoutPlanningState | None", resolved)
        if resolved_state is None or resolved_state.snapshot is None:
            raise PlanValidationError("checkout planning produced no snapshot")
        return resolved_state.snapshot

    def _collect_checkout_inputs(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions,
    ) -> _PlanningOutcome:
        """Collect read-only inputs outside Expression; no durable state is mutated."""
        try:
            provenance, freshness, warnings = self._audit_checkout_plan(project, branch, options)
            private = self._prepare_checkout(project, branch, options=options, dry_run_paths=True)
            return _PlanningOutcome(
                state=_CheckoutPlanningState(
                    private=private,
                    provenance=provenance,
                    freshness=freshness,
                    warnings=warnings,
                )
            )
        except (PlanError, ConfigError, EnvironmentConflictError) as exc:
            return _PlanningOutcome(error=exc)

    def _resolve_checkout_snapshot(self, state: _CheckoutPlanningState) -> _PlanningOutcome:
        """Resolve the immutable private plan into the typed stage state."""
        if state.private.project.repository_root != state.private.repo_root:
            return _PlanningOutcome(
                error=PlanValidationError("checkout repository identity is inconsistent")
            )
        return _PlanningOutcome(state=state)

    def _build_checkout_execution_snapshot(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions,
    ) -> _CheckoutSnapshot:
        """Refresh a stale configured source, then capture one execution snapshot."""
        _, freshness, _ = self._audit_checkout_plan(project, branch, options)
        refreshed_project = self._refresh_checkout_if_stale(project, options, freshness=freshness)
        return self._build_checkout_snapshot(refreshed_project, branch, options=options)

    def _command_from_snapshot(
        self,
        snapshot: _CheckoutSnapshot,
        *,
        executor: ProcessExecutor | None = None,
    ) -> Command[DevelopmentEnvironment]:
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import SubprocessExecutor, prepared_command

        prepared = prepared_command(
            lambda context: self._run_checkout_snapshot(context, snapshot),
            _checkout_steps(snapshot.private),
            executor=executor or SubprocessExecutor(),
        )
        return Command.from_prepared(snapshot.execution_plan, prepared)

    def _run_checkout_snapshot(
        self, context: RunContext[DevelopmentEnvironment], snapshot: _CheckoutSnapshot
    ) -> DevelopmentEnvironment:
        plan = snapshot.private
        if plan.db_mode is EnvironmentDatabaseMode.COPY:
            self._preflight_copy_checkout(plan)
        with exclusive_lock(provisioning_lock_path()):
            self._validate_checkout_snapshot(snapshot)
            context.action("checkout.catalog")
            catalog = self._client.get_catalog()
            self._revalidate_checkout_locked(catalog, plan)
            return self._do_checkout(catalog, plan, context=context)

    def _validate_checkout_snapshot(self, snapshot: _CheckoutSnapshot) -> None:
        """Reject changed read-only inputs before the catalog or artifacts mutate."""
        plan = snapshot.private
        from odoo_instance_sdk.internal.git_worktree import (
            rev_parse_git_common_dir,
            rev_parse_toplevel,
            rev_parse_verify,
        )

        actual_identity = (
            str(rev_parse_toplevel(plan.repo_root)),
            str(rev_parse_git_common_dir(plan.repo_root)),
            rev_parse_verify(plan.repo_root, plan.base_ref),
        )
        expected_identity = (str(plan.repo_root), plan.git_common_dir, plan.base_revision)
        if actual_identity != expected_identity:
            raise StalePlanError(
                "checkout Git identity changed after planning",
                expected=list(expected_identity),
                actual=list(actual_identity),
            )

        current_project = ProjectConfig.load(plan.repo_root)
        current_source_config = self._resolve_source_config(
            plan.options, current_project, plan.repo_root
        )
        current_config_values = (
            parse_odoo_config(current_source_config) if current_source_config is not None else {}
        )
        current_source, current_target = self._resolve_dbs(
            plan.options,
            current_project,
            current_config_values,
            plan.db_mode,
            plan.branch,
            plan.repo_root,
        )
        current_base_ref = plan.options.base_ref or current_project.default_base_ref or "HEAD"
        if (
            current_base_ref != plan.base_ref
            or current_source != plan.source_database
            or current_target != plan.target_database
            or dict(current_config_values) != dict(plan.config_values)
        ):
            raise StalePlanError("checkout resolved inputs changed after planning")

        current_provenance, current_freshness, current_warnings = self._audit_checkout_plan(
            plan.repo_root, plan.branch, plan.options
        )
        if (
            current_provenance != snapshot.public.provenance
            or current_freshness != snapshot.public.freshness
            or current_warnings != snapshot.public.warnings
        ):
            raise StalePlanError("checkout database or provenance identity changed after planning")

        for path in (
            plan.env_root,
            plan.worktree,
            plan.venv,
            plan.generated_config,
            plan.dependency_lock,
        ):
            if path.exists():
                raise StalePlanError(
                    "checkout deterministic future path is no longer available",
                    expected=str(path),
                    actual="exists",
                )

    def plan_checkout(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> EnvironmentCheckoutPlan:
        """Return a secret-free checkout plan without performing mutations."""
        command = self.checkout_command(project, branch, options=options)
        return _checkout_public_plan(command)

    def checkout_command(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> Command[DevelopmentEnvironment]:
        """Capture checkout inputs once and return the inspectable command."""
        snapshot = self._build_checkout_snapshot(project, branch, options=options)
        command = self._command_from_snapshot(snapshot)
        _CHECKOUT_PUBLIC_PLANS[id(command)] = snapshot.public
        return command

    def checkout_with_plan(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> EnvironmentCheckoutResult:
        """Execute checkout and return its final secret-free typed plan."""
        snapshot = self._build_checkout_execution_snapshot(project, branch, options=options)
        command = self._command_from_snapshot(snapshot)
        _CHECKOUT_PUBLIC_PLANS[id(command)] = snapshot.public
        environment = command.run()
        return EnvironmentCheckoutResult(
            environment=environment,
            plan=_checkout_public_plan(command),
        )

    def checkout(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> DevelopmentEnvironment:
        snapshot = self._build_checkout_execution_snapshot(project, branch, options=options)
        command = self._command_from_snapshot(snapshot)
        _CHECKOUT_PUBLIC_PLANS[id(command)] = snapshot.public
        return command.run()

    def _revalidate_checkout_locked(self, catalog: object, plan: _CheckoutPlan) -> None:
        cat = cast("BackupCatalog", catalog)
        existing = cat.active_environment_for(plan.git_common_dir, plan.branch)
        if existing is not None:
            raise EnvironmentConflictError(
                "active_environment_exists",
                f"Active environment already exists for branch {plan.branch!r}",
                details={"branch": plan.branch, "existing_id": existing["id"]},
            )
        try:
            find_free_port(
                "http",
                cat,
                requested=plan.http_port,
                host=plan.http_interface,
                exclude_project=plan.repo_root,
            )
        except EnvironmentConflictError:
            raise EnvironmentConflictError(
                "port_in_use", f"Port {plan.http_port} is no longer available"
            )

    def _do_checkout(  # noqa: C901
        self,
        catalog: object,
        plan: _CheckoutPlan,
        *,
        context: RunContext[DevelopmentEnvironment],
    ) -> DevelopmentEnvironment:
        runtime_json = _encode_runtime_json(plan.odoo_bin, plan.runtime_cwd)
        env_row = {
            "id": str(plan.env_id),
            "name": plan.name,
            "repository_root": str(plan.repo_root),
            "git_common_dir": plan.git_common_dir,
            "branch": plan.branch,
            "base_ref": plan.base_ref,
            "worktree_path": str(plan.worktree),
            "generated_config_path": str(plan.generated_config),
            "python_environment_path": plan.python_path,
            "python_environment_owned": plan.python_owned,
            "dependency_lock_path": str(plan.dependency_lock),
            "db_mode": plan.db_mode,
            "source_db_name": plan.source_database,
            "target_db_name": plan.target_database,
            "backup_id": None,
            "runtime_json": runtime_json,
            "state": EnvironmentState.CREATING,
            "created_at": plan.created_at,
            "last_used_at": None,
            "removed_at": None,
            "last_error": None,
        }

        cat = cast("BackupCatalog", catalog)
        cat.create_environment(env_row)
        cat.add_environment_event(str(plan.env_id), "checkout", "started")

        created_paths: list[Path] = []
        backup_id: uuid.UUID | None = None
        try:
            plan.worktree.parent.mkdir(parents=True, exist_ok=True)
            worktree_result = cast("ProcessResult", context.process("checkout.worktree"))
            if worktree_result.returncode != 0:
                stderr = str(worktree_result.stderr or "").strip()
                if "is already checked out at" in stderr or "already used by worktree" in stderr:
                    raise EnvironmentConflictError(  # noqa: TRY301
                        "branch_in_use", f"Branch {plan.branch!r} is already checked out"
                    )
                raise ConfigError(f"git worktree add failed: {stderr}")  # noqa: TRY301
            created_paths.append(plan.worktree)

            if plan.source_config is not None:
                context.action("checkout.generated_config")
                db_name_for_config = (
                    plan.target_database
                    if plan.db_mode == EnvironmentDatabaseMode.COPY
                    else plan.source_database
                )
                if db_name_for_config is None:
                    db_name_for_config = plan.source_database or ""
                generate_config(
                    plan.source_config,
                    plan.generated_config,
                    repo_root=plan.repo_root,
                    worktree=plan.worktree,
                    http_interface=plan.http_interface,
                    http_port=plan.http_port,
                    db_name=db_name_for_config,
                )
                created_paths.append(plan.generated_config)

            if plan.options.create_venv and plan.python_selector is not None:
                venv_result = cast("ProcessResult", context.process("checkout.venv"))
                if venv_result.returncode != 0:
                    raise ConfigError(  # noqa: TRY301
                        f"uv venv failed: {_process_stderr(venv_result)}".strip()
                    )
                created_paths.append(plan.venv)

            env_obj = self._get_env_row(cat, plan.env_id)
            with exclusive_lock(python_env_lock_path(env_obj.python_environment_path)):
                if plan.dependency_inputs:
                    compile_result = cast(
                        "ProcessResult", context.process("checkout.dependencies.compile")
                    )
                    if compile_result.returncode != 0 and not plan.dependency_lock.is_file():
                        raise ConfigError(  # noqa: TRY301
                            "uv pip compile failed and no prior lock: "
                            f"{_process_stderr(compile_result)}"
                        )
                    install_result = cast(
                        "ProcessResult", context.process("checkout.dependencies.install")
                    )
                    if install_result.returncode != 0:
                        raise ConfigError(  # noqa: TRY301
                            f"uv pip install failed: {_process_stderr(install_result)}".strip()
                        )
                    created_paths.append(plan.dependency_lock)

            context.action("checkout.database")

            if (
                plan.db_mode == EnvironmentDatabaseMode.COPY
                and plan.source_database is not None
                and plan.target_database is not None
            ):
                backup_id = self._do_copy_restore(
                    cat=cat,
                    env_id=plan.env_id,
                    source_config=plan.source_config,
                    cfg_dict=plan.config_values,
                    source_db=plan.source_database,
                    target_db=plan.target_database,
                )

            cat.update_environment_state(str(plan.env_id), EnvironmentState.READY)
            cat.add_environment_event(str(plan.env_id), "checkout", "succeeded")
            context.action("checkout.cleanup")
            return self._get_env_row(cat, plan.env_id)

        except BaseException as exc:
            context.action("checkout.cleanup")
            self._cleanup_on_failure(
                cat=cat,
                env_id=plan.env_id,
                repo_root=plan.repo_root,
                created_paths=created_paths,
                env_root=plan.env_root,
                backup_id=backup_id,
                error=exc,
            )
            raise

    def _get_env_row(self, cat: object, env_id: uuid.UUID) -> DevelopmentEnvironment:

        catalog = cast("BackupCatalog", cat)
        row = catalog.get_environment(str(env_id))
        if row is None:
            raise RuntimeError("environment row disappeared after checkout")
        return _row_to_env(row)

    def _do_copy_restore(
        self,
        *,
        cat: object,
        env_id: uuid.UUID,
        source_config: Path | None,
        cfg_dict: Mapping[str, str],
        source_db: str,
        target_db: str,
    ) -> uuid.UUID:

        catalog = cast("BackupCatalog", cat)
        if source_config is None:
            raise ConfigError("copy mode requires a source config")
        base_url = infer_base_url(cfg_dict)
        assert_local(base_url)
        master_pwd = get_admin_passwd(cfg_dict)
        if master_pwd is None:
            raise MasterPasswordRequiredError("copy mode requires admin_passwd in source config")

        instance = self._client.instance.from_config(source_config, master_password=master_pwd)
        db_port = instance.config.db_port or 5432
        catalog.upsert_copy_journal(
            str(env_id),
            target_database=target_db,
            db_host=instance.config.db_host,
            db_port=db_port,
            db_user=instance.config.db_user,
            backup_id=None,
            stage=CopyJournalStage.PREPARED,
        )

        try:
            existing_dbs = instance.databases.list()
        except Exception as e:
            raise InstanceConfigurationError(
                f"Source Odoo HTTP endpoint unavailable for copy mode: {e}"
            ) from e

        if target_db in {db.name for db in existing_dbs}:
            raise DatabaseAlreadyExistsError(
                f"Target database {target_db!r} already exists on {base_url}"
            )

        backup = instance.databases.backup(source_db, format=BackupFormat.ZIP, filestore=True)
        catalog.update_environment(str(env_id), {"backup_id": str(backup.id)})
        catalog.upsert_copy_journal(
            str(env_id),
            target_database=target_db,
            db_host=instance.config.db_host,
            db_port=db_port,
            db_user=instance.config.db_user,
            backup_id=str(backup.id),
            stage=CopyJournalStage.BACKED_UP,
        )

        # The restore endpoint can create a database and then fail.  Persist
        # uncertainty first so compensation never assumes it did not happen.
        catalog.upsert_copy_journal(
            str(env_id),
            target_database=target_db,
            db_host=instance.config.db_host,
            db_port=db_port,
            db_user=instance.config.db_user,
            backup_id=str(backup.id),
            stage=CopyJournalStage.RESTORE_PENDING,
        )
        instance.databases.restore(backup, target_db, copy=True, neutralize_database=True)
        catalog.upsert_copy_journal(
            str(env_id),
            target_database=target_db,
            db_host=instance.config.db_host,
            db_port=db_port,
            db_user=instance.config.db_user,
            backup_id=str(backup.id),
            stage=CopyJournalStage.RESTORED,
        )

        return backup.id

    def _preflight_copy_checkout(self, plan: _CheckoutPlan) -> None:
        """Perform every COPY rejection check before creating owned artifacts."""
        if plan.source_config is None:
            raise ConfigError("copy mode requires a source config")
        if plan.source_database is None or plan.target_database is None:
            raise ConfigError("copy mode requires source and target databases")
        base_url = infer_base_url(plan.config_values)
        assert_local(base_url)
        master_pwd = get_admin_passwd(plan.config_values)
        if master_pwd is None:
            raise MasterPasswordRequiredError("copy mode requires admin_passwd in source config")
        instance = self._client.instance.from_config(plan.source_config, master_password=master_pwd)
        try:
            existing = instance.databases.names()
        except Exception as exc:
            raise InstanceConfigurationError(
                f"Source Odoo HTTP endpoint unavailable for copy mode: {exc}"
            ) from exc
        if plan.target_database in set(existing):
            raise DatabaseAlreadyExistsError(
                f"Target database {plan.target_database!r} already exists on {base_url}"
            )

    def _cleanup_on_failure(
        self,
        *,
        cat: object,
        env_id: uuid.UUID,
        repo_root: Path,
        created_paths: list[Path],
        env_root: Path,
        backup_id: uuid.UUID | None,
        error: BaseException,
    ) -> None:

        catalog = cast("BackupCatalog", cat)
        if backup_id is None:
            row = catalog.get_environment(str(env_id))
            if row is not None and row["backup_id"] is not None:
                backup_id = uuid.UUID(str(row["backup_id"]))
        cleanup_failed = self._rollback_copy_checkout(catalog, env_id, backup_id)
        # A restored copy must remain diagnosable when compensation cannot prove
        # that the target database is gone.  In particular, do not delete the
        # generated config (the cluster identity) or its owned backup first.
        if not cleanup_failed:
            cleanup_failed = self._cleanup_created_paths(repo_root, created_paths)

        msg = sanitize_last_error(str(error)) or type(error).__name__
        if cleanup_failed:
            catalog.update_environment_state(
                str(env_id), EnvironmentState.CLEANUP_FAILED, last_error=msg
            )
        else:
            catalog.update_environment_state(str(env_id), EnvironmentState.FAILED, last_error=msg)
        catalog.add_environment_event(str(env_id), "checkout", "failed", message=msg)

    def _rollback_copy_checkout(  # noqa: C901
        self, catalog: BackupCatalog, env_id: uuid.UUID, backup_id: uuid.UUID | None
    ) -> bool:
        """Compensate a failed COPY checkout without losing the deletion capability."""
        journal = catalog.get_copy_journal(str(env_id))
        if journal is None:
            return self._cleanup_backup(catalog, backup_id) if backup_id is not None else False

        stage = CopyJournalStage(str(journal["stage"]))
        if stage in (CopyJournalStage.RESTORE_PENDING, CopyJournalStage.RESTORED):
            row = catalog.get_environment(str(env_id))
            if row is None:
                return True
            config_path = Path(str(row["generated_config_path"]))
            if not config_path.is_file():
                return True
            try:
                cfg = parse_odoo_config(config_path)
                master_pwd = get_admin_passwd(cfg)
                if master_pwd is None:
                    return True
                instance = self._client.instance.from_config(
                    config_path, master_password=master_pwd
                )
                target = str(journal["target_database"])
                exists = instance.databases.exists(target)
                if exists:
                    instance.databases.drop(target)
                    if instance.databases.exists(target):
                        return True
            except Exception:
                return True
            catalog.upsert_copy_journal(
                str(env_id),
                target_database=str(journal["target_database"]),
                db_host=str(journal["db_host"]),
                db_port=int(journal["db_port"]),
                db_user=str(journal["db_user"]) if journal["db_user"] is not None else None,
                backup_id=str(journal["backup_id"]) if journal["backup_id"] is not None else None,
                stage=CopyJournalStage.DROPPED,
            )
            stage = CopyJournalStage.DROPPED

        if stage in (
            CopyJournalStage.PREPARED,
            CopyJournalStage.BACKED_UP,
            CopyJournalStage.DROPPED,
        ):
            journal_backup_id = journal["backup_id"]
            resolved_backup_id = (
                uuid.UUID(str(journal_backup_id)) if journal_backup_id is not None else backup_id
            )
            if resolved_backup_id is not None and self._cleanup_backup(catalog, resolved_backup_id):
                return True
            if stage is CopyJournalStage.DROPPED:
                catalog.upsert_copy_journal(
                    str(env_id),
                    target_database=str(journal["target_database"]),
                    db_host=str(journal["db_host"]),
                    db_port=int(journal["db_port"]),
                    db_user=str(journal["db_user"]) if journal["db_user"] is not None else None,
                    backup_id=str(journal_backup_id) if journal_backup_id is not None else None,
                    stage=CopyJournalStage.BACKUP_DELETED,
                )
        return False

    def _cleanup_created_paths(self, repo_root: Path, created_paths: list[Path]) -> bool:
        cleanup_failed = False
        for p in created_paths:
            if p.name == "worktree":
                from odoo_instance_sdk.internal.git_worktree import worktree_remove

                try:
                    worktree_remove(repo_root, p)
                except Exception:
                    cleanup_failed = True
            else:
                try:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=False)
                    else:
                        p.unlink(missing_ok=True)
                except OSError:
                    cleanup_failed = True
        return cleanup_failed

    def _cleanup_backup(self, catalog: object, backup_id: uuid.UUID) -> bool:

        cat = cast("BackupCatalog", catalog)
        try:
            row = cat.get_by_id(str(backup_id))
            if row is not None:
                backup = _row_to_backup(row)
                if backup is not None:
                    self._client.backups.delete(backup)
        except Exception:
            return True
        return False

    def sync_python(
        self,
        selector: EnvironmentSelector,
        *,
        upgrade: bool = False,
    ) -> DevelopmentEnvironment:
        env = (
            self._resolve_selector(selector, include_removed=False)
            if isinstance(selector, str)
            else selector
        )
        catalog = self._client.get_catalog()
        catalog.add_environment_event(str(env.id), "sync", "started")
        with (
            exclusive_lock(environment_lock_path(str(env.id))),
            exclusive_lock(python_env_lock_path(env.python_environment_path)),
        ):
            return self._do_sync_python(catalog, env, upgrade=upgrade)

    def record_use(self, env: DevelopmentEnvironment) -> None:
        catalog = self._client.get_catalog()
        now = datetime.now(UTC).isoformat()
        catalog.record_environment_use(str(env.id), now)

    def _do_sync_python(
        self, catalog: BackupCatalog, env: DevelopmentEnvironment, *, upgrade: bool
    ) -> DevelopmentEnvironment:
        project = _load_project(env)
        worktree = Path(env.worktree_path)
        repo_root = Path(env.repository_root)
        inputs = _rebase_requirement_paths(list(project.requirements), repo_root, worktree)
        odoo_req = _find_odoo_requirements(worktree)
        if inputs or odoo_req is not None:
            try:
                self._compile_requirements(env, project, upgrade=upgrade)
                self._install_requirements(env)
                catalog.add_environment_event(str(env.id), "sync", "succeeded")
            except _CompileFailed:
                catalog.add_environment_event(
                    str(env.id),
                    "sync",
                    "failed",
                    message="uv pip compile failed; kept existing lock",
                )
        else:
            catalog.add_environment_event(
                str(env.id),
                "sync",
                "succeeded",
                message="no requirements to compile",
            )
        return self._get_env_row(catalog, env.id)

    def _compile_and_install(
        self, env: DevelopmentEnvironment, project: ProjectConfig, *, upgrade: bool
    ) -> None:
        worktree = Path(env.worktree_path)
        repo_root = Path(env.repository_root)
        inputs = _rebase_requirement_paths(list(project.requirements), repo_root, worktree)
        odoo_req = _find_odoo_requirements(worktree)
        if not inputs and odoo_req is None:
            return
        try:
            self._compile_requirements(env, project, upgrade=upgrade)
        except _CompileFailed:
            if not Path(env.dependency_lock_path).is_file():
                raise
        self._install_requirements(env)

    def _run_uv_venv(self, venv: Path, selector: str) -> None:
        venv.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["uv", "venv", str(venv), "--python", selector],
            env=sanitized_child_environment(),
            shell=False,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ConfigError(f"uv venv failed: {proc.stderr.strip()}")

    def _compile_requirements(
        self, env: DevelopmentEnvironment, project: ProjectConfig, *, upgrade: bool
    ) -> Path:
        worktree = Path(env.worktree_path)
        repo_root = Path(env.repository_root)
        inputs = _rebase_requirement_paths(list(project.requirements), repo_root, worktree)
        odoo_req = _find_odoo_requirements(worktree)
        if odoo_req is not None:
            inputs.append(str(odoo_req))
        if not inputs:
            raise ConfigError("no requirements to compile; set project.requirements")
        lock_file = Path(env.dependency_lock_path)
        cmd: list[str] = ["uv", "pip", "compile", *inputs]
        if upgrade:
            cmd.append("--upgrade")
        cmd.extend(["-o", str(lock_file)])
        proc = subprocess.run(
            cmd,
            env=sanitized_child_environment(),
            shell=False,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            if lock_file.is_file():
                raise _CompileFailed(proc.stderr.strip() or "uv pip compile failed")
            raise ConfigError(f"uv pip compile failed and no prior lock: {proc.stderr.strip()}")
        return lock_file

    def _install_requirements(self, env: DevelopmentEnvironment) -> None:
        lock_file = Path(env.dependency_lock_path)
        if not lock_file.is_file():
            raise ConfigError(f"requirements lock missing: {lock_file}")
        if env.python_environment_owned:
            venv = Path(env.python_environment_path)
            python_bin = str(venv / "bin" / "python")
            cmd: list[str] = ["uv", "pip", "sync", "--python", python_bin, str(lock_file)]
        else:
            cmd = [
                "uv",
                "pip",
                "install",
                "--python",
                env.python_environment_path,
                "-r",
                str(lock_file),
            ]
        proc = subprocess.run(
            cmd,
            env=sanitized_child_environment(),
            shell=False,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ConfigError(
                f"uv pip {'sync' if env.python_environment_owned else 'install'} failed: "
                f"{proc.stderr.strip()}"
            )

    def get(self, selector: EnvironmentSelector) -> DevelopmentEnvironment:
        if isinstance(selector, DevelopmentEnvironment):
            return selector
        return self._resolve_selector(selector, include_removed=True)

    def open_pgadmin(self, selector: EnvironmentSelector) -> PgAdminOpenResult:
        """Open the selected environment through the private pgAdmin lifecycle seam.

        Resolution and all security-critical preconditions live here so callers
        cannot supply browser-controlled database or endpoint values. The
        lifecycle itself is intentionally supplied by the later pgAdmin task.
        """
        try:
            env = self.get(selector)
        except EnvironmentNotFoundError:
            raise PgAdminEnvironmentNotFoundError() from None
        except EnvironmentResolutionError:
            raise PgAdminUnavailableError() from None
        self._require_pgadmin_environment(env)
        database = self._pgadmin_database(env)
        cluster = self._healthy_owned_compose(env)
        instance = self._configured_pgadmin_instance(env)
        self._require_pgadmin_database(instance, database)

        try:
            return self._open_pgadmin_lifecycle(
                environment=env,
                instance=instance,
                cluster=cluster,
                database=database,
            )
        except PgAdminError:
            raise
        except Exception:
            raise PgAdminUnavailableError() from None

    def _require_pgadmin_environment(self, env: DevelopmentEnvironment) -> None:
        if env.state is not EnvironmentState.READY:
            raise PgAdminNotEligibleError()

    def _pgadmin_database(self, env: DevelopmentEnvironment) -> str:
        database = (
            env.target_db_name
            if env.db_mode is EnvironmentDatabaseMode.COPY
            else env.source_db_name
        )
        if database is None:
            raise PgAdminNotEligibleError()
        return database

    def _healthy_owned_compose(self, env: DevelopmentEnvironment) -> PostgresCluster:
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        try:
            cluster = PostgresCluster.from_project(Path(env.repository_root))
        except (ConfigError, PostgresClusterError):
            raise PgAdminNotEligibleError() from None
        except Exception:
            raise PgAdminUnavailableError() from None
        if cluster.mode != "compose" or not cluster.owned:
            raise PgAdminNotEligibleError()
        try:
            state = cluster.status()
        except Exception:
            raise PgAdminUnavailableError() from None
        if state is PostgresClusterState.HEALTHY:
            return cluster
        if state in {PostgresClusterState.UNKNOWN, PostgresClusterState.UNREACHABLE}:
            raise PgAdminUnavailableError()
        raise PgAdminNotEligibleError()

    def _configured_pgadmin_instance(self, env: DevelopmentEnvironment) -> OdooInstance:
        try:
            return self._client.instance.from_environment(env)
        except Exception:
            raise PgAdminUnavailableError() from None

    def _require_pgadmin_database(self, instance: OdooInstance, database: str) -> None:
        try:
            exists = instance.databases.exists(database)
        except Exception:
            raise PgAdminUnavailableError() from None
        if not exists:
            raise PgAdminDatabaseNotFoundError()

    def _open_pgadmin_lifecycle(
        self,
        *,
        environment: DevelopmentEnvironment,
        instance: OdooInstance,
        cluster: object,
        database: str,
    ) -> PgAdminOpenResult:
        """Delegate file/container mechanics to the private pgAdmin helper."""
        from odoo_instance_sdk.internal.pgadmin import open_pgadmin_lifecycle

        return open_pgadmin_lifecycle(
            environment=environment,
            instance=instance,
            cluster=cluster,
            database=database,
        )

    def list(
        self,
        *,
        project: ProjectConfig | Path | None = None,
        include_removed: bool = False,
    ) -> list[DevelopmentEnvironment]:
        catalog = self._client.get_catalog()
        if project is not None:
            if isinstance(project, ProjectConfig):
                project_path = project.repository_root
            else:
                project_path = Path(project)
            from odoo_instance_sdk.internal.git_worktree import (
                rev_parse_git_common_dir,
                rev_parse_toplevel,
            )

            repo_root = rev_parse_toplevel(project_path)
            git_common = rev_parse_git_common_dir(repo_root)
            rows = catalog.list_environments(
                git_common_dir=str(git_common), include_removed=include_removed
            )
        else:
            rows = catalog.list_environments(include_removed=include_removed)
        return [_row_to_env(r) for r in rows]

    def remove(self, selector: EnvironmentSelector) -> None:
        env = (
            self._resolve_selector(selector, include_removed=True)
            if isinstance(selector, str)
            else selector
        )
        catalog = self._client.get_catalog()
        with exclusive_lock(environment_lock_path(str(env.id))):
            self._do_remove(catalog, env)

    def _do_remove(self, catalog: object, env: DevelopmentEnvironment) -> None:
        cat = cast("BackupCatalog", catalog)
        copy_plan = self._preflight_remove(cat, env)
        cat.update_environment_state(str(env.id), EnvironmentState.REMOVING)
        cat.add_environment_event(str(env.id), "remove", "started")

        env_root = Path(env.worktree_path).parent
        repo_root = Path(env.repository_root)
        worktree = Path(env.worktree_path)
        generated_cfg = Path(env.generated_config_path)
        lock_file = Path(env.dependency_lock_path)
        venv = Path(env.python_environment_path) if env.python_environment_owned else None

        cleanup_failed = False
        failures: list[str] = []

        if copy_plan is not None and copy_plan.stage in (
            CopyJournalStage.RESTORE_PENDING,
            CopyJournalStage.RESTORED,
        ):
            cleanup_failed = self._drop_copy_target(copy_plan, failures) or cleanup_failed
            if cleanup_failed:
                # Keep the config and owned backup: they are the only durable
                # evidence and capability required for a safe retry.
                msg = "; ".join(failures)[:2000]
                cat.update_environment_state(
                    str(env.id), EnvironmentState.CLEANUP_FAILED, last_error=msg
                )
                cat.add_environment_event(str(env.id), "remove", "failed", message=msg)
                raise EnvironmentConflictError("cleanup_failed", msg)
            instance = cast("OdooInstance", copy_plan.instance)
            cat.upsert_copy_journal(
                str(env.id),
                target_database=copy_plan.target_database,
                db_host=instance.config.db_host,
                db_port=instance.config.db_port or 5432,
                db_user=instance.config.db_user,
                backup_id=str(copy_plan.backup_id) if copy_plan.backup_id is not None else None,
                stage=CopyJournalStage.DROPPED,
            )
        else:
            cat.add_environment_event(
                str(env.id), "remove", "succeeded", message="shared mode: source DB not dropped"
            )

        if copy_plan is not None and copy_plan.stage is not CopyJournalStage.BACKUP_DELETED:
            cleanup_failed = self._delete_copy_backup(copy_plan, failures) or cleanup_failed
            if cleanup_failed:
                # Keep every remaining artifact for a safe retry: deleting
                # config/worktree first would lose the cluster evidence.
                msg = "; ".join(failures)[:2000]
                cat.update_environment_state(
                    str(env.id), EnvironmentState.CLEANUP_FAILED, last_error=msg
                )
                cat.add_environment_event(str(env.id), "remove", "failed", message=msg)
                raise EnvironmentConflictError("cleanup_failed", msg)
            if not cleanup_failed:
                journal = cat.get_copy_journal(str(env.id))
                assert journal is not None
                cleanup_instance = cast("OdooInstance | None", copy_plan.instance)
                cat.upsert_copy_journal(
                    str(env.id),
                    target_database=copy_plan.target_database,
                    db_host=(
                        cleanup_instance.config.db_host
                        if cleanup_instance is not None
                        else str(journal["db_host"])
                    ),
                    db_port=(
                        cleanup_instance.config.db_port or 5432
                        if cleanup_instance is not None
                        else int(journal["db_port"])
                    ),
                    db_user=(
                        cleanup_instance.config.db_user
                        if cleanup_instance is not None
                        else (str(journal["db_user"]) if journal["db_user"] is not None else None)
                    ),
                    backup_id=str(copy_plan.backup_id) if copy_plan.backup_id is not None else None,
                    stage=CopyJournalStage.BACKUP_DELETED,
                )
        elif copy_plan is None and env.backup_id is not None:
            cleanup_failed = self._remove_backup(cat, env, failures) or cleanup_failed

        # DB and its owned backup are removed first: configuration/worktree
        # deletion must never make the cluster identity unverifiable.
        cleanup_failed = self._remove_files(generated_cfg, lock_file, failures) or cleanup_failed
        cleanup_failed = self._remove_venv(env_root, venv, failures) or cleanup_failed
        cleanup_failed = (
            self._remove_worktree(cat, env, repo_root, worktree, failures) or cleanup_failed
        )

        if cleanup_failed:
            msg = "; ".join(failures)[:2000]
            cat.update_environment_state(
                str(env.id), EnvironmentState.CLEANUP_FAILED, last_error=msg
            )
            cat.add_environment_event(str(env.id), "remove", "failed", message=msg)
            raise EnvironmentConflictError("cleanup_failed", msg)
        now = datetime.now(UTC).isoformat()
        cat.update_environment_state(str(env.id), EnvironmentState.REMOVED, removed_at=now)
        cat.add_environment_event(str(env.id), "remove", "succeeded")
        with contextlib.suppress(OSError):
            if env_root.is_dir() and not any(env_root.iterdir()):
                env_root.rmdir()

    def _preflight_remove(
        self, catalog: BackupCatalog, env: DevelopmentEnvironment
    ) -> CopyCleanupPlan | None:
        """Reject unsafe or stale catalog rows before changing any external state."""
        if not _port_free(env.http_interface, env.http_port):
            raise EnvironmentConflictError(
                "port_in_use",
                f"reserved port {env.http_interface}:{env.http_port} is occupied",
            )
        repo_root = Path(env.repository_root)
        expected_root = (
            get_environments_root() / repo_key(repo_root, Path(env.git_common_dir)) / str(env.id)
        )
        env_root = Path(env.worktree_path).parent
        if env_root.absolute() != expected_root.absolute() or _has_symlink_component(env_root):
            raise EnvironmentConflictError(
                "unsafe_environment_path", "environment root is not owned"
            )
        expected: tuple[tuple[Path, Path, Literal["file", "dir"]], ...] = (
            (Path(env.worktree_path), env_root / "worktree", "dir"),
            (Path(env.generated_config_path), env_root / "odoo.conf", "file"),
            (Path(env.dependency_lock_path), env_root / "requirements.lock", "file"),
        )
        for path, owned, kind in expected:
            _validate_owned_artifact(path, owned, kind)
        if env.python_environment_owned:
            _validate_owned_artifact(Path(env.python_environment_path), env_root / "venv", "dir")
        worktree = Path(env.worktree_path)
        if worktree.is_dir():
            from odoo_instance_sdk.internal.git_worktree import worktree_is_dirty

            if worktree_is_dirty(worktree):
                raise EnvironmentConflictError(
                    "dirty_worktree", f"worktree {worktree} is dirty; refusing to remove"
                )

        if env.db_mode == EnvironmentDatabaseMode.COPY:
            return self._preflight_copy_remove(catalog, env)
        return None

    def _preflight_copy_remove(  # noqa: C901
        self, catalog: BackupCatalog, env: DevelopmentEnvironment
    ) -> CopyCleanupPlan:
        if env.target_db_name is None:
            raise EnvironmentConflictError(
                "copy_ownership_missing", "copy environment ownership is incomplete"
            )
        config_path = Path(env.generated_config_path)
        journal = catalog.get_copy_journal(str(env.id))
        # The durable journal is authoritative after a crash; never let the
        # mere presence of a generated config downgrade a terminal stage.
        if (
            not config_path.is_file()
            and journal is not None
            and CopyJournalStage(str(journal["stage"]))
            in (
                CopyJournalStage.PREPARED,
                CopyJournalStage.BACKED_UP,
                CopyJournalStage.DROPPED,
                CopyJournalStage.BACKUP_DELETED,
            )
        ):
            stage = CopyJournalStage(str(journal["stage"]))
            backup_id = uuid.UUID(str(journal["backup_id"])) if journal["backup_id"] else None
            backup_row = catalog.get_by_id(str(backup_id)) if backup_id is not None else None
            recovery_backup = _row_to_backup(backup_row) if backup_row is not None else None
            return CopyCleanupPlan(
                target_database=str(journal["target_database"]),
                backup_id=backup_id,
                instance=None,
                backup=recovery_backup,
                stage=stage,
            )
        if not config_path.is_file():
            if journal is not None:
                stage = CopyJournalStage(str(journal["stage"]))
                backup: Backup | None = None
                backup_id = (
                    uuid.UUID(str(journal["backup_id"])) if journal["backup_id"] else env.backup_id
                )
                if stage in (CopyJournalStage.DROPPED, CopyJournalStage.BACKUP_DELETED):
                    if stage is CopyJournalStage.DROPPED:
                        if backup_id is None:
                            raise EnvironmentConflictError(
                                "copy_backup_missing", "owned backup is absent"
                            )
                        row = catalog.get_by_id(str(backup_id))
                        backup = _row_to_backup(row) if row is not None else None
                        if backup is None:
                            raise EnvironmentConflictError(
                                "copy_backup_missing", "owned backup is absent"
                            )
                    return CopyCleanupPlan(
                        target_database=str(journal["target_database"]),
                        backup_id=backup_id,
                        instance=None,
                        backup=backup,
                        stage=stage,
                    )
                # Failed before restore: the durable stage proves no target
                # database exists.  A prepared journal may legitimately have
                # no backup at all; backed_up is retryable only when its backup
                # artifact still exists.
                if stage is CopyJournalStage.PREPARED and backup_id is None:
                    return CopyCleanupPlan(
                        target_database=str(journal["target_database"]),
                        backup_id=None,
                        instance=None,
                        backup=None,
                        stage=stage,
                    )
                if stage is CopyJournalStage.BACKED_UP and backup_id is not None:
                    if backup_id is None:
                        raise EnvironmentConflictError(
                            "copy_backup_missing", "owned backup is absent"
                        )
                    row = catalog.get_by_id(str(backup_id))
                    backup = _row_to_backup(row) if row is not None else None
                    if backup is None:
                        raise EnvironmentConflictError(
                            "copy_backup_missing", "owned backup is absent"
                        )
                    return CopyCleanupPlan(
                        target_database=str(journal["target_database"]),
                        backup_id=backup_id,
                        instance=None,
                        backup=backup,
                        stage=stage,
                    )
            raise EnvironmentConflictError(
                "copy_config_missing", "copy environment config is missing"
            )
        cfg = parse_odoo_config(config_path)  # Read before any deletion.
        master_pwd = get_admin_passwd(cfg)
        if master_pwd is None:
            raise EnvironmentConflictError(
                "copy_config_invalid", "copy environment master password is missing"
            )
        instance = self._client.instance.from_config(config_path, master_password=master_pwd)
        db_port = instance.config.db_port or 5432
        if journal is not None:
            self._validate_copy_journal_ownership(env, journal, instance)
            stage = CopyJournalStage(str(journal["stage"]))
            if stage in (
                CopyJournalStage.PREPARED,
                CopyJournalStage.BACKED_UP,
                CopyJournalStage.DROPPED,
                CopyJournalStage.BACKUP_DELETED,
            ):
                journal_backup_id = journal["backup_id"]
                backup_id = uuid.UUID(str(journal_backup_id)) if journal_backup_id else None
                backup_row = catalog.get_by_id(str(backup_id)) if backup_id is not None else None
                return CopyCleanupPlan(
                    target_database=str(journal["target_database"]),
                    backup_id=backup_id,
                    instance=None,
                    backup=_row_to_backup(backup_row) if backup_row is not None else None,
                    stage=stage,
                )
            if stage in (CopyJournalStage.RESTORE_PENDING, CopyJournalStage.RESTORED):
                journal_backup_id = journal["backup_id"]
                if journal_backup_id is None:
                    raise EnvironmentConflictError(
                        "copy_backup_missing", "journal backup is absent"
                    )
                backup_row = catalog.get_by_id(str(journal_backup_id))
                backup = _row_to_backup(backup_row) if backup_row is not None else None
                if stage is CopyJournalStage.RESTORED and instance.config.db_host is not None:
                    restored = catalog.latest_restore(
                        instance.config.db_host, db_port, str(journal["target_database"])
                    )
                    if restored is None or restored.id != uuid.UUID(str(journal_backup_id)):
                        raise EnvironmentConflictError(
                            "copy_restore_mismatch",
                            "target database has no matching recorded restore",
                        )
                return CopyCleanupPlan(
                    target_database=str(journal["target_database"]),
                    backup_id=uuid.UUID(str(journal_backup_id)),
                    instance=instance,
                    backup=backup,
                    stage=stage,
                )
        restored = catalog.latest_restore(instance.config.db_host, db_port, env.target_db_name)
        if env.backup_id is None or restored is None or restored.id != env.backup_id:
            raise EnvironmentConflictError(
                "copy_restore_mismatch", "target database has no matching recorded restore"
            )
        backup_row = catalog.get_by_id(str(env.backup_id))
        if backup_row is None:
            raise EnvironmentConflictError(
                "copy_backup_missing", "owned backup is absent from catalog"
            )
        backup = _row_to_backup(backup_row)
        if backup is not None and backup.id != env.backup_id:
            raise EnvironmentConflictError(
                "copy_backup_mismatch", "owned backup metadata is invalid"
            )
        return CopyCleanupPlan(
            target_database=env.target_db_name,
            backup_id=env.backup_id,
            instance=instance,
            backup=backup,
            stage=CopyJournalStage.RESTORED,
        )

    def _validate_copy_journal_ownership(
        self, env: DevelopmentEnvironment, journal: sqlite3.Row, instance: OdooInstance
    ) -> None:
        """Fail closed unless config, environment row and durable journal agree."""
        expected_backup = str(env.backup_id) if env.backup_id is not None else None
        values_match = (
            str(journal["environment_id"]) == str(env.id)
            and str(journal["target_database"]) == str(env.target_db_name)
            and (str(journal["backup_id"]) if journal["backup_id"] is not None else None)
            == expected_backup
            and str(journal["db_host"]) == normalize_db_host(instance.config.db_host)
            and int(journal["db_port"]) == (instance.config.db_port or 5432)
            and (str(journal["db_user"]) if journal["db_user"] is not None else None)
            == instance.config.db_user
        )
        if not values_match:
            raise EnvironmentConflictError(
                "copy_cluster_mismatch",
                "copy journal, environment ownership, and generated config disagree",
            )

    def _drop_copy_target(self, plan: CopyCleanupPlan, failures: _StrList) -> bool:
        instance = cast("OdooInstance", plan.instance)
        try:
            if not instance.databases.exists(plan.target_database):
                return False
            instance.databases.drop(plan.target_database)
            if instance.databases.exists(plan.target_database):
                failures.append(f"drop postcondition failed: {plan.target_database} still exists")
                return True
        except Exception as exc:
            failures.append(f"drop: {exc}")
            return True
        return False

    def _delete_copy_backup(self, plan: CopyCleanupPlan, failures: _StrList) -> bool:
        if plan.backup is None:
            # The catalog still proves ownership, but the payload has already
            # disappeared.  Deletion is idempotent: advance the durable stage
            # rather than blocking filesystem cleanup forever.
            return False
        try:
            self._client.backups.delete(plan.backup)
        except Exception as exc:
            failures.append(f"backup delete: {exc}")
            return True
        return False

    def _remove_worktree(
        self,
        cat: object,
        env: DevelopmentEnvironment,
        repo_root: Path,
        worktree: Path,
        failures: _StrList,
    ) -> bool:

        catalog = cast("BackupCatalog", cat)
        if not worktree.is_dir():
            catalog.add_environment_event(
                str(env.id), "remove", "succeeded", message="worktree already absent"
            )
            return False
        from odoo_instance_sdk.internal.git_worktree import worktree_is_dirty, worktree_remove

        if worktree_is_dirty(worktree):
            msg = f"worktree {worktree} is dirty; refusing to remove"
            catalog.update_environment_state(
                str(env.id), EnvironmentState.CLEANUP_FAILED, last_error=msg
            )
            catalog.add_environment_event(str(env.id), "remove", "failed", message=msg)
            raise EnvironmentConflictError("dirty_worktree", msg)
        try:
            worktree_remove(repo_root, worktree)
        except Exception as e:
            failures.append(f"worktree remove: {e}")
            return True
        return False

    def _remove_files(self, generated_cfg: Path, lock_file: Path, failures: _StrList) -> bool:
        failed = False
        for p in (generated_cfg, lock_file):
            try:
                p.unlink(missing_ok=True)
            except OSError as e:
                failed = True
                failures.append(f"{p}: {e}")
        return failed

    def _remove_venv(self, env_root: Path, venv: Path | None, failures: _StrList) -> bool:
        if venv is None:
            return False
        try:
            venv_resolved = venv.resolve()
            env_root_resolved = env_root.resolve()
            try:
                venv_resolved.relative_to(env_root_resolved)
            except ValueError:
                failures.append(f"venv {venv} outside env root; skipped")
                return True
            if venv.is_dir():
                shutil.rmtree(venv, ignore_errors=False)
        except OSError as e:
            failures.append(f"venv: {e}")
            return True
        return False

    def _remove_backup(
        self,
        cat: object,
        env: DevelopmentEnvironment,
        failures: _StrList,
    ) -> bool:

        catalog = cast("BackupCatalog", cat)
        try:
            row = catalog.get_by_id(str(env.backup_id))
            if row is not None:
                backup = _row_to_backup(row)
                if backup is not None:
                    self._client.backups.delete(backup)
        except Exception as e:
            failures.append(f"backup delete: {e}")
            return True
        return False

    def _drop_target_db(
        self,
        cat: object,
        env: DevelopmentEnvironment,
        cleanup_failed: bool,
        failures: _StrList,
    ) -> bool:

        catalog = cast("BackupCatalog", cat)
        target = env.target_db_name
        if target is None:
            return cleanup_failed
        source_config = Path(env.generated_config_path)
        if not source_config.is_file():
            failures.append(f"generated config missing: {source_config}")
            return True
        cfg = parse_odoo_config(source_config)
        master_pwd = get_admin_passwd(cfg)
        if master_pwd is None:
            failures.append("master password missing; cannot drop target DB")
            return True
        base_url = infer_base_url(cfg)
        try:
            assert_local(base_url)
        except NonLocalInstanceError as e:
            failures.append(str(e))
            return True
        try:
            instance = self._client.instance.from_config(source_config, master_password=master_pwd)
            instance.databases.drop(target)
            if instance.databases.exists(target):
                failures.append(f"drop postcondition failed: {target} still exists")
                return True
            catalog.add_environment_event(
                str(env.id), "remove", "succeeded", message=f"dropped {target}"
            )
        except Exception as e:
            failures.append(f"drop: {e}")
            return True
        return cleanup_failed

    def _verify_tools(self) -> None:
        from odoo_instance_sdk.internal.proc import ProcessExecutionError, run_captured

        for executable in ("git", "uv"):
            if shutil.which(executable) is None:
                raise ConfigError(f"{executable} not found in PATH")
            try:
                result = run_captured([executable, "--version"], timeout=10.0, text=True)
            except ProcessExecutionError as error:
                raise ConfigError(f"{executable} probe failed: {error}") from error
            if result.returncode != 0:
                raise ConfigError(f"{executable} probe failed")

    def _resolve_odoo_bin(
        self, options: EnvironmentCheckoutOptions, project: ProjectConfig, repo_root: Path
    ) -> str:
        odoo_bin = options.odoo_bin or project.odoo_bin
        if odoo_bin is None:
            raise ConfigError("No odoo_bin configured; pass --odoo-bin or set project.odoo_bin")
        p = Path(odoo_bin)
        candidate = (repo_root / p).resolve() if not p.is_absolute() else p
        if not candidate.is_file() or not candidate.stat().st_mode & 0o111:
            raise InstanceConfigurationError(
                f"Odoo executable not found or not executable: {candidate}"
            )
        return str(candidate)

    def _resolve_runtime_cwd(self, project: ProjectConfig, repo_root: Path, worktree: Path) -> str:
        if project.runtime_cwd is not None:
            p = Path(project.runtime_cwd)
            if not p.is_absolute():
                resolved_repo = (repo_root / p).resolve()
                if resolved_repo.is_relative_to(repo_root.resolve()):
                    return str((worktree / p).resolve())
                return str(resolved_repo)
            return str(p)
        return str(worktree)

    def _resolve_source_config(
        self, options: EnvironmentCheckoutOptions, project: ProjectConfig, repo_root: Path
    ) -> Path | None:
        cfg = options.config_path or project.source_config
        if cfg is None:
            default = repo_root / "odoo.conf"
            return default if default.is_file() else None
        p = Path(cfg)
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        return p

    def _resolve_python_mode(
        self, options: EnvironmentCheckoutOptions, project: ProjectConfig, repo_root: Path
    ) -> _PythonMode:
        if options.create_venv:
            return _PythonMode("create", None)
        py = options.python or project.python
        if py is None:
            raise ConfigError(
                "No Python interpreter configured; pass --python or use --create-venv"
            )
        pybin = _resolve_python_bin(py, repo_root)
        if not Path(pybin).exists():
            raise InstanceConfigurationError(
                f"Python interpreter not found: {pybin}; use --create-venv to create one"
            )
        if not _is_venv(pybin):
            raise InstanceConfigurationError(
                f"Python interpreter {pybin} is not a virtual-env; use --create-venv"
            )
        return _PythonMode("reuse", pybin)

    def _resolve_dbs(
        self,
        options: EnvironmentCheckoutOptions,
        project: ProjectConfig,
        cfg: dict[str, str],
        db_mode: str,
        branch: str,
        repo_root: Path,
    ) -> tuple[str | None, str | None]:
        if db_mode == EnvironmentDatabaseMode.SHARED:
            source = (
                options.source_database or project.default_source_database or _infer_single_db(cfg)
            )
            if source is None:
                raise ConfigError(
                    "Could not infer source DB from odoo.conf (multiple or empty db_name); pass --source-db"
                )
            return source, None
        source = options.source_database or project.default_source_database or _infer_single_db(cfg)
        if source is None:
            raise ConfigError("copy mode requires --source-db or exactly one db_name in odoo.conf")
        target = options.target_database
        if target is None:
            target = self._default_target_db(source, branch)
        validate_db_name(target)
        data_dir = cfg.get("data_dir")
        if data_dir:
            validate_filestore_containment(Path(data_dir), target)
        return source, target

    def _default_target_db(self, source: str, branch: str) -> str:
        slug = _SLUG_RE.sub("_", branch).strip("._-") or "branch"
        h = hashlib.sha256(branch.encode("utf-8")).hexdigest()[:8]
        name = f"{source}_{slug}_{h}"
        if len(name.encode("utf-8")) > 63:
            name = f"{source}_{h}"
        return name

    def _allocate_port(
        self,
        requested: int | None,
        project: ProjectConfig,
        catalog: object | None,
        http_interface: str,
        exclude_project: Path | None = None,
    ) -> int:
        cat = cast("BackupCatalog | None", catalog)
        return find_free_port(
            "http",
            cat,
            requested=requested,
            project=project,
            host=http_interface,
            exclude_project=exclude_project,
        )

    def _resolve_selector(
        self, selector: str, *, include_removed: bool = False
    ) -> DevelopmentEnvironment:
        catalog = self._client.get_catalog()
        row = catalog.get_environment(selector)
        if row is not None:
            return _row_to_env(row)
        rows = catalog.list_environments(include_removed=True)
        by_name = [r for r in rows if r["name"] == selector]
        if len(by_name) > 1:
            raise EnvironmentResolutionError(
                f"Ambiguous environment selector {selector!r}",
                candidates=[str(r["id"]) for r in by_name],
            )
        if len(by_name) == 1:
            return _row_to_env(by_name[0])
        raise EnvironmentNotFoundError(selector)


def _encode_runtime_json(odoo_bin: str, runtime_cwd: str) -> str:
    import json

    return json.dumps({"odoo_bin": odoo_bin, "runtime_cwd": runtime_cwd})


def _decode_runtime_json(raw: str | None) -> dict[str, str]:
    import json

    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items() if isinstance(v, str)}


def _http_fields_from_generated_config(generated_config_path: str) -> tuple[str, int]:
    """Read http_interface/http_port from the generated odoo.conf (single source of truth)."""
    try:
        cfg = parse_odoo_config(generated_config_path)
    except Exception:
        return "127.0.0.1", 8069
    http_interface = cfg.get("http_interface") or "127.0.0.1"
    http_port_raw = cfg.get("http_port", "8069")
    try:
        http_port = int(http_port_raw)
    except ValueError:
        http_port = 8069
    return http_interface, http_port


def _row_to_env(row: object) -> DevelopmentEnvironment:
    def _get(key: str) -> object:
        r = cast("sqlite3.Row", row)
        return r[key]

    def _opt(key: str) -> str | None:
        r = cast("sqlite3.Row", row)
        try:
            v: object = r[key]
        except (KeyError, IndexError):
            return None
        if v is None:
            return None
        return str(v)

    backup_raw: object = None
    with contextlib.suppress(KeyError, IndexError):
        backup_raw = cast("sqlite3.Row", row)["backup_id"]
    http_interface, http_port = _http_fields_from_generated_config(
        str(_get("generated_config_path"))
    )

    return DevelopmentEnvironment(
        id=uuid.UUID(str(_get("id"))),
        name=str(_get("name")),
        repository_root=str(_get("repository_root")),
        git_common_dir=str(_get("git_common_dir")),
        branch=str(_get("branch")),
        base_ref=str(_get("base_ref")),
        worktree_path=str(_get("worktree_path")),
        generated_config_path=str(_get("generated_config_path")),
        python_environment_path=str(_get("python_environment_path")),
        python_environment_owned=bool(_get("python_environment_owned")),
        dependency_lock_path=str(_get("dependency_lock_path")),
        http_interface=http_interface,
        http_port=http_port,
        db_mode=EnvironmentDatabaseMode(str(_get("db_mode"))),
        source_db_name=_opt("source_db_name"),
        target_db_name=_opt("target_db_name"),
        backup_id=uuid.UUID(str(backup_raw)) if backup_raw is not None else None,
        state=EnvironmentState(str(_get("state"))),
        created_at=datetime.fromisoformat(str(_get("created_at"))),
        last_used_at=datetime.fromisoformat(str(_get("last_used_at")))
        if _opt("last_used_at")
        else None,
        removed_at=datetime.fromisoformat(str(_get("removed_at"))) if _opt("removed_at") else None,
        last_error=_opt("last_error"),
    )


def _row_to_backup(row: object) -> Backup | None:
    r = cast("sqlite3.Row", row)
    try:
        path: object = r["path"]
    except (KeyError, IndexError):
        return None
    if path is None or not Path(str(path)).is_file():
        return None
    size_raw: object = None
    with contextlib.suppress(KeyError, IndexError):
        size_raw = r["size_bytes"]
    return Backup(
        id=uuid.UUID(str(r["id"])),
        source_base_url=str(r["source_base_url"]),
        database_name=str(r["database_name"]),
        format=BackupFormat(str(r["format"])),
        filestore_requested=bool(r["filestore_requested"]),
        path=str(path),
        filename=str(r["filename"]) if r["filename"] else "",
        size_bytes=int(str(size_raw)) if size_raw is not None else 0,
        sha256=str(r["sha256"]) if r["sha256"] else "",
        downloaded_at=datetime.fromisoformat(str(r["downloaded_at"])),
    )


def _infer_single_db(cfg: dict[str, str]) -> str | None:
    names = parse_db_names(cfg.get("db_name"))
    if len(names) == 1:
        return names[0]
    return None


def _resolve_python_bin(py: str | Path, repo_root: Path) -> str:
    s = str(py)
    p = Path(s)
    if not p.is_absolute():
        candidate = shutil.which(s)
        if candidate:
            return candidate
        p = (repo_root / p).resolve()
    return str(p)


def _is_venv(pybin: str) -> bool:
    try:
        from odoo_instance_sdk.internal.proc import ProcessExecutionError, run_captured

        proc = run_captured(
            [pybin, "-c", "import sys; print(sys.prefix != sys.base_prefix)"],
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return False
        return isinstance(proc.stdout, str) and proc.stdout.strip().lower() == "true"
    except (OSError, subprocess.TimeoutExpired, ProcessExecutionError):
        return False


def _port_free(host: str, port: int) -> bool:
    return probe_address(host, port) is AddressState.FREE


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _validate_owned_artifact(path: Path, expected: Path, kind: Literal["file", "dir"]) -> None:
    if path.absolute() != expected.absolute():
        raise EnvironmentConflictError("unsafe_environment_path", f"unexpected {kind} path: {path}")
    if _has_symlink_component(path):
        raise EnvironmentConflictError("unsafe_environment_path", f"symlinked {kind} path: {path}")
    if not path.exists():
        return
    valid = path.is_file() if kind == "file" else path.is_dir()
    if not valid:
        raise EnvironmentConflictError("unsafe_environment_path", f"unexpected {kind} type: {path}")


class _CompileFailed(Exception):
    pass


def _restore_audit_backup(
    client: OdooClient,
    config: Mapping[str, str],
    database: str | None,
    *,
    available: bool,
) -> Backup | None:
    """Read restore provenance, using an existing catalog or SQLite read-only mode."""
    if database is None:
        return None
    host = config.get("db_host")
    try:
        port = int(config.get("db_port", "5432"))
    except ValueError:
        port = 5432
    catalog = getattr(client, "_catalog", None)
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    if isinstance(catalog, BackupCatalog):
        if available:
            return catalog.latest_restore(host, port, database)
        return catalog.latest_restore_provenance(host, port, database)

    from odoo_instance_sdk.internal.paths import get_catalog_path

    path = get_catalog_path()
    if not path.is_file():
        return None
    return _restore_audit_backup_from_sqlite(path, config, database, available=available)


def _restore_audit_backup_from_sqlite(
    path: Path,
    config: Mapping[str, str],
    database: str,
    *,
    available: bool,
) -> Backup | None:
    host = config.get("db_host")
    try:
        port = int(config.get("db_port", "5432"))
    except ValueError:
        port = 5432
    query = (
        "SELECT b.* FROM restores r INNER JOIN backups b ON b.id = r.backup_id "
        "WHERE r.db_host=? AND r.db_port=? AND r.database_name=?"
    )
    if available:
        query += " AND b.state='available' AND b.path IS NOT NULL"
    query += " ORDER BY r.restored_at DESC, r.sequence DESC LIMIT 1"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(query, (normalize_db_host(host), port, database)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            conn.close()
    if row is None:
        return None
    downloaded_at = row["downloaded_at"] or row["started_at"]
    if downloaded_at is None:
        return None
    return Backup(
        id=uuid.UUID(str(row["id"])),
        source_base_url=str(row["source_base_url"]),
        database_name=str(row["database_name"]),
        format=BackupFormat(str(row["format"])),
        filestore_requested=bool(row["filestore_requested"]),
        path=str(row["path"] or ""),
        filename=str(row["filename"] or ""),
        size_bytes=int(row["size_bytes"] or 0),
        sha256=str(row["sha256"] or ""),
        downloaded_at=datetime.fromisoformat(str(downloaded_at)),
        source_git_branch=(
            str(row["source_git_branch"]) if row["source_git_branch"] is not None else None
        ),
    )


def _checkout_steps(plan: _CheckoutPlan) -> tuple[Step, ...]:
    """Return the private steps that are projected and consumed by checkout."""
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep

    steps: list[Step] = [
        PreparedAction("checkout.catalog"),
        PreparedStep(
            step_id="checkout.worktree",
            argv=plan.worktree_argv,
            cwd=str(plan.repo_root),
            mode="captured",
            mutating=True,
        ),
    ]
    if plan.source_config is not None:
        steps.append(PreparedAction("checkout.generated_config"))
    if plan.options.create_venv and plan.python_selector is not None:
        steps.append(
            PreparedStep(
                step_id="checkout.venv",
                argv=("uv", "venv", str(plan.venv), "--python", str(plan.python_selector)),
                cwd=str(plan.repo_root),
                mode="captured",
                mutating=True,
            )
        )
    if plan.dependency_inputs:
        steps.append(
            PreparedStep(
                step_id="checkout.dependencies.compile",
                argv=(
                    "uv",
                    "pip",
                    "compile",
                    *plan.dependency_inputs,
                    "-o",
                    str(plan.dependency_lock),
                ),
                cwd=str(plan.worktree),
                mode="captured",
                mutating=True,
            )
        )
        install_argv = (
            (
                "uv",
                "pip",
                "sync",
                "--python",
                str(Path(plan.python_path) / "bin" / "python"),
                str(plan.dependency_lock),
            )
            if plan.python_owned
            else (
                "uv",
                "pip",
                "install",
                "--python",
                plan.python_path,
                "-r",
                str(plan.dependency_lock),
            )
        )
        steps.append(
            PreparedStep(
                step_id="checkout.dependencies.install",
                argv=install_argv,
                cwd=str(plan.worktree),
                mode="captured",
                mutating=True,
            )
        )
    steps.extend(
        (
            PreparedAction("checkout.database"),
            PreparedAction("checkout.cleanup"),
        )
    )
    return tuple(steps)


def _planning_result(
    expression_api: _ExpressionApi, outcome: _PlanningOutcome
) -> _ExpressionResult:
    """Adapt one concrete pure stage outcome to the bounded Result type."""
    if outcome.error is not None:
        return expression_api.Error(outcome.error)
    if outcome.state is None:
        return expression_api.Error(PlanValidationError("checkout stage produced no state"))
    return expression_api.Ok(outcome.state)


def _planning_error_outcome(error: _PlanningError) -> _PlanningOutcome:
    """Keep an expected planning failure typed while leaving the Result boundary."""
    return _PlanningOutcome(error=error)


def _validate_checkout_stage(state: _CheckoutPlanningState) -> _PlanningOutcome:
    """Validate captured checkout invariants without touching external state."""
    plan = state.private
    if not plan.branch.strip():
        return _PlanningOutcome(error=PlanValidationError("checkout branch must not be empty"))
    if not plan.worktree_argv:
        return _PlanningOutcome(error=PlanValidationError("checkout planning produced no command"))
    if plan.db_mode is EnvironmentDatabaseMode.COPY and plan.target_database is None:
        return _PlanningOutcome(
            error=PlanValidationError("copy checkout requires a target database")
        )
    return _PlanningOutcome(state=state)


def _normalize_checkout_stage(state: _CheckoutPlanningState) -> _PlanningOutcome:
    """Build immutable public projections from already captured values."""
    public = _public_checkout_plan(state.private, state.provenance, state.freshness, state.warnings)
    execution_plan = _execution_plan(
        state.private, state.provenance, state.freshness, state.warnings
    )
    return _PlanningOutcome(state=replace(state, public=public, execution_plan=execution_plan))


def _capture_checkout_stage(state: _CheckoutPlanningState) -> _PlanningOutcome:
    """Capture the final private/public pair without adding effects or locks."""
    if state.public is None or state.execution_plan is None:
        return _PlanningOutcome(error=PlanValidationError("checkout projections are incomplete"))
    snapshot = _CheckoutSnapshot(
        private=state.private,
        public=state.public,
        execution_plan=state.execution_plan,
    )
    return _PlanningOutcome(state=replace(state, snapshot=snapshot))


def _process_stderr(result: ProcessResult) -> str:
    stderr = result.stderr
    if isinstance(stderr, bytes):
        return stderr.decode(errors="replace")
    return stderr or ""


def _execution_plan(
    plan: _CheckoutPlan,
    provenance: BackupProvenanceComparison,
    freshness: BackupFreshness,
    warnings: tuple[str, ...],
) -> ExecutionPlan:
    """Build the public process/action projection from one private snapshot."""
    from odoo_instance_sdk.execution import ActionStep, ExecutionPlan, ExecutionStep
    from odoo_instance_sdk.internal.proc import PreparedStep

    probe_argvs = (
        ("git", "-C", str(plan.repo_root), "rev-parse", "--show-toplevel"),
        ("git", "-C", str(plan.repo_root), "rev-parse", "--git-common-dir"),
        ("git", "-C", str(plan.repo_root), "rev-parse", "--verify", plan.base_ref),
        ("git", "-C", str(plan.repo_root), "rev-parse", "--verify", f"refs/heads/{plan.branch}"),
        ("git", "-C", str(plan.repo_root), "ls-remote", "--heads", "origin", plan.branch),
        ("git", "--version"),
        ("uv", "--version"),
    )
    observations: list[JsonValue] = []
    for argv in probe_argvs:
        observations.append(
            cast(
                "JsonValue",
                {
                    "argv": list(argv),
                    "returncode": 0,
                    "read_only": True,
                    "executed_during_planning": True,
                },
            )
        )

    steps: list[ExecutionStep] = []
    for step in _checkout_steps(plan):
        if isinstance(step, PreparedStep):
            steps.append(step.public_projection())
        else:
            action_details: JsonValue = None
            action = step.step_id.removeprefix("checkout.")
            description = "Execute checkout action"
            if action == "catalog":
                action = "record_environment"
                description = "Record the environment in the catalog"
                action_details = {"environment_id": str(plan.env_id)}
            elif action == "generated_config":
                action = "write_generated_config"
                description = "Generate the checkout Odoo configuration"
                action_details = {"path": str(plan.generated_config)}
            elif action == "database":
                action = "prepare_database"
                description = "Prepare the selected checkout database"
                action_details = {
                    "mode": plan.db_mode.value,
                    "database": plan.target_database or plan.source_database,
                }
            elif action == "cleanup":
                action = "cleanup_on_failure"
                description = "Remove owned checkout artifacts if execution fails"
                action_details = {"root": str(plan.env_root)}
            steps.append(
                ActionStep(
                    step_id=step.step_id,
                    action=action,
                    description=description,
                    details=action_details,
                    mutating=True,
                )
            )
    execution = ExecutionPlan(
        steps=tuple(steps),
        observations=tuple(observations),
        warnings=warnings,
    )
    return execution.with_fingerprint(secrets=tuple(plan.config_values.values()))


def _public_checkout_plan(
    plan: _CheckoutPlan,
    provenance: BackupProvenanceComparison,
    freshness: BackupFreshness,
    warnings: tuple[str, ...],
) -> EnvironmentCheckoutPlan:
    actions: tuple[DatabasePreparationAction, ...] = ()
    if plan.project.refresh_after_hours is not None and freshness is not BackupFreshness.FRESH:
        actions = (
            DatabasePreparationAction.DOWNLOAD,
            DatabasePreparationAction.RESTORE,
            DatabasePreparationAction.SWITCH_DEFAULT,
        )
    return EnvironmentCheckoutPlan(
        name=plan.name,
        branch=plan.branch,
        effective_base_ref=plan.base_ref,
        db_mode=plan.db_mode,
        source_database=plan.source_database,
        target_database=plan.target_database,
        python_mode=(
            EnvironmentPythonMode.CREATE if plan.python_owned else EnvironmentPythonMode.REUSE
        ),
        provenance=provenance,
        freshness=freshness,
        preparation_actions=actions,
        warnings=warnings,
    )


def _load_project(env: DevelopmentEnvironment) -> ProjectConfig:
    return ProjectConfig.load(Path(env.repository_root))


def _rebase_requirement_paths(paths: list[str], repo_root: Path, worktree: Path) -> list[str]:
    rebased: list[str] = []
    for p in paths:
        candidate = Path(p)
        if candidate.is_absolute():
            rebased.append(str(candidate))
            continue
        resolved_repo = (repo_root / candidate).resolve()
        resolved_work = (worktree / candidate).resolve()
        if resolved_repo.is_relative_to(repo_root.resolve()):
            rebased.append(str(resolved_work))
        else:
            rebased.append(str(candidate))
    return rebased


def _find_odoo_requirements(worktree: Path) -> Path | None:
    for candidate in (worktree / "requirements.txt", worktree / "odoo" / "requirements.txt"):
        if candidate.is_file():
            return candidate
    return None
