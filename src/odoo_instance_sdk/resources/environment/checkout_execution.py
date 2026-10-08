"""Execution helper for the environment checkout command."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from odoo_instance_sdk.exceptions import (
    ConfigError,
    EnvironmentConflictError,
    InstanceConfigurationError,
)
from odoo_instance_sdk.internal.dependency_sync import revalidate_hash_lock
from odoo_instance_sdk.internal.generated_config import generate_config
from odoo_instance_sdk.internal.locks import exclusive_lock, python_env_lock_path
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.models import Backup
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_artifacts import _checkout_applied_settings
from odoo_instance_sdk.resources.environment.checkout_planning import (
    EnvironmentDatabaseMode,
    EnvironmentState,
    _CheckoutPlan,
    _encode_runtime_json,
    _process_stderr,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import ProcessResult, RunContext
    from odoo_instance_sdk.models.backup import DevelopmentEnvironment
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, CatalogValue


class _CheckoutExecutionHost(Protocol):
    def _get_env_row(self, cat: BackupCatalog, env_id: uuid.UUID) -> DevelopmentEnvironment: ...

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


def do_checkout(  # noqa: C901
    self: _CheckoutExecutionHost,
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
                data_dir=plan.data_dir,
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
                    f"owned runtime preflight failed (returncode={preflight.returncode}){detail}"
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
