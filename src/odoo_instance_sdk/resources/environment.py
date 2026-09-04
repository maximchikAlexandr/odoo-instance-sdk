from __future__ import annotations

import contextlib
import hashlib
import importlib
import re
import shutil
import sqlite3
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar, Union, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    DatabaseAlreadyExistsError,
    EnvironmentConflictError,
    EnvironmentNotFoundError,
    EnvironmentResolutionError,
    InstanceConfigurationError,
    MasterPasswordRequiredError,
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
from odoo_instance_sdk.internal.pgadmin import PgAdminPhaseHandle
from odoo_instance_sdk.internal.port_allocation import find_free_port
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
    from odoo_instance_sdk.internal.pgadmin import _PgAdminReconciliationCarrier
    from odoo_instance_sdk.internal.pgadmin_files import (
        PgAdminFingerprintInputs,
        PgAdminPaths,
        PostgresIdentity,
    )
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        ProcessResult,
        RunContext,
        Step,
    )
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, CatalogValue

EnvironmentSelector = Union[str, "DevelopmentEnvironment"]
type _PlanningError = PlanError | ConfigError | EnvironmentConflictError
T = TypeVar("T")

# Re-export the dependency-neutral contract for backwards-compatible imports.
EnvironmentState = _EnvironmentState
DevelopmentEnvironment = _DevelopmentEnvironment
EnvironmentDatabaseMode = _EnvironmentDatabaseMode
type _EnvironmentList = list[DevelopmentEnvironment]

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PGADMIN_LIFECYCLE_TIMEOUT = 60.0


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
    instance: OdooInstance | None
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
class _PgAdminCommandInputs:
    """Private values needed by the locked pgAdmin provisioning phase."""

    identity: PostgresIdentity
    paths: PgAdminPaths
    port: int
    password: str
    database: str
    database_probe: PreparedStep | None = None
    fingerprint_inputs: PgAdminFingerprintInputs | None = None


@dataclass(frozen=True, slots=True)
class _PlanningOutcome:
    state: _CheckoutPlanningState | None = None
    error: _PlanningError | None = None


