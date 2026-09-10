"""Private, compensating COPY-environment replacement command."""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import msgspec

from odoo_instance_sdk.exceptions import ConfigError, EnvironmentConflictError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.database_preparation import (
    _assert_verified_snapshot_unchanged,
    _capture_restore_inputs,
    _materialize_verified_snapshot,
    _preparation_process_steps,
    capture_selected_backup_restore,
    materialize_selected_backup_dump,
    materialize_selected_backup_filestore,
)
from odoo_instance_sdk.internal.db_name import validate_db_name, validate_filestore_containment
from odoo_instance_sdk.internal.locks import (
    backup_lock_path,
    environment_lock_path,
    exclusive_lock,
    postgres_cluster_lock_path,
)
from odoo_instance_sdk.internal.odoo_config import parse_db_names, parse_odoo_config
from odoo_instance_sdk.internal.pg.builder import build_psql_specification
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
    Backup,
    DatabaseRefreshOptions,
    DevelopmentEnvironment,
    EnvironmentState,
)
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.resources.instance import OdooInstance


_ROOT = "database.replace"
_INSPECT = "database.replace.inspect"
_REVALIDATE = "database.replace.revalidate"
_MOVE_DATABASE = "database.replace.move-database"
_MOVE_DATABASE_VERIFY = "database.replace.move-database.verify"
_MOVE_FILESTORE = "database.replace.move-filestore"
_RESTORE = "database.replace.restore"
_RESTORE_VERIFY = "database.replace.restore.verify"
_PRE_CLEANUP_VERIFY = "database.replace.pre-cleanup.verify"
_RESET = "database.replace.reset-admin-password"
_PUBLISH = "database.replace.publish-provenance"
_DROP_PARTIAL = "database.replace.compensate.drop-partial"
_DROP_ROLLBACK = "database.replace.cleanup-rollback.database"
_RESTORE_DATABASE = "database.replace.compensate.restore-database"
_CLEANUP_ROLLBACK = "database.replace.cleanup-rollback"
_CLEANUP_VERIFY = "database.replace.cleanup-rollback.verify"


