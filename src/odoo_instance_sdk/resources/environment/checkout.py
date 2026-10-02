from __future__ import annotations

import hashlib
import importlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    EnvironmentConflictError,
    PlanError,
    PlanValidationError,
    StalePlanError,
)
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.dbprep.source import (
    classify_freshness,
    compare_provenance,
)
from odoo_instance_sdk.internal.locks import (
    exclusive_lock,
    provisioning_lock_path,
)
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.project_env import load_project_environment
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.models import (
    Backup,
    BackupFreshness,
    BackupProvenanceComparison,
    BackupProvenanceStatus,
    CopyReplacementResult,
    DatabasePreparationResult,
    DatabaseRefreshOptions,
    EnvironmentCheckoutPlan,
    EnvironmentCheckoutResult,
    EnvironmentCodeOwnership,
    EnvironmentState,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_artifacts import (
    _capture_checkout_stage,
    _checkout_execution_plan_with_private_steps,
    _checkout_steps,
    _normalize_checkout_stage,
    _planning_error_outcome,
    _planning_result,
    _restore_audit_backup,
    _row_to_env,
    _validate_checkout_stage,
)
from odoo_instance_sdk.resources.environment.checkout_execution import do_checkout
from odoo_instance_sdk.resources.environment.checkout_planning import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    _checkout_public_plan,
    _CheckoutPlan,
    _CheckoutPlanningState,
    _CheckoutSnapshot,
    _execution_plan,
    _ExpressionApi,
    _ExpressionResult,
    _PlanningOutcome,
    _public_checkout_plan,
    _PythonMode,
    _resolve_checkout_dependency_inputs,
    _resolve_checkout_hash_lock,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.dbprep.source import _RestoreSourceInput
    from odoo_instance_sdk.internal.proc import (
        ProcessExecutor,
        ProcessResult,
        RunContext,
    )
    from odoo_instance_sdk.models.backup import DevelopmentEnvironment
    from odoo_instance_sdk.resources.instance import AuxiliaryRestoreSession
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


class _CheckoutMixin:
    if TYPE_CHECKING:
        _client: OdooClient

        def _verify_tools(self) -> None: ...

        def _resolve_source_config(
            self,
            options: EnvironmentCheckoutOptions,
            project: ProjectConfig,
            repo_root: Path,
        ) -> Path | None: ...

        def _resolve_python_mode(
            self,
            options: EnvironmentCheckoutOptions,
            project: ProjectConfig,
            repo_root: Path,
        ) -> _PythonMode: ...

        def _resolve_dbs(
            self,
            options: EnvironmentCheckoutOptions,
            project: ProjectConfig,
            cfg: dict[str, str],
            db_mode: str,
            branch: str,
            repo_root: Path,
        ) -> tuple[str | None, str | None]: ...

        def _allocate_port(
            self,
            requested: int | None,
            project: ProjectConfig,
            catalog: BackupCatalog | None,
            http_interface: str,
            exclude_project: Path | None = None,
        ) -> int: ...

        def _resolve_odoo_bin(
            self,
            options: EnvironmentCheckoutOptions,
            project: ProjectConfig,
            repo_root: Path,
        ) -> str: ...

        def _resolve_runtime_cwd(
            self, project: ProjectConfig, repo_root: Path, worktree: Path
        ) -> str: ...

        def _preflight_copy_checkout(self, plan: _CheckoutPlan) -> None: ...

        def _do_copy_restore(
            self,
            *,
            context: RunContext[DevelopmentEnvironment],
            cat: BackupCatalog,
            env_id: uuid.UUID,
            source_config: Path | None,
            cfg_dict: Mapping[str, str],
            source_db: str,
            target_db: str,
            repo_root: Path,
            project: ProjectConfig,
            remote_name: str | None,
            selected_backup: Backup | None,
        ) -> uuid.UUID: ...

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
        ) -> None: ...

    def _copy_source_metadata(
        self, project: ProjectConfig, options: EnvironmentCheckoutOptions
    ) -> tuple[str | None, str | None, str | None, str, Backup | None]:
        """Resolve one explicit COPY source without contacting a remote."""
        if options.remote_name is not None and options.backup_id is not None:
            raise ConfigError("COPY accepts exactly one of remote_name or backup_id")
        if options.remote_name is not None and options.source_database is not None:
            raise ConfigError("--remote cannot be combined with --source-db")
        if options.backup_id is not None and options.source_database is not None:
            raise ConfigError("--backup cannot be combined with --source-db")

        if options.remote_name is not None:
            from odoo_instance_sdk.internal.dbprep.source import resolve_test_source

            source = resolve_test_source(
                project, DatabaseRefreshOptions(remote_name=options.remote_name)
            )
            assert source.config.database is not None
            return (
                source.source_name,
                source.config.base_url,
                source.branch,
                source.config.database,
                None,
            )

        if options.backup_id is not None:
            try:
                backup_id = uuid.UUID(str(options.backup_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ConfigError("catalogue backup identifier must be a complete UUID") from exc
            projection = self._client.get_catalog()._resolve_backup_projection(str(backup_id))
            if projection.state.value != "available":
                raise ConfigError("catalogue backup is not available")
            backup = projection.backup
            return (
                backup.source_name,
                backup.source_base_url,
                backup.source_git_branch,
                backup.database_name,
                backup,
            )

        return None, None, None, "", None

    def _prepare_checkout(  # noqa: C901 -- adoption and SDK-owned plans share validation inputs
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
        dry_run_paths: bool,
        checkout_path: Path | None = None,
    ) -> _CheckoutPlan:
        if isinstance(project, ProjectConfig):
            project_cfg = project
            project_path = project_cfg.repository_root
        else:
            project_path = Path(project)
            project_cfg = ProjectConfig.load(project_path)

        # A plan must be inspectable without creating durable user state.
        # Do not open the durable catalog until all COPY preconditions have
        # passed.  Opening it can create/migrate user state, which must not be
        # the observable result of a rejected checkout.
        catalog = None

        if options.db_mode is not EnvironmentDatabaseMode.COPY and (
            options.remote_name is not None or options.backup_id is not None
        ):
            raise ConfigError("remote_name and backup_id are valid only for COPY checkout")

        from odoo_instance_sdk.internal.git_worktree import (
            local_branch_exists,
            remote_branches,
            rev_parse_git_common_dir,
            rev_parse_toplevel,
        )

        configured_root = project_cfg.repository_root.resolve()
        repo_root = rev_parse_toplevel(checkout_path or project_path)
        git_common = rev_parse_git_common_dir(repo_root)
        git_common_str = str(git_common)

        hash_lock = _resolve_checkout_hash_lock(options, repo_root)

        self._verify_tools()

        source_name, source_base_url, source_git_branch, _copy_source_database, selected_backup = (
            self._copy_source_metadata(project_cfg, options)
            if options.db_mode is EnvironmentDatabaseMode.COPY
            else (None, None, None, "", None)
        )
        if options.backup_id is not None and options.base_ref is None and source_git_branch is None:
            raise EnvironmentConflictError(
                "backup_provenance_unknown",
                "retained backup provenance is unknown; pass --base explicitly",
            )
        base_ref = options.base_ref or source_git_branch or project_cfg.default_base_ref or "HEAD"
        from odoo_instance_sdk.internal.git_worktree import rev_parse_verify

        base_revision = rev_parse_verify(repo_root, base_ref)

        if local_branch_exists(repo_root, branch):
            worktree_mode: Literal["local", "remote", "new"] = "local"
        elif remote_branches(repo_root, branch):
            worktree_mode = "remote"
        else:
            worktree_mode = "new"

        source_config = self._resolve_source_config(
            options, project_cfg, configured_root if checkout_path is not None else repo_root
        )
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
        artifact_key = repo_key(
            configured_root if checkout_path is not None else repo_root, git_common
        )
        env_root = (
            _paths.get_environments_root(ensure_exists=not dry_run_paths)
            / artifact_key
            / str(env_id)
        )
        worktree = (checkout_path or (env_root / "worktree")).resolve()
        venv = env_root / "venv"
        generated_cfg = env_root / "odoo.conf"
        lock_file = env_root / "requirements.lock"
        worktree_argv: tuple[str, ...]
        if checkout_path is not None:
            worktree_argv = ()
        elif worktree_mode == "local":
            worktree_argv = (
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
        dependency_inputs = _resolve_checkout_dependency_inputs(
            project_cfg, repo_root, worktree, hash_lock
        )

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
            hash_lock=hash_lock,
            base_revision=base_revision,
            worktree_argv=worktree_argv,
            created_at=now,
            options=options,
            source_name=source_name,
            source_base_url=source_base_url,
            source_git_branch=source_git_branch,
            selected_backup=selected_backup,
            project_root=configured_root,
            project_id=(
                f"project_{repo_key(configured_root, git_common_dir(configured_root))}"
                if checkout_path is not None
                else None
            ),
            checkout_commit_sha=rev_parse_verify(repo_root, "HEAD"),
            code_ownership=(
                EnvironmentCodeOwnership.CALLER_OWNED
                if checkout_path is not None
                else EnvironmentCodeOwnership.SDK_OWNED
            ),
            artifact_root=env_root,
            adopted=checkout_path is not None,
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
        restore_source: _RestoreSourceInput = None,
        target_database: str | None = None,
    ) -> DatabasePreparationResult:
        return self.refresh_database_command(
            project,
            options=options,
            restore_source=restore_source,
            target_database=target_database,
        ).run()

    def refresh_database_command(
        self,
        project: ProjectConfig | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        restore_source: _RestoreSourceInput = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabasePreparationResult]:
        from odoo_instance_sdk.internal.dbprep.materialize import (
            DatabasePreparationCoordinator,
        )

        return DatabasePreparationCoordinator(self._client).refresh_database_command(
            project,
            options=options,
            restore_source=restore_source,
            target_database=target_database,
            admin_password=admin_password,
            admin_password_provenance=admin_password_provenance,
            executor=executor,
        )

    def replace_copy_database(
        self,
        environment: DevelopmentEnvironment,
        backup_id: uuid.UUID,
        *,
        reset_admin_password: bool = False,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
    ) -> CopyReplacementResult:
        return self.replace_copy_database_command(
            environment,
            backup_id,
            reset_admin_password=reset_admin_password,
            admin_password=admin_password,
            admin_password_provenance=admin_password_provenance,
        ).run()

    def replace_copy_database_command(
        self,
        environment: DevelopmentEnvironment,
        backup_id: uuid.UUID,
        *,
        reset_admin_password: bool = False,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
        executor: ProcessExecutor | None = None,
    ) -> Command[CopyReplacementResult]:
        from odoo_instance_sdk.internal.dbreplace.validation import build_copy_replacement_command

        return build_copy_replacement_command(
            self._client,
            environment,
            backup_id,
            reset_admin_password=reset_admin_password,
            admin_password=admin_password,
            admin_password_provenance=admin_password_provenance,
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
        if options.db_mode is EnvironmentDatabaseMode.COPY and (
            options.remote_name is not None or options.backup_id is not None
        ):
            source_name, source_base_url, source_branch, source_database, backup = (
                self._copy_source_metadata(project_config, options)
            )
            effective_base = (
                options.base_ref or source_branch or project_config.default_base_ref or "HEAD"
            )
            if source_branch is None and options.base_ref is None:
                raise EnvironmentConflictError(
                    "backup_provenance_unknown",
                    "retained backup provenance is unknown; pass --base explicitly",
                )
            comparison = compare_provenance(effective_base, source_branch)
            if comparison.status is BackupProvenanceStatus.MISMATCHED:
                raise EnvironmentConflictError(
                    "backup_provenance_mismatch",
                    "backup source branch does not match checkout base ref",
                    details={
                        "expected_base_ref": comparison.expected_base_ref,
                        "recorded_branch": comparison.recorded_branch,
                    },
                )
            selector_warnings = (
                ("Backup provenance is unknown; branch compatibility could not be verified.",)
                if comparison.status is BackupProvenanceStatus.UNKNOWN
                else ()
            )
            return (
                msgspec.structs.replace(
                    comparison,
                    source_name=source_name,
                    source_base_url=source_base_url,
                    database_name=source_database,
                    backup_id=backup.id if backup is not None else None,
                ),
                BackupFreshness.FRESH,
                selector_warnings,
            )
        root = project_config.repository_root
        source_config = self._resolve_source_config(options, project_config, root)
        config_values = parse_odoo_config(source_config) if source_config is not None else {}
        resolved_source_database, _ = self._resolve_dbs(
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
            resolved_source_database,
            available=False,
        )
        available_backup = _restore_audit_backup(
            self._client,
            config_values,
            resolved_source_database,
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
            if options.source_database is not None and resolved_source_database is not None:
                warnings = (
                    (
                        f"Backup provenance is unknown for explicit source database "
                        f"{resolved_source_database!r}; branch compatibility could not be verified."
                    ),
                )
            else:
                raise EnvironmentConflictError(
                    "backup_provenance_unknown",
                    "backup provenance is unknown; pass --source-db explicitly or refresh a "
                    "provenance-bearing backup",
                    details={"source_database": resolved_source_database},
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
        command = Command.from_prepared(snapshot.execution_plan, prepared)
        if snapshot.private.db_mode is EnvironmentDatabaseMode.COPY:
            source_session = self._copy_auxiliary_session(snapshot.private)
            if source_session is not None:
                from odoo_instance_sdk.resources.instance.auxiliary_restore import (
                    _attach_auxiliary_restore_runtime,
                )

                command = cast(
                    "Command[DevelopmentEnvironment]",
                    _attach_auxiliary_restore_runtime(
                        command,
                        source_session,
                        before_step_id="checkout.catalog",
                    ),
                )
        from odoo_instance_sdk.internal.backup_maintenance import attach_auto_prune

        return attach_auto_prune(
            command,
            backups=self._client.backups,
            project=snapshot.private.project.repository_root,
        )

    def _copy_auxiliary_session(self, plan: _CheckoutPlan) -> AuxiliaryRestoreSession | None:
        if plan.options.remote_name is not None or plan.options.backup_id is not None:
            return None
        if plan.source_config is None:
            return None
        from odoo_instance_sdk.resources.instance import OdooInstance, auxiliary_restore_session
        from odoo_instance_sdk.resources.instance.runtime import _RuntimeBinding

        instance = self._client.instance.from_config(plan.source_config)
        if not isinstance(instance, OdooInstance) or instance.config.start_config is None:
            return None
        python_bin = str(plan.venv / "bin" / "python") if plan.python_owned else plan.python_path
        instance.config = replace(
            instance.config,
            command_prefix=(python_bin, plan.odoo_bin),
            default_cwd=Path(plan.runtime_cwd),
            default_run_args=plan.project.default_run_args,
            project_environment=load_project_environment(plan.repo_root),
        )
        project_id = f"project_{repo_key(plan.repo_root, Path(plan.git_common_dir))}"
        instance._runtime_binding = _RuntimeBinding("project", project_id, project_id, plan.repo_root, git_common_dir(plan.repo_root))  # fmt: skip
        return auxiliary_restore_session(instance)

    def _run_checkout_snapshot(  # noqa: C901 -- locked adoption retry and checkout lifecycle share one boundary
        self, context: RunContext[DevelopmentEnvironment], snapshot: _CheckoutSnapshot
    ) -> DevelopmentEnvironment:
        plan = snapshot.private
        with exclusive_lock(provisioning_lock_path()):
            catalog = None
            if plan.adopted:
                catalog = self._client.get_catalog()
                if plan.project_id is None or plan.adoption_input_fingerprint is None:
                    raise PlanValidationError("adoption plan is missing project identity evidence")
                self._validate_adoption_retry_identity(plan)
                existing = catalog.adopted_environment_for(
                    project_id=plan.project_id,
                    checkout_path=str(plan.worktree),
                    checkout_git_common_dir=plan.git_common_dir,
                )
                if existing is not None:
                    stored_fingerprint = existing["adoption_input_fingerprint"]
                    if stored_fingerprint != plan.adoption_input_fingerprint:
                        raise EnvironmentConflictError(
                            "adoption_conflict",
                            "checkout already has an adoption with different inputs",
                            details={"existing_id": str(existing["id"])},
                        )
                    state = str(existing["state"])
                    if state == EnvironmentState.READY.value:
                        from odoo_instance_sdk.resources.environment.checkout_artifacts import (
                            _checkout_steps,
                        )

                        for step in _checkout_steps(plan):
                            if context.planned(step.step_id) and not context.consumed(step.step_id):
                                context.skip(step.step_id)
                        return _row_to_env(existing)
                    raise EnvironmentConflictError(
                        "adoption_recovery_required",
                        f"existing adoption is {state}; recover or remove it before retrying",
                        details={"existing_id": str(existing["id"]), "state": state},
                    )
            # A new adoption must prove the requested checkout/base identity.
            # A matching ready row has already proved that identity; ordinary
            # caller edits and commits are explicitly allowed on that retry.
            if not plan.adopted or existing is None:
                self._validate_checkout_snapshot(snapshot, context=context)
            if plan.branch_revalidator is not None:
                plan.branch_revalidator(context)
            from odoo_instance_sdk.resources.instance import active_auxiliary_restore_session

            auxiliary_session = active_auxiliary_restore_session()
            if auxiliary_session is not None:
                auxiliary_session.ensure_started(context)
            if plan.db_mode is EnvironmentDatabaseMode.COPY:
                self._preflight_copy_checkout(plan)
            if plan.adopted:
                assert catalog is not None
                assert plan.project_id is not None
                project_root = plan.project_root or plan.project.repository_root
                catalog._register_project(
                    plan.project_id,
                    project_root,
                    git_common_dir(project_root),
                )
            context.action("checkout.catalog")
            if catalog is None:
                catalog = self._client.get_catalog()
            self._revalidate_checkout_locked(catalog, plan)
            result = self._do_checkout(catalog, plan, context=context)
            context.complete_action("checkout.catalog")
            return result

    def _validate_adoption_retry_identity(self, plan: _CheckoutPlan) -> None:
        """Re-capture adoption evidence before reusing a ready catalog row."""
        current_project = ProjectConfig.load(plan.project.repository_root)
        current_source = self._copy_source_metadata(current_project, plan.options)
        current_fingerprint = self._adoption_fingerprint(
            plan.project_id or "",
            plan.worktree,
            Path(plan.git_common_dir),
            plan.options,
            source_name=current_source[0],
            source_base_url=current_source[1],
            source_git_branch=current_source[2],
            source_database=current_source[3],
            selected_backup_id=(current_source[4].id if current_source[4] is not None else None),
        )
        if current_fingerprint != plan.adoption_input_fingerprint:
            raise StalePlanError("adoption resolved base or source identity changed after planning")

    def _validate_checkout_snapshot(  # noqa: C901 -- snapshot validation keeps all drift guards together
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
            actual_base = actual_identity[2]
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
            actual_base = output("checkout.validate.git.base").strip()
            actual_identity = (
                str(top_dir),
                str(common_dir.resolve()),
                actual_base,
            )
        expected_identity = (str(plan.repo_root), plan.git_common_dir, plan.base_revision)
        if actual_identity != expected_identity:
            raise StalePlanError(
                "checkout Git identity changed after planning",
                expected=list(expected_identity),
                actual=list(actual_identity),
            )

        if plan.adopted:
            if context is None:
                current_head = rev_parse_verify(plan.worktree, "HEAD")
                current_base = rev_parse_verify(plan.worktree, plan.base_ref)
                from odoo_instance_sdk.internal.git_worktree import worktree_is_dirty

                dirty = worktree_is_dirty(plan.worktree)
            else:

                def output(step_id: str) -> str:
                    result = cast("ProcessResult", context.process(step_id))
                    value = result.stdout
                    return (
                        value.decode(errors="replace")
                        if isinstance(value, bytes)
                        else (value or "")
                    )

                current_head = output("checkout.validate.git.head").strip()
                current_base = actual_base
                dirty = bool(output("checkout.validate.git.status").strip())
            if dirty:
                raise EnvironmentConflictError(
                    "dirty_checkout", "caller-owned checkout has uncommitted changes"
                )
            if current_base != plan.base_revision or current_head != plan.base_revision:
                raise EnvironmentConflictError(
                    "checkout_base_mismatch",
                    "caller-owned checkout HEAD does not match the explicit base",
                    details={"expected_base": plan.base_revision, "actual_head": current_head},
                )
            return

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
        current_source_branch = None
        if plan.options.remote_name is not None or plan.options.backup_id is not None:
            _, _, current_source_branch, _, _ = self._copy_source_metadata(
                current_project, plan.options
            )
        current_base_ref = (
            plan.options.base_ref
            or current_source_branch
            or current_project.default_base_ref
            or "HEAD"
        )
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
        current_provenance = msgspec.structs.replace(
            current_provenance,
            source_name=plan.source_name,
            source_base_url=plan.source_base_url,
            resolved_base_revision=plan.base_revision,
            backup_id=(
                plan.selected_backup.id
                if plan.selected_backup is not None
                else current_provenance.backup_id
            ),
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
            if not plan.adopted and path.exists():
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

    @staticmethod
    def _adoption_fingerprint(
        project_id: str,
        checkout_path: Path,
        checkout_common: Path,
        options: EnvironmentCheckoutOptions,
        *,
        source_name: str | None,
        source_base_url: str | None,
        source_git_branch: str | None,
        source_database: str,
        selected_backup_id: uuid.UUID | None,
    ) -> str:
        """Hash requested, secret-free evidence that defines one adoption identity."""
        payload = {
            "project_id": project_id,
            "checkout_path": str(checkout_path),
            "checkout_git_common_dir": str(checkout_common),
            "base_ref": options.base_ref,
            "source_name": source_name,
            "source_base_url_digest": (
                hashlib.sha256(source_base_url.encode()).hexdigest()
                if source_base_url is not None
                else None
            ),
            "source_git_branch": source_git_branch,
            "resolved_source_database": source_database,
            "selected_backup_id": (
                str(selected_backup_id) if selected_backup_id is not None else None
            ),
            "db_mode": options.db_mode.value,
            "source_database": options.source_database,
            "target_database": options.target_database,
            "remote_name": options.remote_name,
            "backup_id": str(options.backup_id) if options.backup_id is not None else None,
            "python": str(options.python) if options.python is not None else None,
            "create_venv": options.create_venv,
            "odoo_bin": str(options.odoo_bin) if options.odoo_bin is not None else None,
            "config_path": str(options.config_path) if options.config_path is not None else None,
            "name": options.name,
            "http_port": options.http_port,
            "hash_lock": str(options.hash_lock) if options.hash_lock is not None else None,
            "hash_lock_sha256": options.hash_lock_sha256,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def adopt_command(
        self,
        project: ProjectConfig | Path,
        checkout_path: str | Path,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> Command[DevelopmentEnvironment]:
        """Capture COPY preparation for an existing caller-owned checkout."""
        if options.db_mode is not EnvironmentDatabaseMode.COPY:
            raise ConfigError("adoption requires COPY mode")
        if options.base_ref is None:
            raise ConfigError("adoption requires an explicit base_ref")
        if (options.remote_name is None) == (options.backup_id is None):
            raise ConfigError("adoption requires exactly one of remote_name or backup_id")
        from odoo_instance_sdk.internal.git_worktree import (
            remote_url,
            rev_parse_branch,
            rev_parse_git_common_dir,
            rev_parse_toplevel,
        )

        external_path = Path(checkout_path).expanduser().resolve()
        if not external_path.is_dir():
            raise ConfigError(f"adoption checkout is not a directory: {external_path}")
        actual_root = rev_parse_toplevel(external_path)
        if actual_root != external_path:
            raise EnvironmentConflictError(
                "checkout_path_mismatch",
                "adoption requires the canonical Git working directory, not a child path",
            )
        branch = rev_parse_branch(actual_root)
        common = rev_parse_git_common_dir(actual_root)
        project_cfg = project if isinstance(project, ProjectConfig) else ProjectConfig.load(project)
        project_root = project_cfg.repository_root.resolve()
        configured_remote = remote_url(project_root)
        checkout_remote = remote_url(actual_root)
        if configured_remote is not None and checkout_remote is not None:
            if configured_remote != checkout_remote:
                raise EnvironmentConflictError(
                    "repository_identity_mismatch",
                    "caller-owned checkout remote does not match the selected project",
                )
        elif common != git_common_dir(project_root):
            raise EnvironmentConflictError(
                "repository_identity_unverified",
                "independent clone has no comparable configured origin remote",
            )
        project_id = f"project_{repo_key(project_root, git_common_dir(project_root))}"
        private = self._prepare_checkout(
            project_cfg,
            branch,
            options=options,
            dry_run_paths=True,
            checkout_path=actual_root,
        )
        fingerprint = self._adoption_fingerprint(
            project_id,
            actual_root,
            common,
            options,
            source_name=private.source_name,
            source_base_url=private.source_base_url,
            source_git_branch=private.source_git_branch,
            source_database=private.source_database or "",
            selected_backup_id=(
                private.selected_backup.id if private.selected_backup is not None else None
            ),
        )
        private = replace(
            private,
            project_id=project_id,
            adoption_input_fingerprint=fingerprint,
        )
        provenance, freshness, warnings = self._audit_checkout_plan(project_cfg, branch, options)
        public = _public_checkout_plan(private, provenance, freshness, warnings)
        execution = _execution_plan(private, provenance, freshness, warnings)
        snapshot = _CheckoutSnapshot(private=private, public=public, execution_plan=execution)
        return self._command_from_snapshot(snapshot)

    def adopt(
        self,
        project: ProjectConfig | Path,
        checkout_path: str | Path,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> DevelopmentEnvironment:
        return self.adopt_command(project, checkout_path, options=options).run()

    def _checkout_command_with_branch_revalidation(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
        branch_revalidator: Callable[[RunContext[DevelopmentEnvironment]], None],
    ) -> Command[DevelopmentEnvironment]:
        snapshot = self._build_checkout_snapshot(project, branch, options=options)
        private = replace(snapshot.private, branch_revalidator=branch_revalidator)
        execution_plan = _checkout_execution_plan_with_private_steps(snapshot.execution_plan, private)  # fmt: skip
        return self._command_from_snapshot(replace(snapshot, private=private, execution_plan=execution_plan))  # fmt: skip

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
        except EnvironmentConflictError as exc:
            raise EnvironmentConflictError(
                "port_in_use", f"Port {plan.http_port} is no longer available"
            ) from exc

    def _do_checkout(
        self,
        catalog: BackupCatalog,
        plan: _CheckoutPlan,
        *,
        context: RunContext[DevelopmentEnvironment],
    ) -> DevelopmentEnvironment:
        return do_checkout(self, catalog, plan, context=context)

    def _get_env_row(self, cat: BackupCatalog, env_id: uuid.UUID) -> DevelopmentEnvironment:

        catalog = cat
        row = catalog.get_environment(str(env_id))
        if row is None:
            raise RuntimeError("environment row disappeared after checkout")
        return _row_to_env(row)