def _checkout_public_plan(command: Command[DevelopmentEnvironment]) -> EnvironmentCheckoutPlan:
    """Read the domain projection captured inside one private command."""
    projection = command._private_projection()
    if not isinstance(projection, EnvironmentCheckoutPlan):
        raise PlanError("checkout command has no captured domain plan")
    return projection


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
        return self.refresh_database_command(project, options=options).run()

    def refresh_database_command(
        self,
        project: ProjectConfig | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabasePreparationResult]:
        from odoo_instance_sdk.internal.database_preparation import (
            DatabasePreparationCoordinator,
        )

        return DatabasePreparationCoordinator(self._client).refresh_database_command(
            project,
            options=options,
            executor=executor,
        )

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

    def _command_from_snapshot(
        self,
        snapshot: _CheckoutSnapshot,
        *,
        executor: ProcessExecutor | None = None,
    ) -> Command[DevelopmentEnvironment]:
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import (
            SubprocessExecutor,
            prepared_command,
        )

        def run(context: RunContext[DevelopmentEnvironment]) -> DevelopmentEnvironment:
            return self._run_checkout_snapshot(context, snapshot)

        prepared = prepared_command(
            run,
            _checkout_steps(snapshot.private),
            executor=executor or SubprocessExecutor(),
            private_projection=snapshot.public,
        )
        return Command.from_prepared(snapshot.execution_plan, prepared)

    def _run_checkout_snapshot(
        self, context: RunContext[DevelopmentEnvironment], snapshot: _CheckoutSnapshot
    ) -> DevelopmentEnvironment:
        plan = snapshot.private
        with exclusive_lock(provisioning_lock_path()):
            self._validate_checkout_snapshot(snapshot, context=context)
            if plan.db_mode is EnvironmentDatabaseMode.COPY:
                self._preflight_copy_checkout(plan)
            context.action("checkout.catalog")
            catalog = self._client.get_catalog()
            self._revalidate_checkout_locked(catalog, plan)
            return self._do_checkout(catalog, plan, context=context)

    def _validate_checkout_snapshot(
        self,
        snapshot: _CheckoutSnapshot,
        *,
        context: RunContext[DevelopmentEnvironment] | None = None,
    ) -> None:
        """Reject changed read-only inputs before the catalog or artifacts mutate."""
        plan = snapshot.private
        if (
            plan.project.refresh_after_hours is not None
            and snapshot.public.freshness is not BackupFreshness.FRESH
        ):
            raise StalePlanError(
                "checkout database backup is not fresh; run the refresh database command "
                "as a separate phase before checkout"
            )
        from odoo_instance_sdk.internal.git_worktree import (
            rev_parse_git_common_dir,
            rev_parse_toplevel,
            rev_parse_verify,
        )

        if context is None:
            actual_identity = (
                str(rev_parse_toplevel(plan.repo_root)),
                str(rev_parse_git_common_dir(plan.repo_root)),
                rev_parse_verify(plan.repo_root, plan.base_ref),
            )
        else:

            def output(step_id: str) -> str:
                result = cast("ProcessResult", context.process(step_id))
                value = result.stdout
                return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")

            top_dir = Path(output("checkout.validate.git.toplevel").strip()).resolve()
            common_dir = Path(output("checkout.validate.git.common-dir").strip())
            if not common_dir.is_absolute():
                # Match ``rev_parse_git_common_dir``'s normalization used
                # while capturing the plan. Git reports this path relative
                # to the directory supplied through ``git -C``.
                common_dir = plan.repo_root / common_dir
            actual_identity = (
                str(top_dir),
                str(common_dir.resolve()),
                output("checkout.validate.git.base").strip(),
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
        return self._command_from_snapshot(snapshot)

    def checkout_with_plan(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> EnvironmentCheckoutResult:
        """Execute checkout and return its final secret-free typed plan."""
        command = self.checkout_command(project, branch, options=options)
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
        return self.checkout_command(project, branch, options=options).run()

    def _revalidate_checkout_locked(self, catalog: BackupCatalog, plan: _CheckoutPlan) -> None:
        cat = catalog
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
        catalog: BackupCatalog,
        plan: _CheckoutPlan,
        *,
        context: RunContext[DevelopmentEnvironment],
    ) -> DevelopmentEnvironment:
        runtime_json = _encode_runtime_json(plan.odoo_bin, plan.runtime_cwd)
        env_row: dict[str, CatalogValue] = {
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

        cat = catalog
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
            if context.planned("checkout.cleanup.worktree"):
                context.skip("checkout.cleanup.worktree")
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
                context=context,
            )
            raise

    def _get_env_row(self, cat: BackupCatalog, env_id: uuid.UUID) -> DevelopmentEnvironment:

        catalog = cat
        row = catalog.get_environment(str(env_id))
        if row is None:
            raise RuntimeError("environment row disappeared after checkout")
        return _row_to_env(row)

    def _do_copy_restore(
        self,
        *,
        cat: BackupCatalog,
        env_id: uuid.UUID,
        source_config: Path | None,
        cfg_dict: Mapping[str, str],
        source_db: str,
        target_db: str,
    ) -> uuid.UUID:

        catalog = cat
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
        cat: BackupCatalog,
        env_id: uuid.UUID,
        repo_root: Path,
        created_paths: list[Path],
        env_root: Path,
        backup_id: uuid.UUID | None,
        error: BaseException,
        context: RunContext[DevelopmentEnvironment],
    ) -> None:

        catalog = cat
        if backup_id is None:
            row = catalog.get_environment(str(env_id))
            if row is not None and row["backup_id"] is not None:
                backup_id = uuid.UUID(str(row["backup_id"]))
        cleanup_failed = self._rollback_copy_checkout(catalog, env_id, backup_id)
        # A restored copy must remain diagnosable when compensation cannot prove
        # that the target database is gone.  In particular, do not delete the
        # generated config (the cluster identity) or its owned backup first.
        if not cleanup_failed:
            cleanup_failed = self._cleanup_created_paths(repo_root, created_paths, context=context)

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

    def _cleanup_created_paths(
        self,
        repo_root: Path,
        created_paths: list[Path],
        *,
        context: RunContext[DevelopmentEnvironment] | None = None,
    ) -> bool:
        cleanup_failed = False
        for p in created_paths:
            if p.name == "worktree":
                from odoo_instance_sdk.internal.git_worktree import worktree_remove

                try:
                    if context is None:
                        worktree_remove(repo_root, p)
                    else:
                        result = cast("ProcessResult", context.process("checkout.cleanup.worktree"))
                        if result.returncode != 0:
                            cleanup_failed = True
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

    def _cleanup_backup(self, catalog: BackupCatalog, backup_id: uuid.UUID) -> bool:

        cat = catalog
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
        return self.sync_python_command(selector, upgrade=upgrade).run()

    def sync_python_command(
        self,
        selector: EnvironmentSelector,
        *,
        upgrade: bool = False,
    ) -> Command[DevelopmentEnvironment]:
        """Capture one immutable uv synchronization operation."""
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            PreparedStep,
            SubprocessExecutor,
            prepared_command,
        )

        env = (
            self._resolve_selector(selector, include_removed=False)
            if isinstance(selector, str)
            else selector
        )
        project = _load_project(env)
        worktree = Path(env.worktree_path)
        repo_root = Path(env.repository_root)
        inputs = _rebase_requirement_paths(list(project.requirements), repo_root, worktree)
        odoo_req = _find_odoo_requirements(worktree)
        if odoo_req is not None and str(odoo_req) not in inputs:
            inputs.append(str(odoo_req))
        steps: list[Step] = []
        if inputs:
            compile_argv = ["uv", "pip", "compile", *inputs]
            if upgrade:
                compile_argv.append("--upgrade")
            compile_argv.extend(("-o", env.dependency_lock_path))
            steps.append(
                PreparedStep(
                    step_id="environment.sync.compile",
                    argv=tuple(compile_argv),
                    cwd=str(worktree),
                    mutating=True,
                )
            )
            if env.python_environment_owned:
                install_argv: tuple[str, ...] = (
                    "uv",
                    "pip",
                    "sync",
                    "--python",
                    str(Path(env.python_environment_path) / "bin" / "python"),
                    env.dependency_lock_path,
                )
            else:
                install_argv = (
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    env.python_environment_path,
                    "-r",
                    env.dependency_lock_path,
                )
            steps.append(
                PreparedStep(
                    step_id="environment.sync.install",
                    argv=install_argv,
                    cwd=str(worktree),
                    mutating=True,
                )
            )
        steps.append(
            PreparedAction(
                step_id="environment.sync",
                action="sync_python",
                description="Record Python dependency synchronization",
                mutating=bool(inputs),
            )
        )
        prepared_steps = tuple(steps)

        def execute(context: RunContext[DevelopmentEnvironment]) -> DevelopmentEnvironment:
            catalog = self._client.get_catalog()
            catalog.add_environment_event(str(env.id), "sync", "started")
            try:
                with (
                    exclusive_lock(environment_lock_path(str(env.id))),
                    exclusive_lock(python_env_lock_path(env.python_environment_path)),
                ):
                    if inputs:
                        compile_result = cast(
                            "ProcessResult", context.process("environment.sync.compile")
                        )
                        if compile_result.returncode != 0:
                            context.skip("environment.sync.install")
                            if not Path(env.dependency_lock_path).is_file():
                                raise ConfigError(
                                    "uv pip compile failed and no prior lock: "
                                    f"{_process_stderr(compile_result)}"
                                )
                            catalog.add_environment_event(
                                str(env.id),
                                "sync",
                                "failed",
                                message="uv pip compile failed; kept existing lock",
                            )
                            return self._get_env_row(catalog, env.id)
                        install_result = cast(
                            "ProcessResult", context.process("environment.sync.install")
                        )
                        if install_result.returncode != 0:
                            raise ConfigError(
                                f"uv pip install failed: {_process_stderr(install_result)}".strip()
                            )
                    catalog.add_environment_event(str(env.id), "sync", "succeeded")
                    return self._get_env_row(catalog, env.id)
            finally:
                context.action("environment.sync")

        from odoo_instance_sdk.execution import ExecutionPlan

        return Command.from_prepared(
            ExecutionPlan(
                steps=tuple(step.public_projection() for step in prepared_steps)
            ).with_fingerprint(),
            prepared_command(
                execute,
                prepared_steps,
                executor=SubprocessExecutor(),
            ),
        )

    def record_use(self, env: DevelopmentEnvironment) -> None:
        catalog = self._client.get_catalog()
        now = datetime.now(UTC).isoformat()
        catalog.record_environment_use(str(env.id), now)

    def get(self, selector: EnvironmentSelector) -> DevelopmentEnvironment:
        if isinstance(selector, DevelopmentEnvironment):
            return selector
        return self._resolve_selector(selector, include_removed=True)

    def open_pgadmin(self, selector: EnvironmentSelector) -> PgAdminOpenResult:
        return self.open_pgadmin_command(selector).run()

    def open_pgadmin_command(
        self,
        selector: EnvironmentSelector,
        *,
        executor: ProcessExecutor | None = None,
    ) -> Command[PgAdminOpenResult]:
        captured_selector: EnvironmentSelector = selector
        if isinstance(selector, str):
            try:
                captured_selector = self.get(selector)
            except Exception:
                # Preserve the existing typed selector error in the callback.
                captured_selector = selector
        planned_cluster = _pgadmin_cluster_snapshot(captured_selector)
        captured_inputs = self._capture_pgadmin_command_inputs(captured_selector, planned_cluster)
        steps = _pgadmin_command_steps(
            captured_selector,
            cluster=planned_cluster,
            inputs=captured_inputs,
            include_reconciliation=True,
        )

        def open_pgadmin() -> PgAdminOpenResult:
            from odoo_instance_sdk.internal import pgadmin_files

            try:
                paths = (
                    captured_inputs.paths
                    if captured_inputs is not None
                    else pgadmin_files.PgAdminPaths.from_defaults()
                )
                # The full finite operation owns one lock-atomic lifecycle:
                # no other first-run command can observe a key write before
                # this command has reconciled its container.
                with pgadmin_files.pgadmin_lock(
                    path=paths.lock,
                    timeout=_PGADMIN_LIFECYCLE_TIMEOUT,
                ):
                    carrier = self._open_pgadmin_impl(
                        captured_selector,
                        cluster=planned_cluster,
                        captured_inputs=captured_inputs,
                        lock_held=True,
                    )
                    from odoo_instance_sdk.internal.proc import active_context

                    context = cast("RunContext[PgAdminOpenResult] | None", active_context())
                    if context is None:
                        raise PgAdminUnavailableError()
                    return carrier.reconcile(context, lock_held=True)
            finally:
                _skip_planned_pgadmin_database_probe()

        return self._action_command(
            "environment.open-pgadmin",
            "Open the environment's owned pgAdmin container",
            open_pgadmin,
            executor=executor,
            mutating=True,
            steps=steps,
        )

    def open_pgadmin_phase(self, selector: EnvironmentSelector) -> PgAdminPhaseHandle:
        """Run only provisioning and return the explicit reconciliation phase."""
        return self.open_pgadmin_phase_command(selector).run()

    def open_pgadmin_phase_command(
        self,
        selector: EnvironmentSelector,
        *,
        executor: ProcessExecutor | None = None,
    ) -> Command[PgAdminPhaseHandle]:
        """Capture the finite provisioning phase without hiding reconciliation."""
        captured_selector: EnvironmentSelector = selector
        if isinstance(selector, str):
            try:
                captured_selector = self.get(selector)
            except Exception:
                captured_selector = selector
        planned_cluster = _pgadmin_cluster_snapshot(captured_selector)
        captured_inputs = self._capture_pgadmin_command_inputs(captured_selector, planned_cluster)
        steps = _pgadmin_command_steps(
            captured_selector,
            cluster=planned_cluster,
            inputs=captured_inputs,
        )

        def provision() -> PgAdminPhaseHandle:
            try:
                carrier = self._open_pgadmin_impl(
                    captured_selector,
                    cluster=planned_cluster,
                    captured_inputs=captured_inputs,
                )
                return PgAdminPhaseHandle(reconciliation=carrier.reconciliation_command())
            finally:
                _skip_planned_pgadmin_database_probe()

        return self._action_command(
            "environment.open-pgadmin-phase",
            "Provision pgAdmin and return its explicit reconciliation command",
            provision,
            executor=executor,
            mutating=True,
            steps=steps,
        )

    def _capture_pgadmin_command_inputs(
        self,
        selector: EnvironmentSelector,
        cluster: PostgresCluster | None,
    ) -> _PgAdminCommandInputs | None:
        """Capture every immutable input needed by the pgAdmin command.

        Docker identity is derived from the managed Compose project name, not
        from a runtime container id.  This keeps the later inspect/run argv
        stable while the identity probe still validates the live object.
        """
        from odoo_instance_sdk.resources.postgres import PostgresCluster as _PostgresCluster

        if (
            not isinstance(cluster, _PostgresCluster)
            or cluster.mode != "compose"
            or not cluster.owned
        ):
            return None
        try:
            env = self.get(selector)
            self._require_pgadmin_environment(env)
            database = self._pgadmin_database(env)
            instance = self._configured_pgadmin_instance(env)
            password = instance.config.db_password or ""
            if not isinstance(password, str):
                return None
            from odoo_instance_sdk.internal.pgadmin_files import (
                PgAdminPaths,
                PostgresIdentity,
                execution_fingerprint_inputs,
                select_port,
            )

            paths = PgAdminPaths.from_defaults()
            identity = PostgresIdentity(
                container_name=f"{cluster.compose_project_name}-postgres-1",
                network=f"{cluster.compose_project_name}_default",
                user=cluster._user or "odoo",
                host=f"{cluster.compose_project_name}-postgres-1",
                # This identity is used from pgAdmin's Docker network.  The
                # Compose endpoint is the host-published port and is not
                # reachable as the container-side PostgreSQL service port.
                port=5432,
            )
            database_probe = instance.databases._psql_probe_for(
                database, "pgadmin.database.exists.psql"
            )
            fingerprint_inputs = execution_fingerprint_inputs(paths, identity, database, password)
            return _PgAdminCommandInputs(
                identity=identity,
                paths=paths,
                port=select_port(paths),
                password=password,
                database=database,
                database_probe=database_probe,
                fingerprint_inputs=fingerprint_inputs,
            )
        except Exception:
            return None

    def _open_pgadmin_impl(
        self,
        selector: EnvironmentSelector,
        *,
        cluster: PostgresCluster | None = None,
        captured_inputs: _PgAdminCommandInputs | None = None,
        lock_held: bool = False,
    ) -> _PgAdminReconciliationCarrier:
        """Run preflight and the locked phase, returning its private carrier.

        Resolution and all security-critical preconditions live here so callers
        cannot supply browser-controlled database or endpoint values. The
        caller chooses whether the phase owns its lock or participates in the
        full operation's already-held lifecycle lock.
        """
        try:
            env = self.get(selector)
        except EnvironmentNotFoundError:
            raise PgAdminEnvironmentNotFoundError() from None
        except EnvironmentResolutionError:
            raise PgAdminUnavailableError() from None
        self._require_pgadmin_environment(env)
        database = self._pgadmin_database(env)
        cluster = (
            self._healthy_owned_compose(env)
            if cluster is None
            else self._validate_healthy_owned_compose(cluster)
        )
        from odoo_instance_sdk.internal.proc import active_context

        context = active_context()
        if (
            context is not None
            and context.planned("pgadmin.postgres.status.ps")
            and not context.consumed("pgadmin.postgres.status.ps")
        ):
            # A captured domain double may provide its own health decision;
            # it still has to account for the exact finite status phase.
            _pgadmin_captured_cluster_state(context)
        instance = self._configured_pgadmin_instance(env)
        self._require_pgadmin_database(instance, database)

        if captured_inputs is not None and (
            captured_inputs.database != database
            or captured_inputs.password != (instance.config.db_password or "")
        ):
            raise StalePlanError("captured pgAdmin inputs changed before execution")

        try:
            from odoo_instance_sdk.internal.pgadmin import PgAdminProvisioningPhase
            from odoo_instance_sdk.internal.proc import active_context

            context = active_context()
            return PgAdminProvisioningPhase(
                instance=instance,
                cluster=cluster,
                database=database,
                captured_identity=(captured_inputs.identity if captured_inputs else None),
                captured_paths=(captured_inputs.paths if captured_inputs else None),
                captured_port=(captured_inputs.port if captured_inputs else None),
                captured_fingerprint=(
                    captured_inputs.fingerprint_inputs if captured_inputs else None
                ),
                executor=context.executor if context is not None else None,
            )._provision_carrier(lock_held=lock_held)
        except (PgAdminError, StalePlanError):
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
        return self._validate_healthy_owned_compose(cluster)

    def _validate_healthy_owned_compose(self, cluster: PostgresCluster) -> PostgresCluster:
        if cluster.mode != "compose" or not cluster.owned:
            raise PgAdminNotEligibleError()
        try:
            from odoo_instance_sdk.internal.proc import active_context

            context = active_context()
            if context is not None and context.planned("pgadmin.postgres.status.ps"):
                state = _pgadmin_captured_cluster_state(context)
            elif context is not None and callable(getattr(cluster, "_status_compose", None)):
                state = cluster._status_compose(
                    ps_step_id="pgadmin.postgres.status.ps",
                    health_step_id="pgadmin.postgres.status.health",
                )
            else:
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

    def list(
        self,
        *,
        project: ProjectConfig | Path | None = None,
        include_removed: bool = False,
    ) -> list[DevelopmentEnvironment]:
        return self.list_command(
            project=project,
            include_removed=include_removed,
        ).run()

    def list_command(
        self,
        *,
        project: ProjectConfig | Path | None = None,
        include_removed: bool = False,
        executor: ProcessExecutor | None = None,
    ) -> Command[_EnvironmentList]:
        """Capture Git identity probes and catalog selection as one command."""
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep, SubprocessExecutor

        project_path: Path | None = None
        if project is not None:
            project_path = (
                Path(project.repository_root)
                if isinstance(project, ProjectConfig)
                else Path(project)
            )
        steps: list[PreparedStep | PreparedAction] = [
            PreparedAction(
                step_id="environment.list",
                action="list-environments",
                description="List catalog environments",
                read_only=True,
            )
        ]
        if project_path is not None:
            steps.extend(
                (
                    PreparedStep(
                        step_id="environment.list.git.toplevel",
                        argv=("git", "-C", str(project_path), "rev-parse", "--show-toplevel"),
                        cwd=str(project_path),
                        timeout=30.0,
                        read_only=True,
                    ),
                    PreparedStep(
                        step_id="environment.list.git.common-dir",
                        argv=("git", "-C", str(project_path), "rev-parse", "--git-common-dir"),
                        cwd=str(project_path),
                        timeout=30.0,
                        read_only=True,
                    ),
                )
            )
        captured_steps = tuple(steps)

        def _text(result: ProcessResult) -> str:
            value = result.stdout
            if isinstance(value, bytes):
                return value.decode(errors="replace")
            return value if isinstance(value, str) else ""

        def run(context: RunContext[_EnvironmentList]) -> _EnvironmentList:
            context.action("environment.list")
            catalog = self._client.get_catalog()
            if project_path is None:
                rows = catalog.list_environments(include_removed=include_removed)
            else:
                top = cast("ProcessResult", context.process("environment.list.git.toplevel"))
                if top.returncode != 0:
                    from odoo_instance_sdk.internal.git_worktree import GitError

                    raise GitError(f"not a git repository: {project_path}")
                common = cast("ProcessResult", context.process("environment.list.git.common-dir"))
                if common.returncode != 0 or not _text(common).strip():
                    from odoo_instance_sdk.internal.git_worktree import GitError

                    raise GitError(f"git common directory unavailable: {project_path}")
                common_path = Path(_text(common).strip())
                if not common_path.is_absolute():
                    common_path = project_path / common_path
                rows = catalog.list_environments(
                    git_common_dir=str(common_path.resolve()),
                    include_removed=include_removed,
                )
            return [_row_to_env(row) for row in rows]

        return Command.create(
            ExecutionPlan(steps=tuple(step.public_projection() for step in captured_steps)),
            run,
            captured_steps,
            executor=executor or SubprocessExecutor(),
        )

    def remove(self, selector: EnvironmentSelector) -> None:
        return self.remove_command(selector).run()

    def remove_command(
        self,
        selector: EnvironmentSelector,
        *,
        executor: ProcessExecutor | None = None,
    ) -> Command[None]:
        from odoo_instance_sdk.internal.proc import PreparedStep

        env = (
            self._resolve_selector(selector, include_removed=True)
            if isinstance(selector, str)
            else selector
        )
        steps: tuple[PreparedStep | PreparedAction, ...] = ()
        worktree = Path(env.worktree_path)
        if worktree.is_dir():
            steps = (
                PreparedStep(
                    step_id="environment.remove.worktree-dirty",
                    argv=("git", "-C", str(worktree), "status", "--porcelain"),
                    timeout=30.0,
                    read_only=True,
                ),
                PreparedStep(
                    step_id="environment.remove.worktree",
                    argv=(
                        "git",
                        "-C",
                        str(Path(env.repository_root)),
                        "worktree",
                        "remove",
                        str(worktree),
                    ),
                    timeout=30.0,
                    mutating=True,
                ),
            )
        steps = (*steps, *self._remove_copy_database_steps(env))
        return self._action_command(
            "environment.remove",
            "Remove the selected development environment",
            lambda: self._remove_impl(env),
            executor=executor,
            mutating=True,
            steps=steps,
            optional_steps=tuple(step.step_id for step in steps),
        )

    def _remove_copy_database_steps(
        self, env: DevelopmentEnvironment
    ) -> tuple[PreparedStep | PreparedAction, ...]:
        """Capture the conditional COPY cleanup probes before removal starts."""
        if env.db_mode is not EnvironmentDatabaseMode.COPY or env.target_db_name is None:
            return ()
        config_path = Path(env.generated_config_path)
        if not config_path.is_file():
            return ()
        try:
            from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep

            cfg = parse_odoo_config(config_path)
            password = get_admin_passwd(cfg)
            if password is None:
                return ()
            instance = self._client.instance.from_config(config_path, master_password=password)
            before = instance.databases._psql_probe_for(
                env.target_db_name, "environment.remove.database.exists-before"
            )
            after = instance.databases._psql_probe_for(
                env.target_db_name, "environment.remove.database.exists-after"
            )
            postcondition = instance.databases._psql_probe_for(
                env.target_db_name, "environment.remove.database.exists-postcondition"
            )
        except Exception:
            return ()
        probes = tuple(
            probe for probe in (before, after, postcondition) if isinstance(probe, PreparedStep)
        )
        if not probes:
            return ()
        return (
            PreparedAction(
                step_id="environment.remove.database.drop",
                action="drop-owned-copy-database",
                description="Drop the owned COPY database between exact existence probes",
                mutating=True,
            ),
            *probes,
        )

    def _remove_impl(self, env: DevelopmentEnvironment) -> None:
        from odoo_instance_sdk.internal.proc import active_context

        catalog = self._client.get_catalog()
        with exclusive_lock(environment_lock_path(str(env.id))):
            self._do_remove(catalog, env, context=cast("RunContext[None] | None", active_context()))

    def _action_command(
        self,
        step_id: str,
        description: str,
        callback: Callable[[], T],
        *,
        executor: ProcessExecutor | None,
        mutating: bool,
        steps: Sequence[PreparedStep | PreparedAction] = (),
        optional_steps: Sequence[str] = (),
    ) -> Command[T]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, SubprocessExecutor

        step = PreparedAction(
            step_id=step_id,
            action=step_id,
            description=description,
            mutating=mutating,
        )

        def run(context: RunContext[T]) -> T:
            context.action(step_id)
            result = callback()
            for optional_step_id in optional_steps:
                if not context.consumed(optional_step_id):
                    context.skip(optional_step_id)
            return result

        prepared_steps: tuple[PreparedAction | PreparedStep, ...] = (step, *steps)
        from odoo_instance_sdk.internal.proc import prepared_command

        return Command.from_prepared(
            ExecutionPlan(steps=tuple(item.public_projection() for item in prepared_steps)),
            prepared_command(
                run,
                prepared_steps,
                executor=executor or SubprocessExecutor(),
            ),
        )

    def _do_remove(
        self,
        catalog: BackupCatalog,
        env: DevelopmentEnvironment,
        *,
        context: RunContext[None] | None = None,
    ) -> None:
        cat = catalog
        copy_plan = self._preflight_remove(cat, env, context=context)
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
            cleanup_failed = (
                self._drop_copy_target(copy_plan, failures, context=context) or cleanup_failed
            )
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
                cleanup_instance = copy_plan.instance
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
            self._remove_worktree(
                cat,
                env,
                repo_root,
                worktree,
                failures,
                dirty_checked=context is not None
                and context.planned("environment.remove.worktree-dirty"),
                context=context,
            )
            or cleanup_failed
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
        self,
        catalog: BackupCatalog,
        env: DevelopmentEnvironment,
        *,
        context: RunContext[None] | None = None,
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
        status_step_id = "environment.remove.worktree-dirty"
        if context is not None and context.planned(status_step_id):
            status_result = cast("ProcessResult", context.process(status_step_id))
            status_output = status_result.stdout
            status_text = (
                status_output.decode(errors="replace")
                if isinstance(status_output, bytes)
                else str(status_output or "")
            )
            if status_result.returncode != 0:
                raise EnvironmentConflictError(
                    "worktree_status_failed", "could not inspect the owned worktree"
                )
            is_dirty = bool(status_text.strip())
        elif worktree.is_dir():
            from odoo_instance_sdk.internal.git_worktree import worktree_is_dirty

            is_dirty = worktree_is_dirty(worktree)
        else:
            is_dirty = False
        if is_dirty:
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

    def _drop_copy_target(
        self,
        plan: CopyCleanupPlan,
        failures: _StrList,
        *,
        context: RunContext[None] | None = None,
    ) -> bool:
        instance = cast("OdooInstance", plan.instance)
        try:
            if not instance.databases.exists(plan.target_database):
                return False
            if context is not None and context.planned("environment.remove.database.drop"):
                context.action("environment.remove.database.drop")
                instance.databases._drop_impl(
                    plan.target_database,
                    timeout=None,
                    psql_step_id=(
                        "environment.remove.database.exists-after"
                        if context.planned("environment.remove.database.exists-after")
                        else None
                    ),
                )
            else:
                instance.databases.drop(plan.target_database)
            if context is not None and context.planned(
                "environment.remove.database.exists-postcondition"
            ):
                still_exists = instance.databases._exists_impl(
                    plan.target_database,
                    psql_step_id="environment.remove.database.exists-postcondition",
                )
            else:
                still_exists = instance.databases.exists(plan.target_database)
            if still_exists:
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
        cat: BackupCatalog,
        env: DevelopmentEnvironment,
        repo_root: Path,
        worktree: Path,
        failures: _StrList,
        *,
        dirty_checked: bool = False,
        context: RunContext[None] | None = None,
    ) -> bool:

        catalog = cat
        if not worktree.is_dir():
            if context is not None and context.planned("environment.remove.worktree"):
                context.skip("environment.remove.worktree")
            catalog.add_environment_event(
                str(env.id), "remove", "succeeded", message="worktree already absent"
            )
            return False
        from odoo_instance_sdk.internal.git_worktree import worktree_is_dirty, worktree_remove

        if context is not None and not context.planned("environment.remove.worktree"):
            raise StalePlanError("owned worktree appeared after remove command capture")
        if not dirty_checked and worktree_is_dirty(worktree):
            msg = f"worktree {worktree} is dirty; refusing to remove"
            catalog.update_environment_state(
                str(env.id), EnvironmentState.CLEANUP_FAILED, last_error=msg
            )
            catalog.add_environment_event(str(env.id), "remove", "failed", message=msg)
            raise EnvironmentConflictError("dirty_worktree", msg)
        try:
            if context is None:
                worktree_remove(repo_root, worktree)
            else:
                result = cast("ProcessResult", context.process("environment.remove.worktree"))
                if result.returncode != 0:
                    failures.append(f"worktree remove: {_process_stderr(result)}")
                    return True
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
        cat: BackupCatalog,
        env: DevelopmentEnvironment,
        failures: _StrList,
    ) -> bool:

        catalog = cat
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
        catalog: BackupCatalog | None,
        http_interface: str,
        exclude_project: Path | None = None,
    ) -> int:
        cat = catalog
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