class CopyReplacementFailureContext(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """Bounded identities retained when replacement compensation is incomplete."""

    backup_id: uuid.UUID | None = None
    previous_backup_id: uuid.UUID | None = None
    target_database: str | None = None
    rollback_database: str | None = None
    rollback_filestore: str | None = None
    cleanup_failed: bool = False
    stage: str = "preflight"
    published: bool = False
    target_present: bool | None = None
    rollback_present: bool | None = None
    rollback_filestore_present: bool | None = None


class CopyReplacementResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    backup_id: uuid.UUID
    environment_id: uuid.UUID
    database: str
    filestore: str
    admin_password_reset: bool = False
    rollback_artifacts_removed: bool = True


@dataclass(frozen=True, slots=True)
class CopyReplacementPlan:
    client: OdooClient
    environment: DevelopmentEnvironment
    backup: Backup
    instance: OdooInstance
    target_database: str
    rollback_database: str
    filestore: Path
    rollback_filestore: Path
    cluster_id: str
    data_directory: Path
    reset_admin_password: bool
    reset_process_step: PreparedStep | None = None
    environment_identity: tuple[tuple[str, str | None], ...] = ()
    planning_database: tuple[bool, bool, bool] = (True, False, False)
    generated_config_digest: str = ""
    restore_inputs: tuple[str, Path] | None = None
    retry_from_rollback: bool = False
    cleanup_only: bool = False


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _durable_failure_message(failure: CopyReplacementFailureContext, error: BaseException) -> str:
    retained = {
        "stage": failure.stage,
        "backup_id": None if failure.backup_id is None else str(failure.backup_id),
        "previous_backup_id": (
            None if failure.previous_backup_id is None else str(failure.previous_backup_id)
        ),
        "target_database": failure.target_database,
        "rollback_database": failure.rollback_database,
        "rollback_filestore": (
            None if failure.rollback_filestore is None else Path(failure.rollback_filestore).name
        ),
        "published": failure.published,
        "target_present": failure.target_present,
        "rollback_present": failure.rollback_present,
        "rollback_filestore_present": failure.rollback_filestore_present,
    }
    detail = json.dumps(retained, sort_keys=True, separators=(",", ":"))
    reason = sanitize_last_error(str(error)) or type(error).__name__
    legacy = "" if failure.backup_id is None else f" backup={failure.backup_id}"
    return f"copy replacement cleanup_failed; retained={detail};{legacy} {reason}"[:2000]


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _inspect_sql(database: str, rollback: str) -> str:
    target = _sql_literal(database)
    prior = _sql_literal(rollback)
    return (
        "SELECT json_build_object("
        f"'target_exists', EXISTS (SELECT 1 FROM pg_database WHERE datname={target}),"
        f"'rollback_exists', EXISTS (SELECT 1 FROM pg_database WHERE datname={prior}),"
        "'sessions', COALESCE((SELECT json_agg(json_build_object("
        "'pid', pid)) FROM pg_stat_activity "
        f"WHERE datname={target} AND pid <> pg_backend_pid()), '[]'::json));"
    )


def _exists_sql(database: str) -> str:
    return f"SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname={_sql_literal(database)});"


def _rename_sql(source: str, destination: str) -> str:
    return f"ALTER DATABASE {_identifier(source)} RENAME TO {_identifier(destination)};"


def _drop_sql(database: str) -> str:
    return f"DROP DATABASE IF EXISTS {_identifier(database)};"


def _step(
    instance: OdooInstance,
    *,
    step_id: str,
    sql: str,
    mutating: bool = False,
) -> PreparedStep:
    cluster = instance._postgres_cluster
    if cluster is None:
        raise ConfigError("COPY replacement requires a bound PostgreSQL cluster")
    user = instance.config.db_user or getattr(cluster, "_user", None)
    if user is None:
        raise ConfigError("COPY replacement requires a PostgreSQL user")
    return build_psql_specification(
        step_id=step_id,
        host=cluster.endpoint_host,
        port=cluster.endpoint_port,
        user=user,
        password=instance.config.db_password,
        database="postgres",
        args=("-c", sql),
        _trusted_args=("-t", "-A"),
        timeout=30.0,
        _read_only=not mutating,
        _mutating=mutating,
    ).prepared_step


def _stdout(result: ProcessResult) -> str:
    value = result.stdout
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value or "")


def _inspect(result: ProcessResult, *, target: str, rollback: str) -> tuple[bool, bool, bool]:
    if result.returncode != 0:
        raise ConfigError("COPY replacement PostgreSQL identity inspection failed")
    try:
        payload = json.loads(_stdout(result))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConfigError("COPY replacement PostgreSQL identity inspection was invalid") from exc
    if not isinstance(payload, dict):
        raise ConfigError("COPY replacement PostgreSQL identity inspection was invalid")
    target_exists = payload.get("target_exists")
    rollback_exists = payload.get("rollback_exists")
    sessions = payload.get("sessions")
    if (
        not isinstance(target_exists, bool)
        or not isinstance(rollback_exists, bool)
        or not isinstance(sessions, list)
    ):
        raise ConfigError("COPY replacement PostgreSQL identity inspection was invalid")
    if any(not isinstance(item, dict) or not isinstance(item.get("pid"), int) for item in sessions):
        raise ConfigError("COPY replacement PostgreSQL session identity was invalid")
    return target_exists, rollback_exists, bool(sessions)


def _replacement_backup(catalog: BackupCatalog, backup_id: uuid.UUID) -> Backup:
    projection = catalog._resolve_backup_projection(str(backup_id))
    backup = projection.backup
    if backup.size_bytes <= 0 or not backup.sha256:
        raise ConfigError("catalogue backup has no verified content identity")
    catalog.verify_identity(backup, verify_content=True)
    return backup


def _row_identity(row: sqlite3.Row | None) -> tuple[tuple[str, str | None], ...]:
    if row is None:
        return ()
    keys = getattr(row, "keys", None)
    if not callable(keys):
        return ()
    values = cast("Mapping[str, JsonValue]", row)
    return tuple(
        (str(key), None if values[key] is None else str(values[key]))
        for key in sorted(str(item) for item in keys())
    )


