"""Private, compensating COPY-environment replacement command."""

from __future__ import annotations

import contextlib
import shutil
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import ConfigError, EnvironmentConflictError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.database_preparation import (
    _preparation_process_steps,
    capture_selected_backup_restore,
    materialize_selected_backup_dump,
    materialize_selected_backup_filestore,
)
from odoo_instance_sdk.internal.dbprep.source import (
    _assert_verified_snapshot_unchanged,
    _materialize_verified_snapshot,
)
from odoo_instance_sdk.internal.dbreplace.planning import (
    _CLEANUP_ROLLBACK,
    _CLEANUP_VERIFY,
    _DROP_PARTIAL,
    _DROP_ROLLBACK,
    _INSPECT,
    _MOVE_DATABASE,
    _MOVE_DATABASE_VERIFY,
    _MOVE_FILESTORE,
    _PRE_CLEANUP_VERIFY,
    _PUBLISH,
    _RESET,
    _RESTORE,
    _RESTORE_DATABASE,
    _RESTORE_VERIFY,
    _REVALIDATE,
    _ROOT,
    CopyReplacementFailureContext,
    CopyReplacementPlan,
    _drop_sql,
    _durable_failure_message,
    _exists_sql,
    _inspect,
    _inspect_sql,
    _rename_sql,
    _revalidate,
    _skip_remaining,
    _stdout,
    _step,
    _validate_plan,
    _validate_retained_evidence,
    _verify_database_move,
)
from odoo_instance_sdk.internal.locks import (
    backup_lock_path,
    environment_lock_path,
    exclusive_lock,
    postgres_cluster_lock_path,
)
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    ProcessResult,
    RunContext,
    SubprocessExecutor,
    prepared_command,
)
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.models import (
    CopyReplacementResult,
    DatabaseRefreshOptions,
    DevelopmentEnvironment,
    EnvironmentState,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


def _apply_rename(context: RunContext[None], step_id: str, *, message: str) -> None:
    import odoo_instance_sdk.internal.database_replacement as _replacement_shim

    _replacement_shim._rename(context, step_id, message=message)


def build_copy_replacement_command(  # noqa: C901
    client: OdooClient,
    environment: DevelopmentEnvironment,
    backup_id: uuid.UUID,
    *,
    reset_admin_password: bool = False,
    executor: ProcessExecutor | None = None,
) -> Command[CopyReplacementResult]:
    plan = _validate_plan(
        client,
        environment,
        backup_id,
        reset_admin_password=reset_admin_password,
    )
    process_executor = executor or SubprocessExecutor()
    planning_inspect = _step(
        plan.instance,
        step_id=_INSPECT,
        sql=_inspect_sql(plan.target_database, plan.rollback_database),
    )
    planning_target, planning_rollback, planning_sessions = _inspect(
        cast("ProcessResult", process_executor.execute(planning_inspect)),
        target=plan.target_database,
        rollback=plan.rollback_database,
    )
    _validate_retained_evidence(
        plan,
        client.get_catalog().get_environment(str(plan.environment.id)),
        target_exists=planning_target,
        rollback_exists=planning_rollback,
    )
    retry_from_rollback = plan.retry_from_rollback
    if not planning_target and not (retry_from_rollback and planning_rollback):
        raise EnvironmentConflictError(
            "replacement_conflict", "recorded COPY target database is unavailable"
        )
    if planning_rollback and not retry_from_rollback and not plan.cleanup_only:
        raise EnvironmentConflictError(
            "replacement_conflict", "COPY rollback database identity is already occupied"
        )
    if planning_sessions:
        raise EnvironmentConflictError(
            "active_sessions", "active target database sessions block replacement"
        )
    plan = replace(
        plan,
        planning_database=(planning_target, planning_rollback, planning_sessions),
    )
    # Capture validated archive members only after the immutable identity and
    # execution probes have succeeded.  The public plan contains identities,
    # never a dump or filestore byte payload.
    restore_payload = capture_selected_backup_restore(plan.backup)
    if plan.restore_inputs is None:
        raise ConfigError("selected restore inputs were not captured")
    restore_steps = _preparation_process_steps(
        plan.environment.repository_root,
        options=DatabaseRefreshOptions(restore=True),
        restore_inputs=plan.restore_inputs,
        selected_environment=plan.environment,
        selected_instance=plan.instance,
        selected_restore=restore_payload,
    )
    restore_create, restore_validate, restore_process = restore_steps
    restore_validate_step_id = restore_validate.step_id
    restore_process_step_id = restore_process.step_id

    def discard_restore_dump() -> None:
        for path in (restore_payload.dump_path, restore_payload.verified_snapshot_path):
            with contextlib.suppress(OSError):
                path.unlink()

    steps: tuple[PreparedAction | PreparedStep, ...] = (
        PreparedAction(
            _ROOT, "replace-copy", "Replace a stopped COPY database and filestore", mutating=True
        ),
        PreparedAction(
            "database.replace.validate",
            "Revalidate environment, backup and database ownership",
            read_only=True,
        ),
        _step(
            plan.instance,
            step_id=_INSPECT,
            sql=_inspect_sql(plan.target_database, plan.rollback_database),
        ),
        _step(
            plan.instance,
            step_id=_REVALIDATE,
            sql=_inspect_sql(plan.target_database, plan.rollback_database),
        ),
        _step(
            plan.instance,
            step_id=_MOVE_DATABASE,
            sql=_rename_sql(plan.target_database, plan.rollback_database),
            mutating=True,
        ),
        _step(
            plan.instance,
            step_id=_MOVE_DATABASE_VERIFY,
            sql=_inspect_sql(plan.target_database, plan.rollback_database),
        ),
        PreparedAction(
            _MOVE_FILESTORE, "move prior contained filestore to rollback identity", mutating=True
        ),
        PreparedAction(
            _RESTORE, "restore selected backup into the unchanged target", mutating=True
        ),
        restore_create,
        restore_validate,
        restore_process,
        _step(
            plan.instance,
            step_id="database.restore.exists-after",
            sql=_exists_sql(plan.target_database),
        ),
        PreparedAction(
            _RESTORE_VERIFY, "verify restored database and matching filestore", read_only=True
        ),
        PreparedAction(_RESET, "reset the requested Odoo administrator password", mutating=True),
        *(() if plan.reset_process_step is None else (plan.reset_process_step,)),
        _step(
            plan.instance,
            step_id=_PRE_CLEANUP_VERIFY,
            sql=_inspect_sql(plan.target_database, plan.rollback_database),
        ),
        PreparedAction(_PUBLISH, "publish replacement provenance atomically", mutating=True),
        _step(
            plan.instance, step_id=_DROP_PARTIAL, sql=_drop_sql(plan.target_database), mutating=True
        ),
        _step(
            plan.instance,
            step_id=_DROP_ROLLBACK,
            sql=_drop_sql(plan.rollback_database),
            mutating=True,
        ),
        _step(
            plan.instance,
            step_id=_RESTORE_DATABASE,
            sql=_rename_sql(plan.rollback_database, plan.target_database),
            mutating=True,
        ),
        PreparedAction(_CLEANUP_ROLLBACK, "remove proven rollback artifacts", mutating=True),
        _step(
            plan.instance,
            step_id=_CLEANUP_VERIFY,
            sql=_inspect_sql(plan.target_database, plan.rollback_database),
        ),
    )

    def execute(context: RunContext[CopyReplacementResult]) -> CopyReplacementResult:  # noqa: C901
        context.action(_ROOT)
        cluster = plan.instance._postgres_cluster
        assert cluster is not None
        moved_database = False
        moved_filestore = False
        restored_database = False
        drop_attempted = False
        database_removed = False
        published = False
        rollback_cleanup_started = False
        rollback_database_removed = False
        rollback_filestore_removed = False
        try:
            catalog = plan.client.get_catalog()
            with (
                exclusive_lock(environment_lock_path(str(plan.environment.id))),
                exclusive_lock(backup_lock_path(str(plan.backup.id))),
                exclusive_lock(postgres_cluster_lock_path(cluster._project_id)),
            ):
                context.action("database.replace.validate")
                context.skip(_INSPECT)
                _revalidate(plan, cast("RunContext[None]", context), _REVALIDATE)
                execution_payload = restore_payload
                if not plan.cleanup_only:
                    execution_payload = _materialize_verified_snapshot(restore_payload)
                    _assert_verified_snapshot_unchanged(execution_payload)
                if plan.cleanup_only:
                    published = True
                    restored_database = True
                    _skip_remaining(
                        cast("RunContext[None]", context),
                        (
                            _MOVE_DATABASE,
                            _MOVE_DATABASE_VERIFY,
                            _MOVE_FILESTORE,
                            _RESTORE,
                            "database.replace.restore.create",
                            restore_validate_step_id,
                            restore_process_step_id,
                            "database.restore.exists-after",
                            _RESTORE_VERIFY,
                            _RESET,
                            "instance.shell_script",
                            _PRE_CLEANUP_VERIFY,
                            _PUBLISH,
                            _DROP_PARTIAL,
                            _RESTORE_DATABASE,
                        ),
                    )
                    context.action(_CLEANUP_ROLLBACK)
                    rollback_cleanup_started = True
                    if plan.planning_database[1]:
                        _apply_rename(
                            cast("RunContext[None]", context),
                            _DROP_ROLLBACK,
                            message="rollback database cleanup failed",
                        )
                        rollback_database_removed = True
                    else:
                        context.skip(_DROP_ROLLBACK)
                    if plan.rollback_filestore.exists():
                        shutil.rmtree(plan.rollback_filestore)
                    if plan.rollback_filestore.exists() or not plan.filestore.is_dir():
                        raise ConfigError("rollback filestore cleanup verification failed")  # noqa: TRY301
                    rollback_filestore_removed = True
                    target_exists, rollback_exists, sessions = _inspect(
                        cast("ProcessResult", context.process(_CLEANUP_VERIFY)),
                        target=plan.target_database,
                        rollback=plan.rollback_database,
                    )
                    if not target_exists or rollback_exists or sessions:
                        raise ConfigError("replacement cleanup postconditions failed")  # noqa: TRY301
                    return CopyReplacementResult(
                        backup_id=plan.backup.id,
                        environment_id=plan.environment.id,
                        database=plan.target_database,
                        filestore=str(plan.filestore),
                        admin_password_reset=False,
                    )
                if plan.retry_from_rollback:
                    context.skip(_MOVE_DATABASE)
                    context.skip(_MOVE_DATABASE_VERIFY)
                    moved_database = True
                else:
                    _apply_rename(
                        cast("RunContext[None]", context),
                        _MOVE_DATABASE,
                        message="prior database move failed",
                    )
                    moved_database = True
                    _verify_database_move(
                        cast("RunContext[None]", context),
                        _MOVE_DATABASE_VERIFY,
                        target=plan.target_database,
                        rollback=plan.rollback_database,
                    )
                context.action(_MOVE_FILESTORE)
                if plan.retry_from_rollback:
                    if plan.filestore.is_dir() and not plan.rollback_filestore.exists():
                        if plan.filestore.is_symlink():
                            raise ConfigError("retained prior filestore is unsafe")  # noqa: TRY301
                        plan.filestore.rename(plan.rollback_filestore)
                    elif (
                        not plan.rollback_filestore.is_dir()
                        or plan.rollback_filestore.is_symlink()
                        or plan.filestore.exists()
                    ):
                        raise ConfigError("retained rollback filestore is unsafe")  # noqa: TRY301
                else:
                    if (
                        not plan.filestore.is_dir()
                        or plan.filestore.is_symlink()
                        or plan.rollback_filestore.exists()
                        or plan.rollback_filestore.is_symlink()
                    ):
                        raise ConfigError("prior filestore ownership is unsafe")  # noqa: TRY301
                    plan.filestore.rename(plan.rollback_filestore)
                    if not plan.rollback_filestore.is_dir() or plan.filestore.exists():
                        raise ConfigError("prior filestore move verification failed")  # noqa: TRY301
                moved_filestore = True
                context.action(_RESTORE)
                create_result = cast(
                    "ProcessResult", context.process("database.replace.restore.create")
                )
                if create_result.returncode != 0:
                    raise ConfigError("replacement target database creation failed")  # noqa: TRY301
                restored_database = True
                materialize_selected_backup_dump(execution_payload)
                _assert_verified_snapshot_unchanged(execution_payload)
                if isinstance(restore_validate, PreparedStep):
                    validation_result = cast(
                        "ProcessResult", context.process(restore_validate_step_id)
                    )
                    if validation_result.returncode != 0:
                        raise ConfigError(  # noqa: TRY301
                            "replacement PostgreSQL dump validation failed"
                        )
                else:
                    context.action(restore_validate_step_id)
                    context.complete_action(restore_validate_step_id)
                dump_result = cast("ProcessResult", context.process(restore_process_step_id))
                if dump_result.returncode != 0:
                    raise ConfigError("replacement PostgreSQL restore failed")  # noqa: TRY301
                materialize_selected_backup_filestore(plan.filestore, execution_payload)
                _assert_verified_snapshot_unchanged(execution_payload)
                after = cast("ProcessResult", context.process("database.restore.exists-after"))
                if after.returncode != 0 or _stdout(after).strip().lower() not in {
                    "t",
                    "true",
                    "1",
                }:
                    raise ConfigError("replacement database was not created")  # noqa: TRY301
                context.action(_RESTORE_VERIFY)
                if not plan.filestore.is_dir() or plan.filestore.is_symlink():
                    raise ConfigError("replacement filestore was not created safely")  # noqa: TRY301
                if plan.reset_admin_password:
                    context.action(_RESET)
                    reset_result = cast("ProcessResult", context.process("instance.shell_script"))
                    if reset_result.returncode != 0:
                        detail = sanitize_last_error(_stdout(reset_result))
                        raise ConfigError(  # noqa: TRY301
                            "replacement administrator password reset failed"
                            + (f": {detail}" if detail else "")
                        )
                pre_target, pre_rollback, pre_sessions = _inspect(
                    cast("ProcessResult", context.process(_PRE_CLEANUP_VERIFY)),
                    target=plan.target_database,
                    rollback=plan.rollback_database,
                )
                if (
                    not pre_target
                    or not pre_rollback
                    or pre_sessions
                    or not plan.filestore.is_dir()
                    or plan.filestore.is_symlink()
                ):
                    raise ConfigError("replacement postconditions failed before cleanup")  # noqa: TRY301
                context.action(_PUBLISH)
                catalog._finalize_environment_replacement(
                    str(plan.environment.id),
                    str(plan.backup.id),
                    db_host=cluster.endpoint_host,
                    db_port=cluster.endpoint_port,
                    target_database=plan.target_database,
                    cluster_id=plan.cluster_id,
                    data_directory=str(plan.data_directory),
                )
                published = True
                context.action(_CLEANUP_ROLLBACK)
                if moved_database:
                    rollback_cleanup_started = True
                    _apply_rename(
                        cast("RunContext[None]", context),
                        _DROP_ROLLBACK,
                        message="rollback database cleanup failed",
                    )
                    rollback_database_removed = True
                if moved_filestore:
                    shutil.rmtree(plan.rollback_filestore)
                    if plan.rollback_filestore.exists() or not plan.filestore.is_dir():
                        raise ConfigError(  # noqa: TRY301
                            "rollback filestore cleanup verification failed"
                        )
                    rollback_filestore_removed = True
                target_exists, rollback_exists, sessions = _inspect(
                    cast("ProcessResult", context.process(_CLEANUP_VERIFY)),
                    target=plan.target_database,
                    rollback=plan.rollback_database,
                )
                if not target_exists or rollback_exists or sessions:
                    raise ConfigError("replacement cleanup postconditions failed")  # noqa: TRY301
                discard_restore_dump()
                _skip_remaining(
                    cast("RunContext[None]", context),
                    (_RESET, "instance.shell_script", _DROP_PARTIAL, _RESTORE_DATABASE),
                )
                return CopyReplacementResult(
                    backup_id=plan.backup.id,
                    environment_id=plan.environment.id,
                    database=plan.target_database,
                    filestore=str(plan.filestore),
                    admin_password_reset=plan.reset_admin_password,
                )
        except BaseException as exc:
            # Once rollback cleanup has started, the old pair may have been
            # deleted even when the child reported an error.  Never destroy
            # the only known new pair or roll provenance back to an identity
            # that may no longer exist.
            compensated = not rollback_cleanup_started
            if compensated and restored_database and not drop_attempted:
                drop_attempted = True
                try:
                    _apply_rename(
                        cast("RunContext[None]", context),
                        _DROP_PARTIAL,
                        message="partial database cleanup failed",
                    )
                    database_removed = True
                except Exception:
                    compensated = False
            if compensated and moved_filestore:
                try:
                    if plan.filestore.exists() or plan.filestore.is_symlink():
                        shutil.rmtree(plan.filestore)
                    plan.rollback_filestore.rename(plan.filestore)
                except Exception:
                    compensated = False
            if compensated and moved_database and (not drop_attempted or database_removed):
                try:
                    _apply_rename(
                        cast("RunContext[None]", context),
                        _RESTORE_DATABASE,
                        message="prior database compensation failed",
                    )
                except Exception:
                    compensated = False
            if compensated and published and plan.environment.backup_id is not None:
                try:
                    catalog._rollback_environment_replacement(
                        str(plan.environment.id),
                        str(plan.environment.backup_id),
                        db_host=cluster.endpoint_host,
                        db_port=cluster.endpoint_port,
                        target_database=plan.target_database,
                        cluster_id=plan.cluster_id,
                        data_directory=str(plan.data_directory),
                    )
                except Exception:
                    compensated = False
            failure = CopyReplacementFailureContext(
                backup_id=plan.backup.id,
                previous_backup_id=plan.environment.backup_id,
                target_database=plan.target_database,
                rollback_database=plan.rollback_database if moved_database else None,
                rollback_filestore=plan.rollback_filestore.name if moved_filestore else None,
                cleanup_failed=not compensated,
                published=published,
                target_present=restored_database,
                rollback_present=(None if rollback_database_removed else moved_database),
                rollback_filestore_present=(
                    None if rollback_filestore_removed else moved_filestore
                ),
                stage=(
                    "provenance/cleanup"
                    if published
                    else "restore"
                    if restored_database
                    else "database-move"
                    if moved_database
                    else "preflight"
                ),
            )
            if not compensated:
                message = _durable_failure_message(failure, exc)
                with contextlib.suppress(Exception):
                    catalog = plan.client.get_catalog()
                    catalog.update_environment_state(
                        str(plan.environment.id),
                        EnvironmentState.CLEANUP_FAILED.value,
                        last_error=message,
                    )
                    catalog.add_environment_event(
                        str(plan.environment.id), "sync", "failed", message=message
                    )
            _skip_remaining(
                cast("RunContext[None]", context),
                (
                    _MOVE_DATABASE_VERIFY,
                    _RESTORE,
                    "database.replace.restore.create",
                    restore_validate_step_id,
                    restore_process_step_id,
                    "database.restore.exists-after",
                    _RESTORE_VERIFY,
                    _PRE_CLEANUP_VERIFY,
                    _RESET,
                    "instance.shell_script",
                    _PUBLISH,
                    _DROP_PARTIAL,
                    _DROP_ROLLBACK,
                    _RESTORE_DATABASE,
                    _CLEANUP_ROLLBACK,
                    _CLEANUP_VERIFY,
                ),
            )
            with contextlib.suppress(Exception):
                setattr(exc, "failure_context", failure)
            discard_restore_dump()
            raise

    return Command.from_prepared(
        ExecutionPlan(steps=tuple(item.public_projection() for item in steps)),
        prepared_command(execute, steps, executor=executor or SubprocessExecutor()),
    )


__all__ = [
    "CopyReplacementFailureContext",
    "CopyReplacementPlan",
    "CopyReplacementResult",
    "build_copy_replacement_command",
]
