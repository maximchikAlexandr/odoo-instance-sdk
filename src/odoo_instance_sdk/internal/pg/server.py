"""Bounded, failure-tolerant PostgreSQL server status enrichment."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar, cast

from odoo_instance_sdk.execution import JsonValue, deadline_bound_attempt_observation
from odoo_instance_sdk.internal.proc import MIN_PROCESS_TIMEOUT, ExecutionDeadline
from odoo_instance_sdk.models import PostgresServerInfo, ServerUnavailabilityReason

from .builder import build_psql_specification, resolve_psql_executable
from .diagnostics import decode_typed_json, load_sql_asset, validate_timeout

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RunContext

SERVER_SUMMARY_SQL_VERSION = 1
SERVER_SUMMARY_SQL = load_sql_asset("server_summary_v1.sql")
SERVER_SUMMARY_TIMEOUT = 10.0
_SQLSTATE = re.compile(r"(?<![0-9A-Z])[0-9A-Z]{5}(?![0-9A-Z])")

ServerExecutorCategory = Literal[
    "missing_tool", "credentials_missing", "connection", "timeout", "query", "decode"
]
_ContextT = TypeVar("_ContextT")


class _ServerCluster(Protocol):
    @property
    def mode(self) -> str: ...

    @property
    def owned(self) -> bool: ...

    @property
    def endpoint_host(self) -> str: ...

    @property
    def endpoint_port(self) -> int: ...


@dataclass(frozen=True, slots=True)
class ServerSummary:
    """The public-safe result of server enrichment."""

    server: PostgresServerInfo | None
    reason: ServerUnavailabilityReason | None


def classify_server_failure(
    sqlstate: str | None = None,
    executor_category: ServerExecutorCategory | None = None,
) -> ServerUnavailabilityReason:
    """Map stable typed process/SQLSTATE categories to a closed public reason."""
    if executor_category == "missing_tool":
        return "psql_missing"
    if executor_category == "credentials_missing":
        return "credentials_missing"
    if executor_category == "timeout":
        return "timeout"
    if executor_category == "decode":
        return "invalid_response"
    state = (sqlstate or "").upper()
    if state.startswith("28"):
        return "authentication_failed"
    if state.startswith("08"):
        return "server_unreachable"
    if state in {"3D000", "42501"}:
        return "maintenance_database_unavailable" if state == "3D000" else "privilege_denied"
    return "query_failed"


def _continuable(sqlstate: str | None) -> bool:
    return (sqlstate or "").upper() in {"3D000", "42501"}


def decode_server_summary(stdout: str | bytes) -> PostgresServerInfo:
    """Decode the sole JSON value emitted by the summary statement."""
    return decode_typed_json(stdout, PostgresServerInfo, "postgres server summary")


def maintenance_database_candidates(
    generated_database: str | None = None, project_default: str | None = None
) -> tuple[str, ...]:
    """Return the deterministic, de-duplicated maintenance candidate list."""
    first = (generated_database or "").strip() or (project_default or "").strip()
    candidates = [name for name in (first, "postgres", "template1") if name]
    return tuple(dict.fromkeys(candidates))


def build_server_summary_steps(
    cluster: _ServerCluster, *, timeout: float = SERVER_SUMMARY_TIMEOUT
) -> tuple[PreparedStep, ...]:
    """Capture all candidate summary processes for one status command."""
    budget = validate_timeout(timeout)
    host, port, user, password = _credentials(cluster)
    executable = resolve_psql_executable()
    if not user or executable is None:
        return ()
    generated, project_default = _project_database(cluster)
    candidates = maintenance_database_candidates(generated, project_default)
    return tuple(
        build_psql_specification(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            args=("-v", "VERBOSITY=sqlstate", "-c", SERVER_SUMMARY_SQL),
            _trusted_args=("-q", "-A", "-t"),
            timeout=budget,
            step_id=f"postgres.status.server-summary.{index}",
            _executable=executable,
            _environment_values=(("LC_ALL", "C"), ("LANG", "C")),
            _redact_database=True,
        ).prepared_step
        for index, database in enumerate(candidates)
    )


def server_summary_deadline_observation(
    steps: Sequence[PreparedStep], *, timeout: float = SERVER_SUMMARY_TIMEOUT
) -> JsonValue:
    """Describe the shared attempt boundary in the public execution plan."""
    return deadline_bound_attempt_observation(
        scope="postgres.status.server-summary",
        step_ids=tuple(step.step_id for step in steps),
        budget_seconds=validate_timeout(timeout),
    )


def _project_database(cluster: _ServerCluster) -> tuple[str | None, str | None]:
    root = getattr(cluster, "_repository_root", None)
    if root is None:
        return None, None
    try:
        from odoo_instance_sdk.project import ProjectConfig

        config = ProjectConfig.load(Path(root).resolve())
    except Exception:
        return None, None
    else:
        generated: str | None = None
        if config.source_config is not None:
            from odoo_instance_sdk.models import StartConfig

            generated = StartConfig.from_odoo_config(config.source_config).db_name
        return generated, config.default_source_database


def _credentials(cluster: _ServerCluster) -> tuple[str | None, int, str | None, str | None]:
    host = cast("str | None", getattr(cluster, "endpoint_host", None))
    port = cast("int", getattr(cluster, "endpoint_port", 5432))
    user = cast("str | None", getattr(cluster, "_user", None))
    password = cast("str | None", getattr(cluster, "_password", None))
    if user is None and getattr(cluster, "mode", None) == "external":
        root = getattr(cluster, "_repository_root", None)
        if root is not None:
            try:
                from odoo_instance_sdk.models import StartConfig
                from odoo_instance_sdk.project import ProjectConfig

                config = ProjectConfig.load(Path(root).resolve())
                if config.source_config is not None:
                    source = StartConfig.from_odoo_config(config.source_config)
                    user, password = source.db_user, source.db_password
            except Exception:
                pass
    if password is None and getattr(cluster, "owned", False):
        password_file = getattr(cluster, "password_file", None)
        if isinstance(password_file, Path) and password_file.is_file():
            try:
                password = password_file.read_text(encoding="utf-8").strip() or None
            except OSError:
                password = None
    return host, port, user, password


def _sqlstate(stderr: str | bytes | None) -> str | None:
    text = stderr.decode(errors="replace") if isinstance(stderr, bytes) else (stderr or "")
    matches = _SQLSTATE.findall(text.upper())
    return matches[-1] if matches else None


def collect_server_summary(  # noqa: C901
    cluster: _ServerCluster,
    *,
    timeout: float = 10.0,
    monotonic: Callable[[], float] | None = None,
    context: RunContext[_ContextT],
    steps: Sequence[PreparedStep],
) -> ServerSummary:
    """Try each captured maintenance probe under one monotonic deadline."""
    budget = validate_timeout(timeout)
    clock = monotonic or time.monotonic
    _host, _port, user, _password = _credentials(cluster)

    captured_steps = tuple(steps)

    def finish(summary: ServerSummary) -> ServerSummary:
        for step in captured_steps:
            if context.planned(step.step_id) and not context.consumed(step.step_id):
                context.skip(step.step_id)
        return summary

    if not user:
        return finish(ServerSummary(None, "credentials_missing"))
    if not captured_steps:
        return finish(ServerSummary(None, "psql_missing"))

    deadline = ExecutionDeadline.start(budget, monotonic=clock)
    continuable: list[str] = []
    for step in captured_steps:
        if deadline.remaining() < MIN_PROCESS_TIMEOUT:
            return finish(ServerSummary(None, "timeout"))
        try:
            result = cast("ProcessResult", context.process_prepared_with_deadline(step, deadline))
        except FileNotFoundError:
            return finish(ServerSummary(None, "psql_missing"))
        except TimeoutError:
            return finish(ServerSummary(None, "timeout"))
        except Exception as exc:
            from odoo_instance_sdk.internal.proc import ProcessSpawnError, ProcessTimeoutError

            if isinstance(exc, ProcessTimeoutError):
                return finish(ServerSummary(None, "timeout"))
            if isinstance(exc, ProcessSpawnError):
                if isinstance(exc.__cause__, FileNotFoundError):
                    return finish(ServerSummary(None, "psql_missing"))
                return finish(ServerSummary(None, "server_unreachable"))
            return finish(ServerSummary(None, "query_failed"))
        if result.returncode == 0:
            stdout = result.stdout
            if not isinstance(stdout, (str, bytes)):
                return finish(ServerSummary(None, "invalid_response"))
            try:
                return finish(ServerSummary(decode_server_summary(stdout), None))
            except Exception:
                return finish(ServerSummary(None, "invalid_response"))
        state = _sqlstate(result.stderr)
        reason = classify_server_failure(state, "query")
        if _continuable(state):
            continuable.append(state or "")
            continue
        return finish(ServerSummary(None, reason))
    if "42501" in continuable:
        return finish(ServerSummary(None, "privilege_denied"))
    return finish(ServerSummary(None, "maintenance_database_unavailable"))


__all__ = [
    "SERVER_SUMMARY_SQL",
    "SERVER_SUMMARY_SQL_VERSION",
    "SERVER_SUMMARY_TIMEOUT",
    "ServerSummary",
    "build_server_summary_steps",
    "classify_server_failure",
    "collect_server_summary",
    "decode_server_summary",
    "maintenance_database_candidates",
    "server_summary_deadline_observation",
]