def _retained_failure(row: sqlite3.Row | None) -> dict[str, JsonValue]:
    """Decode only the bounded structured replacement context, if present."""
    if row is None:
        return {}
    try:
        raw = cast("Mapping[str, JsonValue]", row)["last_error"]
    except (KeyError, IndexError, TypeError):
        return {}
    if not isinstance(raw, str) or "retained=" not in raw:
        return {}
    payload = raw.split("retained=", 1)[1].split(";", 1)[0]
    try:
        value = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _validate_retained_evidence(
    plan: CopyReplacementPlan,
    row: sqlite3.Row | None,
    *,
    target_exists: bool,
    rollback_exists: bool,
) -> None:
    """Require durable cleanup evidence to describe the selected topology."""
    if plan.environment.state is not EnvironmentState.CLEANUP_FAILED:
        return
    retained = _retained_failure(row)
    expected = {
        "backup_id": str(plan.backup.id),
        "target_database": plan.target_database,
        "rollback_database": plan.rollback_database,
        "rollback_filestore": plan.rollback_filestore.name,
    }
    if any(retained.get(key) != value for key, value in expected.items()):
        raise EnvironmentConflictError(
            "replacement_conflict", "retained replacement evidence does not match selection"
        )
    if not isinstance(retained.get("published"), bool):
        raise EnvironmentConflictError(
            "replacement_conflict", "retained replacement publication evidence is invalid"
        )
    previous_backup_id = retained.get("previous_backup_id")
    if not isinstance(previous_backup_id, str) or not previous_backup_id:
        raise EnvironmentConflictError(
            "replacement_conflict", "retained replacement provenance evidence is invalid"
        )
    recorded_target = retained.get("target_present")
    recorded_rollback = retained.get("rollback_present")
    if (
        recorded_target is not None
        and recorded_rollback is not None
        and (recorded_target, recorded_rollback) != (target_exists, rollback_exists)
    ):
        raise EnvironmentConflictError(
            "replacement_conflict", "retained replacement database topology changed"
        )
    recorded_filestore = retained.get("rollback_filestore_present")
    if isinstance(recorded_filestore, bool) and recorded_filestore != (
        plan.rollback_filestore.is_dir() and not plan.rollback_filestore.is_symlink()
    ):
        raise EnvironmentConflictError(
            "replacement_conflict", "retained replacement filestore topology changed"
        )


def _restore_binding(
    catalog: BackupCatalog, *, host: str | None, port: int, database: str
) -> tuple[str | None, str | None] | None:
    for row in catalog._list_restore_bindings(host, port):
        if str(row["database_name"]) == database:
            return (
                None if row["cluster_id"] is None else str(row["cluster_id"]),
                None if row["data_directory"] is None else str(row["data_directory"]),
            )
    return None


def _environment_matches(row: sqlite3.Row | None, env: DevelopmentEnvironment) -> bool:
    if row is None:
        return False
    values = cast("Mapping[str, JsonValue]", row)
    try:
        expected_values: tuple[tuple[str, JsonValue], ...] = (
            ("id", str(env.id)),
            ("name", env.name),
            ("repository_root", env.repository_root),
            ("git_common_dir", env.git_common_dir),
            ("branch", env.branch),
            ("base_ref", env.base_ref),
            ("worktree_path", env.worktree_path),
            ("generated_config_path", env.generated_config_path),
            ("python_environment_path", env.python_environment_path),
            ("dependency_lock_path", env.dependency_lock_path),
            ("db_mode", env.db_mode.value),
            ("source_db_name", env.source_db_name),
            ("target_db_name", env.target_db_name),
            ("backup_id", str(env.backup_id) if env.backup_id is not None else None),
            ("state", env.state.value),
        )
        for name, expected in expected_values:
            actual = values[name]
            if expected is None and actual is None:
                continue
            if expected is None or str(actual) != str(expected):
                return False
        return bool(values["python_environment_owned"]) == env.python_environment_owned
    except (KeyError, IndexError, TypeError):
        return False