def _row_to_env(row: sqlite3.Row) -> DevelopmentEnvironment:
    def _get(key: str) -> JsonValue:
        r = row
        return cast("JsonValue", r[key])

    def _opt(key: str) -> str | None:
        r = row
        try:
            v = cast("JsonValue", r[key])
        except (KeyError, IndexError):
            return None
        if v is None:
            return None
        return str(v)

    backup_raw: JsonValue = None
    with contextlib.suppress(KeyError, IndexError):
        backup_raw = cast("JsonValue", row["backup_id"])
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


def _row_to_backup(row: sqlite3.Row) -> Backup | None:
    r = row
    try:
        path = cast("JsonValue", r["path"])
    except (KeyError, IndexError):
        return None
    if path is None or not Path(str(path)).is_file():
        return None
        size_raw: JsonValue = None
    with contextlib.suppress(KeyError, IndexError):
        size_raw = cast("JsonValue", r["size_bytes"])
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
        PreparedStep(
            step_id="checkout.validate.git.toplevel",
            argv=("git", "-C", str(plan.repo_root), "rev-parse", "--show-toplevel"),
            read_only=True,
        ),
        PreparedStep(
            step_id="checkout.validate.git.common-dir",
            argv=("git", "-C", str(plan.repo_root), "rev-parse", "--git-common-dir"),
            read_only=True,
        ),
        PreparedStep(
            step_id="checkout.validate.git.base",
            argv=(
                "git",
                "-C",
                str(plan.repo_root),
                "rev-parse",
                "--verify",
                plan.base_ref,
            ),
            read_only=True,
        ),
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
            PreparedStep(
                step_id="checkout.cleanup.worktree",
                argv=(
                    "git",
                    "-C",
                    str(plan.repo_root),
                    "worktree",
                    "remove",
                    str(plan.worktree),
                ),
                timeout=30.0,
                mutating=True,
            ),
            PreparedAction("checkout.cleanup"),
        )
    )
    return tuple(steps)


