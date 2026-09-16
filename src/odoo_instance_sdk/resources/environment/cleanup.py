from __future__ import annotations

# ruff: noqa: F821
import contextlib
import shutil
import sqlite3
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from odoo_instance_sdk.exceptions import (
    EnvironmentConflictError,
)
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.db_name import validate_filestore_containment
from odoo_instance_sdk.internal.locks import (
    environment_lock_path,
    exclusive_lock,
)
from odoo_instance_sdk.internal.odoo_config import (
    get_admin_passwd,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.models import (
    Backup,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import CopyJournalStage, normalize_db_host

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedCommand,
        PreparedStep,
        ProcessExecutor,
        ProcessResult,
        RunContext,
    )
    from odoo_instance_sdk.models.backup import DevelopmentEnvironment
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from odoo_instance_sdk.resources.environment import helpers as _helpers

globals().update(
    {name: value for name, value in _helpers.__dict__.items() if not name.startswith("__")}
)


class _CleanupMixin:
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
        copy_drop = self._remove_copy_database_command(env, executor=executor)
        if copy_drop is not None:
            steps = (*steps, *copy_drop.steps)
        return self._action_command(
            "environment.remove",
            "Remove the selected development environment",
            lambda: self._remove_impl(env, copy_drop=copy_drop),
            executor=executor,
            mutating=True,
            steps=steps,
            optional_steps=tuple(step.step_id for step in steps),
        )

    def _remove_copy_database_command(  # noqa: C901
        self,
        env: DevelopmentEnvironment,
        *,
        executor: ProcessExecutor | None,
    ) -> PreparedCommand[None] | None:
        """Capture the guarded direct COPY database cleanup before removal starts."""
        if env.db_mode is not EnvironmentDatabaseMode.COPY or env.target_db_name is None:
            return None
        config_path = Path(env.generated_config_path)
        if not config_path.is_file():
            return None
        try:
            from odoo_instance_sdk.internal.pg.drop import build_database_drop_command

            cfg = parse_odoo_config(config_path)
            password = get_admin_passwd(cfg)
            if password is None:
                return None
            instance = self._client.instance.from_config(config_path, master_password=password)
            from odoo_instance_sdk.resources.postgres import PostgresCluster

            instance._postgres_cluster = PostgresCluster.from_project(env.repository_root)
            drop_name = env.target_db_name
            retained = _replacement_retained_error(env.last_error)
            if retained.get("target_present") is False and retained.get("rollback_present") is True:
                rollback_name = retained.get("rollback_database")
                if isinstance(rollback_name, str) and rollback_name:
                    drop_name = rollback_name
            command = build_database_drop_command(
                instance,
                env.repository_root,
                drop_name,
                executor=executor,
                allow_environment_id=str(env.id),
                allow_environment_backup_id=(
                    str(env.backup_id) if env.backup_id is not None else None
                ),
                allow_environment_rollback_database=(
                    drop_name if drop_name != env.target_db_name else None
                ),
                idempotent_absent=True,
            )
            prepared = cast("PreparedCommand[None]", command._prepared())

            def validate_retained_replay() -> None:
                _validate_retained_removal_evidence(self._client.get_catalog(), env)

            if (
                drop_name == env.target_db_name
                and retained.get("target_present") is True
                and retained.get("rollback_present") is True
            ):
                rollback_name = retained.get("rollback_database")
                if not isinstance(rollback_name, str) or not rollback_name:
                    return prepared
                rollback_command = build_database_drop_command(
                    instance,
                    env.repository_root,
                    rollback_name,
                    executor=executor,
                    allow_environment_id=str(env.id),
                    allow_environment_backup_id=(
                        str(env.backup_id) if env.backup_id is not None else None
                    ),
                    allow_environment_rollback_database=rollback_name,
                    idempotent_absent=True,
                    step_prefix="environment.remove.rollback.",
                )
                rollback_prepared = cast("PreparedCommand[None]", rollback_command._prepared())
                from odoo_instance_sdk.internal.proc import prepared_command

                def remove_pair(context: RunContext[None]) -> None:
                    validate_retained_replay()
                    result = prepared.callback(context)
                    rollback_prepared.callback(context)
                    return result

                return prepared_command(
                    remove_pair,
                    (*prepared.steps, *rollback_prepared.steps),
                    executor=executor,
                )
            if retained:

                def remove_retained(context: RunContext[None]) -> None:
                    validate_retained_replay()
                    return prepared.callback(context)

                from odoo_instance_sdk.internal.proc import prepared_command

                return prepared_command(remove_retained, prepared.steps, executor=executor)
            return prepared  # noqa: TRY300
        except Exception as exc:
            reason = sanitize_last_error(str(exc)) or type(exc).__name__
            raise EnvironmentConflictError(
                "copy_drop_plan_failed",
                f"guarded COPY database drop plan construction failed: {reason}",
            ) from exc

    def _remove_impl(
        self, env: DevelopmentEnvironment, *, copy_drop: PreparedCommand[None] | None
    ) -> None:
        from odoo_instance_sdk.internal.proc import active_context

        catalog = self._client.get_catalog()
        with exclusive_lock(environment_lock_path(str(env.id))):
            self._do_remove(
                catalog,
                env,
                context=cast("RunContext[None] | None", active_context()),
                copy_drop=copy_drop,
            )

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
            context.complete_action(step_id)
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

    def _do_remove(  # noqa: C901
        self,
        catalog: BackupCatalog,
        env: DevelopmentEnvironment,
        *,
        context: RunContext[None] | None = None,
        copy_drop: PreparedCommand[None] | None = None,
    ) -> None:
        cat = catalog
        # A removed catalog row is the durable idempotence boundary.  Do not
        # re-probe its released endpoint: a later process may legitimately
        # own that port, while active environments must still pass the
        # fail-closed port preflight below.
        if env.state is EnvironmentState.REMOVED:
            return
        copy_plan = self._preflight_remove(cat, env, context=context)
        _validate_retained_removal_evidence(cat, env)
        cat.update_environment_state(str(env.id), EnvironmentState.REMOVING)
        cat.add_environment_event(str(env.id), "remove", "started")

        env_root = Path(env.worktree_path).parent
        repo_root = Path(env.repository_root)
        worktree = Path(env.worktree_path)
        generated_cfg = Path(env.generated_config_path)
        lock_file = Path(env.dependency_lock_path)
        venv = Path(env.python_environment_path) if env.python_environment_owned else None

        if copy_plan is not None and copy_plan.stage is CopyJournalStage.RESTORE_PENDING:
            msg = "copy restore ownership is unresolved; manual reconciliation is required"
            cat.update_environment_state(
                str(env.id), EnvironmentState.CLEANUP_FAILED, last_error=msg
            )
            cat.add_environment_event(str(env.id), "remove", "failed", message=msg)
            raise EnvironmentConflictError("cleanup_failed", msg)

        cleanup_failed = False
        failures: list[str] = []

        if copy_plan is not None and copy_plan.stage is CopyJournalStage.RESTORED:
            cleanup_failed = (
                self._drop_copy_target(copy_plan, failures, context=context, copy_drop=copy_drop)
                or cleanup_failed
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
            if copy_plan.rollback_filestore is not None:
                rollback_filestore = copy_plan.rollback_filestore
                if rollback_filestore.is_symlink() or not rollback_filestore.is_dir():
                    if rollback_filestore.exists() or rollback_filestore.is_symlink():
                        failures.append("rollback filestore ownership is unresolved")
                        cleanup_failed = True
                else:
                    try:
                        shutil.rmtree(rollback_filestore)
                    except OSError as exc:
                        failures.append(f"rollback filestore delete: {exc}")
                        cleanup_failed = True
                    if rollback_filestore.exists() or rollback_filestore.is_symlink():
                        failures.append("rollback filestore cleanup was not verified")
                        cleanup_failed = True
                if cleanup_failed:
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
        if env.db_mode is not EnvironmentDatabaseMode.COPY and not _port_free(
            env.http_interface, env.http_port
        ):
            raise EnvironmentConflictError(
                "port_in_use",
                f"reserved port {env.http_interface}:{env.http_port} is occupied",
            )
        repo_root = Path(env.repository_root)
        expected_root = (
            _paths.get_environments_root()
            / repo_key(repo_root, Path(env.git_common_dir))
            / str(env.id)
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
        if catalog.get_environment_runtime(str(env.id)) is not None:
            raise EnvironmentConflictError(
                "runtime_active",
                "copy environment has an active owned runtime; stop it before removal",
            )
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
                    if stage is CopyJournalStage.DROPPED and backup_id is not None:
                        row = catalog.get_by_id(str(backup_id))
                        backup = _row_to_backup(row) if row is not None else None
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
        retained = _replacement_retained_error(env.last_error)
        if (
            env.state is EnvironmentState.CLEANUP_FAILED
            and isinstance(env.last_error, str)
            and "copy replacement cleanup_failed" in env.last_error
            and not retained
        ):
            raise EnvironmentConflictError(
                "replacement_conflict", "retained replacement cleanup evidence is malformed"
            )
        if env.state is EnvironmentState.CLEANUP_FAILED and retained:
            expected_backup = str(env.backup_id) if env.backup_id is not None else None
            if (
                retained.get("target_database") != env.target_db_name
                or retained.get("backup_id") != expected_backup
                or not isinstance(retained.get("rollback_database"), str)
                or Path(str(retained["rollback_database"])).name
                != str(retained["rollback_database"])
            ):
                raise EnvironmentConflictError(
                    "replacement_conflict",
                    "retained replacement cleanup evidence does not match environment",
                )
            rollback_database = str(retained["rollback_database"])
            start_config = instance.config.start_config
            data_dir_value = cfg.get("data_dir") or (
                None if start_config is None else start_config.data_dir
            )
            if not data_dir_value:
                raise EnvironmentConflictError(
                    "replacement_conflict", "retained replacement data binding is unavailable"
                )
            rollback_filestore = validate_filestore_containment(
                Path(data_dir_value), rollback_database
            )
            target_present = retained.get("target_present")
            rollback_present = retained.get("rollback_present")
            if rollback_present is True and isinstance(target_present, bool):
                backup_id = uuid.UUID(str(retained["backup_id"]))
                backup_row = catalog.get_by_id(str(backup_id))
                return CopyCleanupPlan(
                    target_database=str(env.target_db_name),
                    backup_id=backup_id,
                    instance=instance,
                    backup=_row_to_backup(backup_row) if backup_row is not None else None,
                    stage=CopyJournalStage.RESTORED,
                    rollback_database=rollback_database,
                    rollback_filestore=rollback_filestore,
                )
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
        copy_drop: PreparedCommand[None] | None = None,
    ) -> bool:
        if plan.stage is CopyJournalStage.RESTORE_PENDING:
            failures.append("copy restore ownership is unresolved")
            return True
        if copy_drop is None or context is None:
            failures.append("guarded direct PostgreSQL drop plan is unavailable")
            return True
        try:
            copy_drop.callback(context)
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