def _validate_plan(  # noqa: C901
    client: OdooClient,
    environment: DevelopmentEnvironment,
    backup_id: uuid.UUID,
    *,
    reset_admin_password: bool,
) -> CopyReplacementPlan:
    if environment.state not in {EnvironmentState.READY, EnvironmentState.CLEANUP_FAILED}:
        raise EnvironmentConflictError(
            "replacement_conflict", "environment is not an active or retryable COPY environment"
        )
    if environment.removed_at is not None:
        raise EnvironmentConflictError("replacement_conflict", "environment has been removed")
    if environment.db_mode.value != "copy" or not environment.target_db_name:
        raise EnvironmentConflictError(
            "replacement_conflict", "replacement requires a registered COPY environment"
        )
    target = environment.target_db_name
    validate_db_name(target)
    catalog = client.get_catalog()
    if not isinstance(catalog, BackupCatalog):
        raise ConfigError("COPY replacement requires the managed catalogue")
    environment_row = catalog.get_environment(str(environment.id))
    retained = _retained_failure(environment_row)
    if not _environment_matches(environment_row, environment):
        raise EnvironmentConflictError(
            "replacement_conflict", "environment catalogue identity changed"
        )
    if environment.backup_id is None:
        raise EnvironmentConflictError(
            "replacement_conflict", "COPY environment has no previous backup provenance"
        )
    backup = _replacement_backup(catalog, backup_id)
    if not backup.filestore_requested:
        raise ConfigError("replacement backup does not contain a filestore")
    restore_inputs = _capture_restore_inputs(
        environment.repository_root,
        DatabaseRefreshOptions(restore=True),
        client=client,
        target_database=target,
        selected_environment=environment,
    )
    config_path = Path(environment.generated_config_path)
    if not config_path.is_file():
        raise ConfigError("generated environment config is unavailable")
    config = parse_odoo_config(config_path)
    if parse_db_names(config.get("db_name")) != (target,):
        raise EnvironmentConflictError(
            "replacement_conflict", "generated config target does not match environment"
        )
    admin_password = config.get("admin_passwd")
    if not admin_password:
        raise ConfigError("generated config has no local database administrator credential")
    instance = client.instance.from_environment(environment)
    instance.databases.master_password = admin_password
    if tuple(instance.config.configured_database_names) != (target,):
        raise EnvironmentConflictError(
            "replacement_conflict", "instance database binding does not match environment"
        )
    reset_process_step: PreparedStep | None = None
    if reset_admin_password:
        from odoo_instance_sdk.resources.database import _RESET_ADMIN_PASSWORD_SCRIPT
        from odoo_instance_sdk.resources.instance import _build_shell_script_step

        start_config = instance.config.start_config
        if start_config is None:
            raise ConfigError("replacement admin reset has no generated start configuration")
        reset_process_step, _, _, _ = _build_shell_script_step(
            start_config,
            executable_prefix=instance._executable_prefix(),
            default_cwd=instance.config.default_cwd,
            source=_RESET_ADMIN_PASSWORD_SCRIPT,
            commit=True,
            project_environment=instance.config.project_environment,
        )
    if instance._postgres_cluster is None or not instance._postgres_cluster.owned:
        raise ConfigError("COPY replacement requires an SDK-owned PostgreSQL cluster")
    cluster_id, _ = instance._postgres_cluster._restore_provenance()
    if cluster_id is None:
        raise ConfigError("PostgreSQL cluster ownership evidence is unavailable")
    previous = catalog.latest_restore_provenance(
        instance._postgres_cluster.endpoint_host,
        instance._postgres_cluster.endpoint_port,
        target,
    )
    if previous is None or previous.id != environment.backup_id:
        raise EnvironmentConflictError(
            "replacement_conflict", "COPY database provenance does not match the environment"
        )
    data_dir_value = config.get("data_dir") or getattr(
        instance.config.start_config, "data_dir", None
    )
    if not data_dir_value:
        raise ConfigError("generated config has no contained filestore root")
    data_dir = Path(data_dir_value)
    if not data_dir.is_absolute():
        data_dir = (config_path.parent / data_dir).resolve()
    else:
        data_dir = data_dir.resolve()
    filestore = validate_filestore_containment(data_dir, target)
    binding = _restore_binding(
        catalog,
        host=instance._postgres_cluster.endpoint_host,
        port=instance._postgres_cluster.endpoint_port,
        database=target,
    )
    if binding != (cluster_id, str(data_dir)):
        raise EnvironmentConflictError(
            "replacement_conflict", "COPY database/filestore provenance does not match cluster"
        )
    rollback_database = f"{target[:34]}_odcli_rb_{environment.id.hex[:20]}"
    validate_db_name(rollback_database)
    rollback_filestore = filestore.with_name(rollback_database)
    retry_from_rollback = environment.state is EnvironmentState.CLEANUP_FAILED and (
        (
            not filestore.exists()
            and rollback_filestore.is_dir()
            and not rollback_filestore.is_symlink()
        )
        or (filestore.is_dir() and not filestore.is_symlink() and not rollback_filestore.exists())
    )
    cleanup_only = (
        environment.state is EnvironmentState.CLEANUP_FAILED
        and retained.get("published") is True
        and filestore.is_dir()
        and not filestore.is_symlink()
        and (not rollback_filestore.exists() or rollback_filestore.is_dir())
    )
    if (
        filestore.is_symlink()
        or (rollback_filestore.exists() and not retry_from_rollback and not cleanup_only)
        or rollback_filestore.is_symlink()
    ):
        raise ConfigError("COPY filestore ownership or rollback identity is unsafe")
    if not filestore.is_dir() and not retry_from_rollback:
        raise ConfigError("prior COPY filestore is unavailable")
    environment_row = catalog.get_environment(str(environment.id))
    environment_identity = _row_identity(environment_row)
    if not environment_identity:
        raise EnvironmentConflictError(
            "replacement_conflict", "environment catalogue identity unavailable"
        )
    return CopyReplacementPlan(
        client=client,
        environment=environment,
        backup=backup,
        instance=instance,
        target_database=target,
        rollback_database=rollback_database,
        filestore=filestore,
        rollback_filestore=rollback_filestore,
        cluster_id=cluster_id,
        data_directory=data_dir,
        reset_admin_password=reset_admin_password,
        reset_process_step=reset_process_step,
        environment_identity=environment_identity,
        generated_config_digest=_file_digest(config_path),
        restore_inputs=restore_inputs,
        retry_from_rollback=retry_from_rollback,
        cleanup_only=cleanup_only,
    )