def _pgadmin_cluster_snapshot(selector: EnvironmentSelector) -> PostgresCluster | None:
    """Capture the selected compose object once for command construction."""
    if not isinstance(selector, DevelopmentEnvironment):
        return None
    selected_database = (
        selector.target_db_name
        if selector.db_mode is EnvironmentDatabaseMode.COPY
        else selector.source_db_name
    )
    if selected_database is None:
        return None
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    try:
        return PostgresCluster.from_project(Path(selector.repository_root))
    except Exception:
        return None


def _pgadmin_captured_cluster_state(context: RunContext[T]) -> PostgresClusterState:
    """Consume the captured finite Compose status phase exactly once."""
    result = cast("ProcessResult", context.process("pgadmin.postgres.status.ps"))
    if result.returncode != 0:
        if context.planned("pgadmin.postgres.status.health"):
            context.skip("pgadmin.postgres.status.health")
        return PostgresClusterState.UNKNOWN
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    if not any(line.strip() for line in stdout.splitlines()):
        if context.planned("pgadmin.postgres.status.health"):
            context.skip("pgadmin.postgres.status.health")
        return PostgresClusterState.STOPPED
    health = cast("ProcessResult", context.process("pgadmin.postgres.status.health"))
    if health.returncode == 0:
        return PostgresClusterState.HEALTHY
    return PostgresClusterState.STARTING


