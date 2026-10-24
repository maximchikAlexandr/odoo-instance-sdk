from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeVar, cast

import msgspec

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import JsonValue, PlanPrecondition
from odoo_instance_sdk.internal.proc import ProcessResult, RunContext
from odoo_instance_sdk.internal.proc.redaction import redacted_projection
from odoo_instance_sdk.internal.sanitize import sanitize_last_error

_ContextT = TypeVar("_ContextT")


class DatabaseDropSession(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Sanitized identity for one active target-database session."""

    pid: int
    user: str
    client: str | None = None
    application: str | None = None


@dataclass(frozen=True, slots=True)
class DropInspection:
    exists: bool
    is_template: bool
    sessions: tuple[DatabaseDropSession, ...]
    database_oid: int | None = None


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def inspection_sql(database: str) -> str:
    literal = sql_literal(database)
    return (
        "SELECT json_build_object("
        f"'exists', EXISTS (SELECT 1 FROM pg_database WHERE datname={literal}),"
        f"'database_oid', (SELECT oid::bigint FROM pg_database WHERE datname={literal}),"
        f"'is_template', COALESCE((SELECT datistemplate FROM pg_database WHERE datname={literal}), false),"
        "'sessions', COALESCE((SELECT json_agg(json_build_object("
        "'pid', pid, 'user', usename, 'client', client_addr::text, "
        "'application', application_name)) FROM pg_stat_activity "
        f"WHERE datname={literal} AND pid <> pg_backend_pid()), '[]'::json)"
        ");"
    )


def terminate_sql(database: str) -> str:
    literal = sql_literal(database)
    return (
        "SELECT count(*) FROM (SELECT pg_terminate_backend(pid) "
        "FROM pg_stat_activity "
        f"WHERE datname={literal} AND pid <> pg_backend_pid()) AS terminated;"
    )


def drop_sql(database: str) -> str:
    return f"DROP DATABASE {quoted_identifier(database)};"


def verify_sql(database: str) -> str:
    literal = sql_literal(database)
    return f"SELECT NOT EXISTS (SELECT 1 FROM pg_database WHERE datname={literal});"


def safe_session_value(value: JsonValue) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError("database safety inspection returned invalid session identity")
    projected = redacted_projection(value, field="error")
    if not isinstance(projected, str):
        raise ConfigError("database safety inspection returned invalid session identity")
    return sanitize_last_error(projected) or None


def session_projection(session: DatabaseDropSession) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", msgspec.to_builtins(session))


def decode_inspection(result: ProcessResult, database: str) -> DropInspection:
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
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("pid"), int)
            or isinstance(raw.get("pid"), bool)
            or raw["pid"] <= 0
        ):
            raise ConfigError("database safety inspection returned invalid session identity")
        sessions.append(
            DatabaseDropSession(
                pid=raw["pid"],
                user=safe_session_value(raw.get("user")) or "unknown",
                client=safe_session_value(raw.get("client")),
                application=safe_session_value(raw.get("application")),
            )
        )
    exists = payload.get("exists")
    is_template = payload.get("is_template")
    if not isinstance(exists, bool) or not isinstance(is_template, bool):
        raise ConfigError("database safety inspection returned invalid preconditions")
    database_oid = payload.get("database_oid")
    if database_oid is not None and (
        not isinstance(database_oid, int) or isinstance(database_oid, bool) or database_oid <= 0
    ):
        raise ConfigError("database safety inspection returned invalid database identity")
    return DropInspection(exists, is_template, tuple(sessions), database_oid)


def decode_verify(result: ProcessResult, database: str) -> bool:
    if result.returncode != 0:
        raise ConfigError(f"database absence verification failed for {database!r}")
    stdout = (
        result.stdout.decode(errors="replace")
        if isinstance(result.stdout, bytes)
        else result.stdout
    )
    return (stdout or "").strip().lower() in {"t", "true", "1"}


def process_result(context: RunContext[_ContextT], step_id: str) -> ProcessResult:
    result = context.process(step_id)
    if not isinstance(result, ProcessResult):
        raise ConfigError(f"database drop step {step_id!r} returned no process result")
    return result


def session_advice(*, command_origin: str | None, force_connections: bool) -> str:
    if force_connections:
        return "will be terminated"
    if command_origin == "env-remove":
        return (
            "require --force-connections (or stop the environment with `odcli stop`) "
            "to terminate only target sessions"
        )
    return "require --force-connections to terminate only target sessions"


def safety_preconditions(
    inspection: DropInspection,
    *,
    database: str,
    project_default: str | None,
    force_default: bool,
    force_connections: bool,
    command_origin: str | None = None,
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
                else (
                    f"{len(inspection.sessions)} active target session(s) "
                    + session_advice(
                        command_origin=command_origin, force_connections=force_connections
                    )
                )
            ),
        ),
    )
