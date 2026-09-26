from __future__ import annotations

import importlib
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
    InstanceConfigurationError,
    PlanError,
    PlanValidationError,
    StalePlanError,
)
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.dbprep.source import (
    classify_freshness,
    compare_provenance,
)
from odoo_instance_sdk.internal.dependency_sync import revalidate_hash_lock
from odoo_instance_sdk.internal.generated_config import generate_config
from odoo_instance_sdk.internal.locks import (
    exclusive_lock,
    provisioning_lock_path,
    python_env_lock_path,
)
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.project_env import load_project_environment
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
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
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_artifacts import (
    _capture_checkout_stage,
    _checkout_applied_settings,
    _checkout_execution_plan_with_private_steps,
    _checkout_steps,
    _normalize_checkout_stage,
    _planning_error_outcome,
    _planning_result,
    _restore_audit_backup,
    _row_to_env,
    _validate_checkout_stage,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentState,
    _checkout_public_plan,
    _CheckoutPlan,
    _CheckoutPlanningState,
    _CheckoutSnapshot,
    _encode_runtime_json,
    _ExpressionApi,
    _ExpressionResult,
    _PlanningOutcome,
    _process_stderr,
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
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, CatalogValue


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

        repo_root = rev_parse_toplevel(project_path)
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
        env_root = _paths.get_environments_root(ensure_exists=not dry_run_paths) / key / str(env_id)
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
                replace(
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
                    f"Backup provenance is unknown for explicit source database "
                    f"{resolved_source_database!r}; branch compatibility could not be verified.",
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
        if snapshot.private.db_mode is not EnvironmentDatabaseMode.COPY:
            return command
        if (source_session := self._copy_auxiliary_session(snapshot.private)) is None:
            return command
        from odoo_instance_sdk.resources.instance.auxiliary_restore import (
            _attach_auxiliary_restore_runtime,
        )

        return cast(
            "Command[DevelopmentEnvironment]",
            _attach_auxiliary_restore_runtime(
                command,
                source_session,
                before_step_id="checkout.catalog",
            ),
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

    def _run_checkout_snapshot(
        self, context: RunContext[DevelopmentEnvironment], snapshot: _CheckoutSnapshot
    ) -> DevelopmentEnvironment:
        plan = snapshot.private
        with exclusive_lock(provisioning_lock_path()):
            self._validate_checkout_snapshot(snapshot, context=context)
            if plan.branch_revalidator is not None:
                plan.branch_revalidator(context)
            from odoo_instance_sdk.resources.instance import active_auxiliary_restore_session

            auxiliary_session = active_auxiliary_restore_session()
            if auxiliary_session is not None:
                auxiliary_session.ensure_started(context)
            if plan.db_mode is EnvironmentDatabaseMode.COPY:
                self._preflight_copy_checkout(plan)
            context.action("checkout.catalog")
            catalog = self._client.get_catalog()
            self._revalidate_checkout_locked(catalog, plan)
            result = self._do_checkout(catalog, plan, context=context)
            context.complete_action("checkout.catalog")
            return result

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
            # Register the path before invoking Git.  ``git worktree add`` can
            # be interrupted after creating the administrative entry but
            # before returning; failure cleanup must then remove that partial
            # worktree instead of leaving a stale catalog row and lock.
            created_paths.append(plan.worktree)
            worktree_result = cast("ProcessResult", context.process("checkout.worktree"))
            if worktree_result.returncode != 0:
                stderr = str(worktree_result.stderr or "").strip()
                if "is already checked out at" in stderr or "already used by worktree" in stderr:
                    raise EnvironmentConflictError(  # noqa: TRY301
                        "branch_in_use", f"Branch {plan.branch!r} is already checked out"
                    )
                raise ConfigError(f"git worktree add failed: {stderr}")  # noqa: TRY301
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
                # Create the environment-owned logfile with the other artifacts
                # so detached launch and `logs` share one resolved path.
                env_logfile = plan.generated_config.parent / "odoo.log"
                env_logfile.touch(exist_ok=True)
                created_paths.append(env_logfile)
                context.complete_action("checkout.generated_config")

            if plan.options.create_venv and plan.python_selector is not None:
                venv_result = cast("ProcessResult", context.process("checkout.venv"))
                if venv_result.returncode != 0:
                    raise ConfigError(  # noqa: TRY301
                        f"uv venv failed: {_process_stderr(venv_result)}".strip()
                    )
                created_paths.append(plan.venv)

            env_obj = self._get_env_row(cat, plan.env_id)
            with exclusive_lock(python_env_lock_path(env_obj.python_environment_path)):
                if plan.hash_lock is not None or plan.dependency_inputs:
                    if plan.hash_lock is None:
                        compile_result = cast(
                            "ProcessResult", context.process("checkout.dependencies.compile")
                        )
                        if compile_result.returncode != 0 and not plan.dependency_lock.is_file():
                            raise ConfigError(  # noqa: TRY301
                                "uv pip compile failed and no prior lock: "
                                f"{_process_stderr(compile_result)}"
                            )
                    else:
                        revalidate_hash_lock(plan.hash_lock, plan.options.hash_lock_sha256)
                    install_result = cast(
                        "ProcessResult", context.process("checkout.dependencies.install")
                    )
                    if install_result.returncode != 0:
                        raise ConfigError(  # noqa: TRY301
                            f"uv pip install failed: {_process_stderr(install_result)}".strip()
                        )
                    if plan.hash_lock is None:
                        created_paths.append(plan.dependency_lock)

            if plan.python_owned:
                preflight = cast("ProcessResult", context.process("checkout.runtime.preflight"))
                if preflight.returncode != 0:
                    diagnostic = sanitize_last_error(_process_stderr(preflight).strip())
                    detail = f": {diagnostic}" if diagnostic else ""
                    raise InstanceConfigurationError(  # noqa: TRY301
                        "owned runtime preflight failed "
                        f"(returncode={preflight.returncode}){detail}"
                    )

            context.action("checkout.database")

            if (
                plan.db_mode == EnvironmentDatabaseMode.COPY
                and plan.source_database is not None
                and plan.target_database is not None
            ):
                backup_id = self._do_copy_restore(
                    context=context,
                    cat=cat,
                    env_id=plan.env_id,
                    source_config=plan.source_config,
                    cfg_dict=plan.config_values,
                    source_db=plan.source_database,
                    target_db=plan.target_database,
                    repo_root=plan.repo_root,
                    project=plan.project,
                    remote_name=plan.options.remote_name,
                    selected_backup=plan.selected_backup,
                )

            cat._finalize_environment_checkout(str(plan.env_id), _checkout_applied_settings(plan))
            context.action("checkout.cleanup")
            if context.planned("checkout.cleanup.worktree"):
                context.skip("checkout.cleanup.worktree")
            context.complete_action("checkout.cleanup")
            context.complete_action("checkout.database")
            return self._get_env_row(cat, plan.env_id)

        except BaseException as exc:
            if not context.consumed("checkout.cleanup"):
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
            context.complete_action("checkout.cleanup")
            raise

    def _get_env_row(self, cat: BackupCatalog, env_id: uuid.UUID) -> DevelopmentEnvironment:

        catalog = cat
        row = catalog.get_environment(str(env_id))
        if row is None:
            raise RuntimeError("environment row disappeared after checkout")
        return _row_to_env(row)
