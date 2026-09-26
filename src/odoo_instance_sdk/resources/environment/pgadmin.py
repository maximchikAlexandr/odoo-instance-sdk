from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import (
    ConfigError,
    EnvironmentConflictError,
    EnvironmentNotFoundError,
    EnvironmentResolutionError,
    InstanceConfigurationError,
    StalePlanError,
)
from odoo_instance_sdk.internal.db_name import validate_db_name, validate_filestore_containment
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_artifacts import (
    _infer_single_db,
    _is_venv,
    _resolve_python_bin,
    _row_to_backup,
    _row_to_env,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _SLUG_RE,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentState,
    _process_stderr,
    _PythonMode,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.internal.proc import (
        ProcessResult,
        RunContext,
    )
    from odoo_instance_sdk.models.backup import DevelopmentEnvironment
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


class _PgadminMixin:
    if TYPE_CHECKING:
        _client: OdooClient

    def _remove_worktree(
        self,
        cat: BackupCatalog,
        env: DevelopmentEnvironment,
        repo_root: Path,
        worktree: Path,
        failures: list[str],
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

    def _remove_files(self, generated_cfg: Path, lock_file: Path, failures: list[str]) -> bool:
        failed = False
        for p in (generated_cfg, lock_file):
            try:
                p.unlink(missing_ok=True)
            except OSError as e:
                failed = True
                failures.append(f"{p}: {e}")
        return failed

    def _remove_venv(self, env_root: Path, venv: Path | None, failures: list[str]) -> bool:
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
        failures: list[str],
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
        if not candidate.is_file():
            raise InstanceConfigurationError(f"Odoo script is missing or not a file: {candidate}")
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

    def _resolve_dbs(  # noqa: C901
        self,
        options: EnvironmentCheckoutOptions,
        project: ProjectConfig,
        cfg: dict[str, str],
        db_mode: str,
        branch: str,
        repo_root: Path,
    ) -> tuple[str | None, str | None]:
        if options.remote_name is not None or options.backup_id is not None:
            if db_mode != EnvironmentDatabaseMode.COPY:
                raise ConfigError("remote_name and backup_id are valid only for COPY checkout")
            if options.remote_name is not None and options.backup_id is not None:
                raise ConfigError("COPY accepts exactly one of remote_name or backup_id")
            if options.source_database is not None:
                raise ConfigError("explicit COPY source cannot be combined with --source-db")
            if options.remote_name is not None:
                from odoo_instance_sdk.internal.dbprep.source import resolve_test_source
                from odoo_instance_sdk.models import DatabaseRefreshOptions

                source_resolution = resolve_test_source(
                    project, DatabaseRefreshOptions(remote_name=options.remote_name)
                )
                source = source_resolution.config.database
            else:
                try:
                    backup_id = str(options.backup_id)
                    uuid.UUID(backup_id)
                except (ValueError, TypeError, AttributeError) as exc:
                    raise ConfigError(
                        "catalogue backup identifier must be a complete UUID"
                    ) from exc
                projection = self._client.get_catalog()._resolve_backup_projection(backup_id)
                if projection.state.value != "available":
                    raise ConfigError("catalogue backup is not available")
                source = projection.backup.database_name
            if not source:
                raise ConfigError("explicit COPY source has no database provenance")
            target = options.target_database or self._default_target_db(source, branch)
            validate_db_name(target)
            data_dir = cfg.get("data_dir")
            if data_dir:
                validate_filestore_containment(Path(data_dir), target)
            return source, target
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
        resolved_target: str | None = options.target_database
        if resolved_target is None:
            resolved_target = self._default_target_db(source, branch)
        validate_db_name(resolved_target)
        data_dir = cfg.get("data_dir")
        if data_dir:
            validate_filestore_containment(Path(data_dir), resolved_target)
        return source, resolved_target

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
