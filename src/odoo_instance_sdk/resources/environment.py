from __future__ import annotations

import contextlib
import enum
import hashlib
import re
import shutil
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Union, cast

if TYPE_CHECKING:
    import sqlite3

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
)
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.db_name import validate_db_name, validate_filestore_containment
from odoo_instance_sdk.internal.generated_config import generate_config
from odoo_instance_sdk.internal.git_worktree import (
    worktree_add,
)
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
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.urls import assert_local
from odoo_instance_sdk.models import Backup, BackupFormat
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import CopyJournalStage, normalize_db_host

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

EnvironmentSelector = Union[str, "DevelopmentEnvironment"]

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


class EnvironmentState(enum.StrEnum):
    CREATING = "creating"
    READY = "ready"
    FAILED = "failed"
    REMOVING = "removing"
    CLEANUP_FAILED = "cleanup_failed"
    REMOVED = "removed"


class EnvironmentDatabaseMode(enum.StrEnum):
    SHARED = "shared"
    COPY = "copy"


class DevelopmentEnvironment(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    id: uuid.UUID
    name: str
    repository_root: str
    git_common_dir: str
    branch: str
    base_ref: str
    worktree_path: str
    generated_config_path: str
    python_environment_path: str
    python_environment_owned: bool
    dependency_lock_path: str
    http_interface: str
    http_port: int
    db_mode: EnvironmentDatabaseMode
    source_db_name: str | None = None
    target_db_name: str | None = None
    backup_id: uuid.UUID | None = None
    state: EnvironmentState
    created_at: datetime
    last_used_at: datetime | None = None
    removed_at: datetime | None = None
    last_error: str | None = None


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


_PORT_RANGE_START = 8069
_PORT_RANGE_END = 8099

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
            rev_parse_git_common_dir,
            rev_parse_toplevel,
        )

        repo_root = rev_parse_toplevel(project_path)
        git_common = rev_parse_git_common_dir(repo_root)
        git_common_str = str(git_common)

        self._verify_tools()

        base_ref = options.base_ref or "HEAD"
        from odoo_instance_sdk.internal.git_worktree import rev_parse_verify

        rev_parse_verify(repo_root, base_ref)

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
        http_port = self._allocate_port(options.http_port, project_cfg, catalog, http_interface)

        env_id = uuid.uuid4()
        key = repo_key(repo_root, git_common)
        env_root = get_environments_root(ensure_exists=not dry_run_paths) / key / str(env_id)
        worktree = env_root / "worktree"
        venv = env_root / "venv"
        generated_cfg = env_root / "odoo.conf"
        lock_file = env_root / "requirements.lock"

        if options.create_venv:
            python_path = str(venv)
            python_owned = True
        else:
            python_path = str(python_mode["interpreter"])
            python_owned = False

        name = options.name or f"{repo_root.name}:{branch}"

        now = datetime.now(UTC).isoformat()
        odoo_bin = self._resolve_odoo_bin(options, project_cfg, repo_root)
        runtime_cwd = self._resolve_runtime_cwd(project_cfg, repo_root, worktree)
        dependency_inputs = tuple(
            _rebase_requirement_paths(list(project_cfg.requirements), repo_root, worktree)
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
            worktree_argv=("git", "worktree", "add", str(worktree), branch, base_ref),
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

    def checkout(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> DevelopmentEnvironment:
        plan = self._prepare_checkout(project, branch, options=options, dry_run_paths=False)
        if plan.db_mode is EnvironmentDatabaseMode.COPY:
            self._preflight_copy_checkout(plan)
        catalog = self._client.get_catalog()
        lock_path = provisioning_lock_path()
        with exclusive_lock(lock_path):
            if plan.options.http_port is None:
                plan = replace(
                    plan,
                    http_port=self._allocate_port(None, plan.project, catalog, plan.http_interface),
                )
            self._revalidate_checkout_locked(catalog, plan)
            return self._do_checkout(catalog, plan)

    def _revalidate_checkout_locked(self, catalog: object, plan: _CheckoutPlan) -> None:
        cat = cast("BackupCatalog", catalog)
        existing = cat.active_environment_for(plan.git_common_dir, plan.branch)
        if existing is not None:
            raise EnvironmentConflictError(
                "active_environment_exists",
                f"Active environment already exists for branch {plan.branch!r}",
                details={"branch": plan.branch, "existing_id": existing["id"]},
            )
        allocated = cat.active_environment_for_port(plan.http_port)
        if allocated is not None or not _port_free(plan.http_interface, plan.http_port):
            raise EnvironmentConflictError(
                "port_in_use", f"Port {plan.http_port} is no longer available"
            )

    def _do_checkout(self, catalog: object, plan: _CheckoutPlan) -> DevelopmentEnvironment:
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
            "http_interface": plan.http_interface,
            "http_port": plan.http_port,
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
            worktree_add(plan.repo_root, plan.worktree, plan.branch, base_ref=plan.base_ref)
            created_paths.append(plan.worktree)

            if plan.source_config is not None:
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
                self._run_uv_venv(plan.venv, str(plan.python_selector))
                created_paths.append(plan.venv)

            env_obj = self._get_env_row(cat, plan.env_id)
            with exclusive_lock(python_env_lock_path(env_obj.python_environment_path)):
                self._compile_and_install(env_obj, plan.project, upgrade=False)
                created_paths.append(plan.dependency_lock)

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
            return self._get_env_row(cat, plan.env_id)

        except BaseException as exc:
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
        proc = subprocess.run(cmd, shell=False, capture_output=True, text=True, check=False)
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
        proc = subprocess.run(cmd, shell=False, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise ConfigError(
                f"uv pip {'sync' if env.python_environment_owned else 'install'} failed: "
                f"{proc.stderr.strip()}"
            )

    def get(self, selector: EnvironmentSelector) -> DevelopmentEnvironment:
        if isinstance(selector, DevelopmentEnvironment):
            return selector
        return self._resolve_selector(selector, include_removed=True)

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
        if shutil.which("git") is None:
            raise ConfigError("git not found in PATH")
        if shutil.which("uv") is None:
            raise ConfigError("uv not found in PATH")

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
    ) -> dict[str, object]:
        if options.create_venv:
            return {"mode": "create", "interpreter": None}
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
        return {"mode": "reuse", "interpreter": pybin}

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
    ) -> int:

        cat = cast("BackupCatalog | None", catalog)
        start = requested or project.preferred_http_port or _PORT_RANGE_START
        if requested is not None:
            if cat is not None and cat.active_environment_for_port(requested) is not None:
                raise EnvironmentConflictError(
                    "port_in_use",
                    f"Port {requested} already allocated to an active environment",
                    details={"port": requested},
                )
            return requested
        port = start
        while port <= _PORT_RANGE_END:
            if (cat is None or cat.active_environment_for_port(port) is None) and _port_free(
                http_interface, port
            ):
                return port
            port += 1
        raise EnvironmentConflictError(
            "no_free_port",
            f"No free port in range {_PORT_RANGE_START}-{_PORT_RANGE_END}; pass --http-port",
            details={"range": [str(_PORT_RANGE_START), str(_PORT_RANGE_END)]},
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
        http_interface=str(_get("http_interface")),
        http_port=int(str(_get("http_port"))),
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
        proc = subprocess.run(
            [pybin, "-c", "import sys; print(sys.prefix != sys.base_prefix)"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return False
        return proc.stdout.strip().lower() == "true"
    except (OSError, subprocess.TimeoutExpired):
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
