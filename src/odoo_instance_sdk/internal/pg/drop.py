"""CLI-private, fail-closed PostgreSQL database deletion operation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import msgspec

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import (
    Command,
    ExecutionPlan,
    JsonValue,
    PlanningInspectionObservation,
    PlanPrecondition,
    SemanticPlanObservation,
)
from odoo_instance_sdk.internal.db_name import validate_db_name
from odoo_instance_sdk.internal.pg.builder import build_psql_specification
from odoo_instance_sdk.internal.pg.context import DatabaseContext, resolve_database_context
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

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance


_DENIED_DATABASES = frozenset({"postgres", "template0", "template1"})
_ROOT_STEP = "database.drop"
_PLANNING_INSPECT_STEP = "database.drop.planning-inspect"
_INSPECT_STEP = "database.drop.inspect"
_REVALIDATE_TERMINATE_STEP = "database.drop.revalidate-terminate"
_TERMINATE_STEP = "database.drop.terminate"
_REVALIDATE_DROP_STEP = "database.drop.revalidate-drop"
_DROP_STEP = "database.drop.execute"
_VERIFY_STEP = "database.drop.verify"
_ContextT = TypeVar("_ContextT")


class DatabaseDropSession(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Sanitized identity for one active target-database session."""

    pid: int
    user: str
    client: str | None = None
    application: str | None = None


class DatabaseDropResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Secret-free result of a verified cluster-bound database deletion."""

    database: str
    cluster: str
    active_sessions: tuple[DatabaseDropSession, ...] = ()
    terminated_sessions: int = 0
    dropped: bool = True


@dataclass(frozen=True, slots=True)
class _DropInspection:
    exists: bool
    is_template: bool
    sessions: tuple[DatabaseDropSession, ...]


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _inspection_sql(database: str) -> str:
    literal = _sql_literal(database)
    return (
        "SELECT json_build_object("
        f"'exists', EXISTS (SELECT 1 FROM pg_database WHERE datname={literal}),"
        f"'is_template', COALESCE((SELECT datistemplate FROM pg_database WHERE datname={literal}), false),"
        "'sessions', COALESCE((SELECT json_agg(json_build_object("
        "'pid', pid, 'user', usename, 'client', client_addr::text, "
        "'application', application_name)) FROM pg_stat_activity "
        f"WHERE datname={literal} AND pid <> pg_backend_pid()), '[]'::json)"
        ");"
    )


def _terminate_sql(database: str) -> str:
    literal = _sql_literal(database)
    return (
        "SELECT count(*) FROM (SELECT pg_terminate_backend(pid) "
        "FROM pg_stat_activity "
        f"WHERE datname={literal} AND pid <> pg_backend_pid()) AS terminated;"
    )


def _drop_sql(database: str) -> str:
    return f"DROP DATABASE {_quoted_identifier(database)};"


def _verify_sql(database: str) -> str:
    literal = _sql_literal(database)
    return f"SELECT NOT EXISTS (SELECT 1 FROM pg_database WHERE datname={literal});"


def _safe_session_value(value: JsonValue) -> str | None:
    if value is None:
        return None
    value = sanitize_last_error(str(value))
    return value or None


def _decode_inspection(result: ProcessResult, database: str) -> _DropInspection:
    if result.returncode != 0:
        raise ConfigError(f"database safety inspection failed for {database!r}")
    stdout = (
        result.stdout.decode(errors="replace")
        if isinstance(result.stdout, bytes)
        else result.stdout
    )
    try:
        payload = json.loads(stdout or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConfigError("database safety inspection returned invalid data") from exc
    if not isinstance(payload, dict):
        raise ConfigError("database safety inspection returned invalid data")
    raw_sessions = payload.get("sessions", [])
    if not isinstance(raw_sessions, list):
        raise ConfigError("database safety inspection returned invalid sessions")
    sessions: list[DatabaseDropSession] = []
    for raw in raw_sessions:
        if not isinstance(raw, dict) or not isinstance(raw.get("pid"), int):
            raise ConfigError("database safety inspection returned invalid session identity")
        sessions.append(
            DatabaseDropSession(
                pid=raw["pid"],
                user=_safe_session_value(raw.get("user")) or "unknown",
                client=_safe_session_value(raw.get("client")),
                application=_safe_session_value(raw.get("application")),
            )
        )
    exists = payload.get("exists")
    is_template = payload.get("is_template")
    if not isinstance(exists, bool) or not isinstance(is_template, bool):
        raise ConfigError("database safety inspection returned invalid preconditions")
    return _DropInspection(exists, is_template, tuple(sessions))


def _decode_verify(result: ProcessResult, database: str) -> bool:
    if result.returncode != 0:
        raise ConfigError(f"database absence verification failed for {database!r}")
    stdout = (
        result.stdout.decode(errors="replace")
        if isinstance(result.stdout, bytes)
        else result.stdout
    )
    return (stdout or "").strip().lower() in {"t", "true", "1"}


def _process_result(context: RunContext[_ContextT], step_id: str) -> ProcessResult:
    result = context.process(step_id)
    if not isinstance(result, ProcessResult):
        raise ConfigError(f"database drop step {step_id!r} returned no process result")
    return result


def _safety_preconditions(
    inspection: _DropInspection,
    *,
    database: str,
    project_default: str | None,
    force_default: bool,
    force_connections: bool,
) -> tuple[PlanPrecondition, ...]:
    default_target = project_default == database
    return (
        PlanPrecondition(
            name="target exists",
            status="passed" if inspection.exists else "failed",
            detail=("target database exists" if inspection.exists else "target database is absent"),
        ),
        PlanPrecondition(
            name="target is not a template",
            status="failed" if inspection.is_template else "passed",
            detail=(
                "pg_database.datistemplate is true"
                if inspection.is_template
                else "target is not a template"
            ),
        ),
        PlanPrecondition(
            name="configured default protection",
            status="passed" if not default_target or force_default else "failed",
            detail=(
                "target is configured project default and --force-default is required"
                if default_target and not force_default
                else "target is not the configured project default"
                if not default_target
                else "configured project default is explicitly forced"
            ),
        ),
        PlanPrecondition(
            name="active target sessions",
            status="passed" if not inspection.sessions or force_connections else "failed",
            detail=(
                "no active target sessions"
                if not inspection.sessions
                else f"{len(inspection.sessions)} active target session(s) require --force-connections"
                if not force_connections
                else f"{len(inspection.sessions)} active target session(s) will be terminated"
            ),
        ),
    )


def _assert_safe(
    inspection: _DropInspection,
    *,
    database: str,
    project_default: str | None,
    force_default: bool,
    force_connections: bool,
    require_no_sessions: bool = False,
) -> None:
    validate_db_name(database)
    if database in _DENIED_DATABASES:
        raise ConfigError(f"database {database!r} is protected and cannot be dropped")
    if not inspection.exists:
        raise ConfigError(f"database {database!r} does not exist on the bound cluster")
    if inspection.is_template:
        raise ConfigError(f"database {database!r} is a template database and cannot be dropped")
    if project_default == database and not force_default:
        raise ConfigError("configured project default requires --force-default")
    if inspection.sessions and (require_no_sessions or not force_connections):
        raise ConfigError(
            f"database {database!r} has {len(inspection.sessions)} active session(s); "
            "pass --force-connections to terminate only target sessions"
        )


def _inspect_command_step(
    binding: DatabaseContext,
    *,
    database: str,
    step_id: str,
    timeout: float,
    mutating: bool = False,
    sql: str | None = None,
) -> PreparedStep:
    specification = build_psql_specification(
        host=binding.host,
        port=binding.port,
        user=binding.user,
        password=binding.password,
        database="postgres",
        stdin=(sql or _inspection_sql(database)).encode(),
        timeout=timeout,
        mode="captured",
        step_id=step_id,
        _trusted_args=("-q", "-t", "-A", "-v", "ON_ERROR_STOP=1"),
        _read_only=not mutating,
        _mutating=mutating,
    )
    return specification.prepared_step


def build_database_drop_command(  # noqa: C901
    instance: OdooInstance,
    project_root: str | Path,
    database_name: str,
    *,
    force_default: bool = False,
    force_connections: bool = False,
    timeout: float = 30.0,
    executor: ProcessExecutor | None = None,
) -> Command[DatabaseDropResult]:
    """Build and inspect one exact project-cluster drop command."""
    database = database_name
    if isinstance(database, str) and database != database.strip():
        raise ConfigError(
            "database drop requires an exact name without leading or trailing whitespace"
        )
    validate_db_name(database)
    if database in _DENIED_DATABASES:
        raise ConfigError(f"database {database!r} is protected and cannot be dropped")
    if timeout <= 0:
        raise ConfigError("database drop timeout must be greater than zero")

    project_path = Path(project_root).resolve()
    from odoo_instance_sdk.project import ProjectConfig

    project = ProjectConfig.load(project_path)
    binding = resolve_database_context(instance, explicit=database, project=project)
    cluster = binding.cluster
    if cluster is None:
        raise ConfigError("database drop requires the resolved project's PostgreSQL cluster")
    process_executor = executor or SubprocessExecutor()
    planning_step = _inspect_command_step(
        binding, database=database, step_id=_PLANNING_INSPECT_STEP, timeout=timeout
    )
    planning_result = process_executor.execute(planning_step)
    if not isinstance(planning_result, ProcessResult):
        raise ConfigError("database safety inspection returned no process result")
    inspection = _decode_inspection(planning_result, database)
    project_default = project.default_source_database
    preconditions = _safety_preconditions(
        inspection,
        database=database,
        project_default=project_default,
        force_default=force_default,
        force_connections=force_connections,
    )
    semantic = SemanticPlanObservation(
        kind="semantic",
        goal=f"Drop database {database}",
        targets=(f"database={database}", f"cluster={cluster.endpoint}"),
        mutations=(
            "terminate target sessions" if force_connections else "drop database",
            "DROP DATABASE via maintenance database postgres",
            "record dropped database after absence verification",
        ),
        preconditions=preconditions,
        warnings=(
            (f"{len(inspection.sessions)} active target session(s) require force",)
            if inspection.sessions and not force_connections
            else ()
        ),
    )
    planning_observation = PlanningInspectionObservation(
        kind="planning-inspection",
        scope=f"database={database}",
        step_ids=(_PLANNING_INSPECT_STEP,),
        budget_seconds=timeout,
        read_only=True,
        executed_during_planning=True,
    )

    inspect_step = _inspect_command_step(
        binding, database=database, step_id=_INSPECT_STEP, timeout=timeout
    )

    revalidate_terminate_step = _inspect_command_step(
        binding,
        database=database,
        step_id=_REVALIDATE_TERMINATE_STEP,
        timeout=timeout,
    )
    terminate_step = _inspect_command_step(
        binding,
        database=database,
        step_id=_TERMINATE_STEP,
        timeout=timeout,
        mutating=True,
        sql=_terminate_sql(database),
    )
    revalidate_drop_step = _inspect_command_step(
        binding,
        database=database,
        step_id=_REVALIDATE_DROP_STEP,
        timeout=timeout,
    )
    drop_step = _inspect_command_step(
        binding,
        database=database,
        step_id=_DROP_STEP,
        timeout=timeout,
        mutating=True,
        sql=_drop_sql(database),
    )
    verify_step = _inspect_command_step(
        binding,
        database=database,
        step_id=_VERIFY_STEP,
        timeout=timeout,
        sql=_verify_sql(database),
    )
    prepared_steps: tuple[PreparedAction | PreparedStep, ...] = (
        PreparedAction(
            step_id=_ROOT_STEP,
            action="drop-project-database",
            description="Drop one exact database from the bound project cluster",
            mutating=True,
        ),
        inspect_step,
        revalidate_terminate_step,
        terminate_step,
        revalidate_drop_step,
        drop_step,
        verify_step,
    )

    def current_default() -> str | None:
        return ProjectConfig.load(project_path).default_source_database

    def execute(context: RunContext[DatabaseDropResult]) -> DatabaseDropResult:
        context.action(_ROOT_STEP)
        current_project_default = current_default()
        planned = _decode_inspection(_process_result(context, _INSPECT_STEP), database)
        _assert_safe(
            planned,
            database=database,
            project_default=current_project_default,
            force_default=force_default,
            force_connections=force_connections,
        )
        terminated = 0
        if planned.sessions and force_connections:
            checked = _decode_inspection(
                _process_result(context, _REVALIDATE_TERMINATE_STEP), database
            )
            _assert_safe(
                checked,
                database=database,
                project_default=current_default(),
                force_default=force_default,
                force_connections=True,
            )
            terminate_result = _process_result(context, _TERMINATE_STEP)
            if terminate_result.returncode != 0:
                raise ConfigError("target session termination failed; database was not dropped")
            terminated = len(checked.sessions)
        else:
            context.skip(_REVALIDATE_TERMINATE_STEP)
            context.skip(_TERMINATE_STEP)
        checked = _decode_inspection(_process_result(context, _REVALIDATE_DROP_STEP), database)
        _assert_safe(
            checked,
            database=database,
            project_default=current_default(),
            force_default=force_default,
            force_connections=force_connections,
            require_no_sessions=True,
        )
        drop_result = _process_result(context, _DROP_STEP)
        if drop_result.returncode != 0:
            raise ConfigError(f"DROP DATABASE failed for {database!r}")
        verified = _decode_verify(_process_result(context, _VERIFY_STEP), database)
        if not verified:
            raise ConfigError(f"database {database!r} still exists after drop")
        catalog = instance._client.get_catalog()
        catalog.record_database_dropped(binding.host, binding.port, database)
        return DatabaseDropResult(
            database=database,
            cluster=cluster.endpoint,
            active_sessions=planned.sessions,
            terminated_sessions=terminated,
        )

    plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in prepared_steps),
        observations=(semantic, planning_observation),
    )
    return Command.from_prepared(
        plan,
        prepared_command(execute, prepared_steps, executor=process_executor),
    )


__all__ = ["DatabaseDropResult", "DatabaseDropSession", "build_database_drop_command"]