def _skip_planned_pgadmin_database_probe() -> None:
    """Account a fallback-only probe when preflight exits before database lookup."""
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    if (
        context is not None
        and context.planned("pgadmin.database.exists.psql")
        and not context.consumed("pgadmin.database.exists.psql")
    ):
        context.skip("pgadmin.database.exists.psql")


def _pgadmin_command_steps(
    selector: EnvironmentSelector,
    *,
    cluster: PostgresCluster | None = None,
    inputs: _PgAdminCommandInputs | None = None,
    include_reconciliation: bool = False,
) -> tuple[PreparedStep | PreparedAction, ...]:
    """Describe the pgAdmin child-process boundary from captured inputs."""
    if not isinstance(selector, DevelopmentEnvironment):
        return ()
    from odoo_instance_sdk.internal import pgadmin_files
    from odoo_instance_sdk.internal.pgadmin_files import PGADMIN_CONTAINER_NAME
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep

    try:
        cluster = cluster or _pgadmin_cluster_snapshot(selector)
        if cluster is None:
            return ()
        if cluster.mode != "compose":
            return ()
        compose_file = cluster.compose_file
        prefix = (
            "docker",
            "compose",
            "--project-name",
            cluster.compose_project_name,
            "-f",
            str(compose_file),
        )
        paths = inputs.paths if inputs is not None else pgadmin_files.PgAdminPaths.from_defaults()
    except Exception:
        # The regular typed preflight remains authoritative for unresolved
        # selectors/configuration.  A command with no process manifest keeps
        # that error path observable rather than inventing a partial plan.
        return ()

    status_steps: list[PreparedStep | PreparedAction] = [
        PreparedStep(
            step_id="pgadmin.postgres.status.ps",
            argv=(*prefix, "ps", "--format", "json"),
            cwd=str(compose_file.parent),
            read_only=True,
        ),
        PreparedStep(
            step_id="pgadmin.postgres.status.health",
            argv=(
                *prefix,
                "exec",
                "-T",
                "postgres",
                "pg_isready",
                "-U",
                str(getattr(cluster, "_user", "") or ""),
                "-d",
                "postgres",
            ),
            cwd=str(compose_file.parent),
            read_only=True,
        ),
    ]
    if inputs is None:
        return tuple(status_steps)

    identity = inputs.identity

    steps: list[PreparedStep | PreparedAction] = [*status_steps]
    if inputs.database_probe is not None:
        steps.append(inputs.database_probe)
    steps.extend(
        [
            PreparedStep(
                step_id="pgadmin.identity.ps",
                argv=(*prefix, "ps", "--format", "json"),
                read_only=True,
            ),
            PreparedStep(
                step_id="pgadmin.identity.inspect",
                argv=("docker", "inspect", "--format", "json", identity.container_name),
                read_only=True,
            ),
            PreparedStep(
                step_id="pgadmin.identity.network",
                argv=("docker", "network", "inspect", "--format", "json", identity.network),
                read_only=True,
            ),
            PreparedStep(
                step_id="pgadmin.container.inspect.0",
                argv=("docker", "inspect", "--format", "json", PGADMIN_CONTAINER_NAME),
                read_only=True,
            ),
            PreparedAction(
                step_id="pgadmin.port.revalidate",
                action="revalidate-pgadmin-port",
                description="Revalidate the captured loopback port under the pgAdmin lifecycle lock",
                read_only=True,
            ),
            PreparedAction(
                step_id="pgadmin.prepare",
                action="provision-pgadmin-reconciliation",
                description=(
                    "Run the locked pgAdmin provisioning phase and return exact reconciliation inputs"
                ),
                mutating=True,
            ),
        ]
    )
    file_acl = ",".join(sorted(pgadmin_files._file_acl()))
    acl_specs = (
        (
            (
                "pgadmin.acl.root.set",
                (
                    "setfacl",
                    "--set",
                    ",".join(sorted(pgadmin_files._directory_acl(0o710))),
                    str(paths.root),
                ),
            ),
            ("pgadmin.acl.root.validate", ("getfacl", "-cp", str(paths.root))),
            (
                "pgadmin.acl.private.set",
                (
                    "setfacl",
                    "--set",
                    ",".join(sorted(pgadmin_files._directory_acl(0o710))),
                    str(paths.private_dir),
                ),
            ),
            ("pgadmin.acl.private.validate", ("getfacl", "-cp", str(paths.private_dir))),
            (
                "pgadmin.acl.data.set",
                (
                    "setfacl",
                    "--set",
                    ",".join(sorted(pgadmin_files._directory_acl(0o770))),
                    str(paths.data_dir),
                ),
            ),
            (
                "pgadmin.acl.data.default.set",
                (
                    "setfacl",
                    "--default",
                    "--set",
                    ",".join(sorted(pgadmin_files._default_directory_acl())),
                    str(paths.data_dir),
                ),
            ),
            ("pgadmin.acl.data.validate", ("getfacl", "-cp", str(paths.data_dir))),
            ("pgadmin.acl.data.default.validate", ("getfacl", "-cp", str(paths.data_dir))),
            ("pgadmin.acl.admin.existing", ("getfacl", "-cp", str(paths.admin_password))),
            (
                "pgadmin.acl.admin.final.set",
                ("setfacl", "--set", file_acl, str(paths.admin_password)),
            ),
            ("pgadmin.acl.pgpass.existing", ("getfacl", "-cp", str(paths.pgpass))),
            (
                "pgadmin.acl.pgpass.final.set",
                ("setfacl", "--set", file_acl, str(paths.pgpass)),
            ),
            ("pgadmin.acl.servers.existing", ("getfacl", "-cp", str(paths.servers_json))),
            (
                "pgadmin.acl.servers.final.set",
                ("setfacl", "--set", file_acl, str(paths.servers_json)),
            ),
            ("pgadmin.acl.metadata.existing", ("getfacl", "-cp", str(paths.metadata))),
            (
                "pgadmin.acl.metadata.final.set",
                ("setfacl", "--set", file_acl, str(paths.metadata)),
            ),
            ("pgadmin.acl.admin.final", ("getfacl", "-cp", str(paths.admin_password))),
            ("pgadmin.acl.pgpass.final", ("getfacl", "-cp", str(paths.pgpass))),
            ("pgadmin.acl.servers.final", ("getfacl", "-cp", str(paths.servers_json))),
            ("pgadmin.acl.metadata.final", ("getfacl", "-cp", str(paths.metadata))),
        )
        if pgadmin_files._linux()
        else ()
    )
    for step_id, argv in acl_specs:
        steps.append(
            PreparedStep(
                step_id=step_id,
                argv=argv,
                read_only=argv[0] == "getfacl",
                mutating=argv[0] == "setfacl",
            )
        )
    if include_reconciliation and inputs.fingerprint_inputs is not None:
        from odoo_instance_sdk.internal.pgadmin_container import (
            reconciliation_inspect_step,
            reconciliation_steps,
        )

        steps.extend(
            [
                *pgadmin_files.preparation_revalidation_steps(paths),
                reconciliation_inspect_step(),
                PreparedAction(
                    step_id="pgadmin.reconciliation.port.revalidate",
                    action="pgadmin_reconciliation_port_revalidate",
                    description="Revalidate the captured loopback port under the lifecycle lock",
                    read_only=True,
                ),
                *reconciliation_steps(
                    paths=paths,
                    port=inputs.port,
                    network=identity.network,
                    fingerprint=inputs.fingerprint_inputs.fingerprint,
                    secret_values=(
                        inputs.fingerprint_inputs.fingerprint,
                        inputs.password,
                    ),
                ),
            ]
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