def _revalidate(  # noqa: C901
    plan: CopyReplacementPlan, context: RunContext[None], step_id: str
) -> None:
    catalog = plan.client.get_catalog()
    row = catalog.get_environment(str(plan.environment.id))
    if (
        not _environment_matches(row, plan.environment)
        or _row_identity(row) != plan.environment_identity
    ):
        raise EnvironmentConflictError(
            "replacement_conflict", "environment identity changed before replacement"
        )
    if catalog.get_environment_runtime(str(plan.environment.id)) is not None:
        raise EnvironmentConflictError(
            "runtime_active", "replacement requires a stopped environment runtime"
        )
    current_backup = _replacement_backup(catalog, plan.backup.id)
    if current_backup != plan.backup:
        raise EnvironmentConflictError(
            "replacement_conflict", "selected backup identity changed before replacement"
        )
    cluster = plan.instance._postgres_cluster
    if cluster is None or not cluster.owned:
        raise ConfigError("PostgreSQL cluster ownership evidence is unavailable")
    current_cluster_id, _ = cluster._restore_provenance()
    if current_cluster_id != plan.cluster_id:
        raise EnvironmentConflictError(
            "replacement_conflict", "PostgreSQL cluster ownership changed before replacement"
        )
    previous = catalog.latest_restore_provenance(
        cluster.endpoint_host, cluster.endpoint_port, plan.target_database
    )
    if previous is None or previous.id != plan.environment.backup_id:
        raise EnvironmentConflictError(
            "replacement_conflict", "COPY database provenance changed before replacement"
        )
    config_path = Path(plan.environment.generated_config_path)
    if not config_path.is_file() or _file_digest(config_path) != plan.generated_config_digest:
        raise EnvironmentConflictError(
            "replacement_conflict", "generated environment config changed before replacement"
        )
    config = parse_odoo_config(config_path)
    if parse_db_names(config.get("db_name")) != (plan.target_database,):
        raise EnvironmentConflictError(
            "replacement_conflict", "generated config target changed before replacement"
        )
    data_dir_value = config.get("data_dir") or getattr(
        plan.instance.config.start_config, "data_dir", None
    )
    if not data_dir_value:
        raise ConfigError("generated config lost its contained filestore root")
    current_data_dir = Path(data_dir_value)
    if not current_data_dir.is_absolute():
        current_data_dir = (config_path.parent / current_data_dir).resolve()
    else:
        current_data_dir = current_data_dir.resolve()
    if current_data_dir != plan.data_directory:
        raise EnvironmentConflictError(
            "replacement_conflict", "generated filestore binding changed before replacement"
        )
    binding = _restore_binding(
        catalog,
        host=cluster.endpoint_host,
        port=cluster.endpoint_port,
        database=plan.target_database,
    )
    if binding != (plan.cluster_id, str(plan.data_directory)):
        raise EnvironmentConflictError(
            "replacement_conflict", "COPY database/filestore provenance changed"
        )
    if tuple(plan.instance.config.configured_database_names) != (plan.target_database,):
        raise EnvironmentConflictError(
            "replacement_conflict", "instance database binding changed before replacement"
        )
    if plan.cleanup_only:
        filestore_stale = (
            not plan.filestore.is_dir()
            or plan.filestore.is_symlink()
            or (plan.rollback_filestore.exists() and not plan.rollback_filestore.is_dir())
            or plan.rollback_filestore.is_symlink()
        )
    elif plan.retry_from_rollback:
        filestore_stale = (
            plan.filestore.exists()
            or plan.filestore.is_symlink()
            or not plan.rollback_filestore.is_dir()
            or plan.rollback_filestore.is_symlink()
        )
    else:
        filestore_stale = (
            plan.filestore.is_symlink()
            or not plan.filestore.is_dir()
            or plan.rollback_filestore.exists()
            or plan.rollback_filestore.is_symlink()
        )
    if filestore_stale:
        raise EnvironmentConflictError(
            "replacement_conflict", "COPY filestore ownership changed before replacement"
        )
    target_exists, rollback_exists, sessions = _inspect(
        cast("ProcessResult", context.process(step_id)),
        target=plan.target_database,
        rollback=plan.rollback_database,
    )
    _validate_retained_evidence(
        plan,
        row,
        target_exists=target_exists,
        rollback_exists=rollback_exists,
    )
    if plan.cleanup_only:
        if not target_exists or sessions:
            raise EnvironmentConflictError(
                "replacement_conflict", "published replacement cleanup identity is stale"
            )
    elif plan.retry_from_rollback:
        if target_exists or not rollback_exists:
            raise EnvironmentConflictError(
                "replacement_conflict", "retained replacement rollback identity is stale"
            )
    elif not target_exists or rollback_exists:
        raise EnvironmentConflictError(
            "replacement_conflict", "COPY replacement database identity is stale"
        )
    if sessions:
        raise EnvironmentConflictError(
            "active_sessions", "active target database sessions block replacement"
        )


def _rename(context: RunContext[None], step_id: str, *, message: str) -> None:
    result = cast("ProcessResult", context.process(step_id))
    if result.returncode != 0:
        raise ConfigError(message)


def _verify_database_move(
    context: RunContext[None], step_id: str, *, target: str, rollback: str
) -> None:
    target_exists, rollback_exists, sessions = _inspect(
        cast("ProcessResult", context.process(step_id)), target=target, rollback=rollback
    )
    if target_exists or not rollback_exists or sessions:
        raise ConfigError("prior database move verification failed")


def _skip_remaining(context: RunContext[None], step_ids: tuple[str, ...]) -> None:
    for step_id in step_ids:
        if context.planned(step_id) and not context.consumed(step_id):
            context.skip(step_id)


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
                        _rename(
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
                    _rename(
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
                    _rename(
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
                    _rename(
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
                    _rename(
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
