"""Process and resource inventory projection from one monitor snapshot.

This module is a projection boundary. It consumes one captured
``EnvironmentMonitor.snapshot_command()`` result plus an optional bounded set
of external process contributions and builds the frozen ``ProcessInventory``
model. It does not own a catalogue, perform a second sample, spawn processes,
or query a live database outside the bounded PostgreSQL backend attribution
helper.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.models import (
    BackendGroupReason,
    BackendProcessGroup,
    BackendSession,
    BackendUnavailabilityReason,
    CheckoutProcessBlock,
    ClusterSnapshot,
    EnvironmentSnapshot,
    EnvironmentState,
    PidScope,
    ProcessContribution,
    ProcessGroupRuntime,
    ProcessInventory,
    ProjectSummary,
    RuntimeMetrics,
    RuntimeState,
    SharedResourcesBlock,
    Snapshot,
)

_PROCESS_INVENTORY_SCHEMA_VERSION = 1


def _pid_scope_for_platform() -> PidScope:
    # ponytail: platform split only; Colima/Desktop both run containers in a VM on darwin.
    return PidScope.DOCKER_VM if sys.platform == "darwin" else PidScope.HOST


def _runtime_to_group(runtime: RuntimeMetrics) -> ProcessGroupRuntime:
    return ProcessGroupRuntime(
        root_pid=runtime.root_pid,
        child_pids=runtime.child_pids,
        process_count=runtime.process_count,
        cpu_percent=runtime.cpu_percent,
        memory_bytes=runtime.memory_bytes,
        started_at=runtime.started_at,
        state=runtime.state,
        http_url=runtime.http_url,
        http_port=runtime.http_port,
        database_name=runtime.database_name,
        commit_sha=runtime.commit_sha,
        branch=runtime.branch,
    )


def _stopped_group() -> ProcessGroupRuntime:
    return ProcessGroupRuntime(
        root_pid=None,
        child_pids=(),
        process_count=0,
        cpu_percent=None,
        memory_bytes=None,
        started_at=None,
        state=RuntimeState.STOPPED,
    )


@dataclass(frozen=True, slots=True)
class DatabaseCredentials:
    """Bounded PostgreSQL credentials for one database attribution."""

    database: str
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None


CredentialResolver = Callable[[str], DatabaseCredentials | None]


@dataclass(frozen=True, slots=True)
class _BackendAttributionInput:
    """One bounded backend attribution request for a single cluster/database."""

    project_id: str
    cluster: ClusterSnapshot | None
    database: str
    credentials: DatabaseCredentials | None


BackendRunner = Callable[[_BackendAttributionInput], tuple[int, str, str]]


@dataclass(frozen=True, slots=True)
class _BackendAttributionResult:
    """One bounded backend attribution response."""

    database: str
    sessions: tuple[BackendSession, ...] = ()
    pid_scope: PidScope = PidScope.UNAVAILABLE
    host_pids: tuple[int, ...] = ()
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    sample_time: datetime | None = None
    unavailability_reason: BackendUnavailabilityReason | None = None


BackendAttributionCollector = "collections.abc.Callable[[Sequence[_BackendAttributionInput]], tuple[_BackendAttributionResult, ...]]"


def _classify_backend_failure(returncode: int, stderr: str) -> BackendUnavailabilityReason:
    text = (stderr or "").lower()
    if returncode in (12,):
        return "timeout"
    if "authentication" in text or "password" in text:
        return "authentication_failed"
    if "permission denied" in text or "privilege" in text:
        return "privilege_denied"
    if "could not connect" in text or "connection refused" in text or "no such file" in text:
        return "server_unreachable"
    if "does not exist" in text and "pg_stat_activity" not in text:
        return "maintenance_database_unavailable"
    if returncode != 0:
        return "query_failed"
    return "invalid_response"


def _parse_pg_stat_activity_rows(
    stdout: str,
) -> tuple[dict[str, list[BackendSession]], dict[int, BackendSession]]:
    """Decode the bounded ``pg_stat_activity`` JSON payload.

    Returns a mapping of ``datname -> sessions`` and a mapping of
    ``pid -> session`` for host-visible PID verification.
    """
    try:
        payload = json.loads(stdout or "[]")
    except (TypeError, json.JSONDecodeError):
        return {}, {}
    if not isinstance(payload, list):
        return {}, {}
    by_database: dict[str, list[BackendSession]] = {}
    by_pid: dict[int, BackendSession] = {}
    for row in payload:
        if not isinstance(row, Mapping):
            continue
        database = row.get("datname")
        if not isinstance(database, str) or not database:
            continue
        pid_raw = row.get("pid")
        if not isinstance(pid_raw, int) or pid_raw < 0:
            continue
        session = BackendSession(
            pid=pid_raw,
            state=_as_str(row.get("state")),
            application_name=_as_str(row.get("application_name")),
            user_name=_as_str(row.get("usename")),
            client_address=_as_str(row.get("client_addr")),
            backend_start=_as_datetime(row.get("backend_start")),
            query_start=_as_datetime(row.get("query_start")),
            transaction_start=_as_datetime(row.get("xact_start")),
            wait_event_type=_as_str(row.get("wait_event_type")),
            wait_event=_as_str(row.get("wait_event")),
        )
        by_database.setdefault(database, []).append(session)
        by_pid[pid_raw] = session
    return by_database, by_pid


def _as_str(value: JsonValue) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_datetime(value: JsonValue) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pg_stat_activity_query() -> str:
    return (
        "SELECT coalesce(json_agg(row_to_json(t)), '[]'::json) FROM ("
        "SELECT pid, datname, state, application_name, usename, "
        "client_addr::text AS client_addr, backend_start, query_start, "
        "xact_start, wait_event_type, wait_event "
        "FROM pg_stat_activity "
        "WHERE datname IS NOT NULL AND pid <> pg_backend_pid()"
        ") AS t;"
    )


def _verify_host_pid(
    pid: int,
    sessions_by_pid: Mapping[int, BackendSession],
    *,
    pid_scope: PidScope,
) -> tuple[int, BackendSession | None, BackendUnavailabilityReason | None]:
    """Verify a host-visible PID after PID plus create-time verification.

    ponytail: psutil create-time verification guards against PID reuse. A
    macOS Docker/Colima backend PID is VM-scoped and never host-visible.
    """
    if pid_scope is PidScope.DOCKER_VM:
        return pid, None, "vm_scoped_pid"
    try:
        import psutil
    except ImportError:
        return pid, None, "stale_pid"
    try:
        if not psutil.pid_exists(pid):
            return pid, None, "stale_pid"
        session = sessions_by_pid.get(pid)
    except Exception:
        return pid, None, "stale_pid"
    return pid, session, None


def _unavailable(database: str, reason: BackendUnavailabilityReason) -> _BackendAttributionResult:
    return _BackendAttributionResult(
        database=database,
        pid_scope=PidScope.UNAVAILABLE,
        unavailability_reason=reason,
    )


def _collect_one_attribution(
    request: _BackendAttributionInput,
    *,
    scope: PidScope,
    runner: BackendRunner | None,
) -> _BackendAttributionResult:
    cluster = request.cluster
    if cluster is None or cluster.mode == "external":
        return _unavailable(request.database, "maintenance_database_unavailable")
    if cluster.state.value != "healthy":
        return _unavailable(request.database, "server_unreachable")
    if request.credentials is None or request.credentials.user is None:
        return _unavailable(request.database, "credentials_missing")
    if runner is None:
        returncode, stdout, stderr = _run_pg_stat_activity(request)
    else:
        returncode, stdout, stderr = runner(request)
    if returncode != 0:
        return _unavailable(request.database, _classify_backend_failure(returncode, stderr))
    _by_database, sessions_by_pid = _parse_pg_stat_activity_rows(stdout)
    sessions = tuple(
        sessions_by_pid.get(item.pid, item) for item in _by_database.get(request.database, ())
    )
    if scope is PidScope.DOCKER_VM:
        return _BackendAttributionResult(
            database=request.database,
            sessions=sessions,
            pid_scope=PidScope.DOCKER_VM,
            host_pids=tuple(session.pid for session in sessions),
            sample_time=datetime.now(UTC),
            unavailability_reason="vm_scoped_pid" if sessions else None,
        )
    verified_pids: list[int] = []
    stale = False
    for session in sessions:
        pid, verified_session, reason = _verify_host_pid(
            session.pid, sessions_by_pid, pid_scope=scope
        )
        if reason is None and verified_session is not None:
            verified_pids.append(pid)
        elif reason == "stale_pid":
            stale = True
    return _BackendAttributionResult(
        database=request.database,
        sessions=sessions,
        pid_scope=PidScope.HOST,
        host_pids=tuple(verified_pids),
        sample_time=datetime.now(UTC),
        unavailability_reason="stale_pid" if stale and not verified_pids else None,
    )


def _collect_backend_attributions(
    inputs: Sequence[_BackendAttributionInput],
    *,
    runner: BackendRunner | None = None,
) -> tuple[_BackendAttributionResult, ...]:
    """Collect bounded PostgreSQL backend attributions for the given inputs.

    The ``runner`` callable returns ``(returncode, stdout, stderr)`` for one
    bounded ``pg_stat_activity`` query against the cluster's maintenance
    database. When ``runner`` is ``None`` the production transport is used.
    """
    if not inputs:
        return ()
    scope = _pid_scope_for_platform()
    return tuple(
        _collect_one_attribution(request, scope=scope, runner=runner) for request in inputs
    )


def _run_pg_stat_activity(
    request: _BackendAttributionInput,
) -> tuple[int, str, str]:
    """Run one bounded ``pg_stat_activity`` query through the shared transport."""
    from odoo_instance_sdk.internal.pg.transport import run_psql

    credentials = request.credentials
    if credentials is None:
        return 0, "[]", ""
    port = credentials.port if credentials.port is not None else 5432
    completed = run_psql(
        host=credentials.host,
        port=port,
        user=credentials.user,
        password=credentials.password,
        query=_pg_stat_activity_query(),
        timeout=5.0,
        database="postgres",
        step_id=f"process_inventory.{request.project_id}.{request.database}.pg_stat_activity",
    )
    if completed is None:
        return 0, "[]", ""
    return completed.returncode, completed.stdout, completed.stderr


def _build_backend_group(
    result: _BackendAttributionResult,
    *,
    reason: BackendGroupReason,
) -> BackendProcessGroup:
    return BackendProcessGroup(
        database=result.database,
        sessions=result.sessions,
        pid_scope=result.pid_scope,
        host_pids=result.host_pids,
        cpu_percent=result.cpu_percent,
        memory_bytes=result.memory_bytes,
        sample_time=result.sample_time,
        reason=reason,
        unavailability_reason=result.unavailability_reason,
    )


def _database_owner_map(
    environments: Sequence[EnvironmentSnapshot],
    *,
    include_main_runtime: RuntimeMetrics | None,
) -> dict[str, list[str]]:
    """Map each database name to the list of owner IDs using it.

    ponytail: linear scan; the environment count per project is small.
    """
    owners: dict[str, list[str]] = {}
    if include_main_runtime is not None and include_main_runtime.database_name:
        owners.setdefault(include_main_runtime.database_name, []).append("main_checkout")
    for environment in environments:
        if environment.lifecycle_state is EnvironmentState.REMOVED:
            continue
        if environment.database:
            owners.setdefault(environment.database, []).append(environment.id)
    return owners


def _resolve_cluster_for_project(
    projects: Sequence[ProjectSummary],
    *,
    project_id: str | None,
) -> ClusterSnapshot | None:
    for project in projects:
        if project_id is None or project.id == project_id:
            return project.cluster
    return None


def _project_main_runtime(
    projects: Sequence[ProjectSummary],
    *,
    project_id: str | None,
) -> tuple[ProjectSummary | None, RuntimeMetrics | None]:
    for project in projects:
        if project_id is None or project.id == project_id:
            return project, project.runtime
    return None, None


def build_process_inventory(  # noqa: C901
    snapshot: Snapshot,
    *,
    project_id: str | None = None,
    contributions: Sequence[ProcessContribution] = (),
    backend_runner: BackendRunner | None = None,
    credential_resolver: CredentialResolver | None = None,
) -> ProcessInventory:
    """Project one ``ProcessInventory`` from one canonical snapshot.

    Three ownership kinds in order: shared project resources, the main
    checkout, and each non-removed environment. Shared Git objects and the
    PostgreSQL volume appear exactly once. Backend attribution is bounded to
    ``pg_stat_activity``; a ``pg_stat_activity`` error degrades only the
    PostgreSQL portion of the row.
    """
    sample_time = snapshot.generated_at
    project, main_runtime = _project_main_runtime(snapshot.projects, project_id=project_id)
    if project is None and project_id is not None:
        return ProcessInventory(
            schema_version=_PROCESS_INVENTORY_SCHEMA_VERSION,
            generated_at=snapshot.generated_at,
            sample_time=sample_time,
            project_id=project_id,
            shared=(),
            main_checkout=None,
            environments=(),
            complete=False,
            unavailability_reason="project_not_found",
        )

    environments = tuple(
        environment
        for environment in snapshot.environments
        if (project_id is None or environment.project_id == project_id)
        and environment.lifecycle_state is not EnvironmentState.REMOVED
    )

    database_owners = _database_owner_map(environments, include_main_runtime=main_runtime)

    cluster = _resolve_cluster_for_project(snapshot.projects, project_id=project_id)

    backend_inputs: list[_BackendAttributionInput] = []
    for database in sorted(database_owners):
        credentials: DatabaseCredentials | None = None
        if credential_resolver is not None:
            credentials = credential_resolver(database)
        if credentials is None:
            credentials = DatabaseCredentials(database=database)
        backend_inputs.append(
            _BackendAttributionInput(
                project_id=project.id if project is not None else (project_id or ""),
                cluster=cluster,
                database=database,
                credentials=credentials,
            )
        )

    backend_results = _collect_backend_attributions(backend_inputs, runner=backend_runner)
    backend_by_database: dict[str, _BackendAttributionResult] = {
        result.database: result for result in backend_results
    }

    shared_external = tuple(
        contribution for contribution in contributions if contribution.owner_kind == "shared"
    )

    shared_backend_groups: list[BackendProcessGroup] = []
    owner_backend_groups: dict[str, list[BackendProcessGroup]] = {}

    for database, owners in sorted(database_owners.items()):
        result = backend_by_database.get(database)
        if result is None:
            continue
        if len(owners) > 1:
            shared_backend_groups.append(_build_backend_group(result, reason="shared_database"))
            continue
        owner = owners[0]
        owner_backend_groups.setdefault(owner, []).append(
            _build_backend_group(result, reason="unique_database")
        )

    shared_blocks: list[SharedResourcesBlock] = []
    if project is not None:
        shared_blocks.append(
            SharedResourcesBlock(
                project_id=project.id,
                postgres_container=cluster,
                backend_groups=tuple(shared_backend_groups),
                external_contributions=shared_external,
            )
        )

    main_checkout: CheckoutProcessBlock | None = None
    if project is not None:
        main_group = (
            _runtime_to_group(main_runtime) if main_runtime is not None else _stopped_group()
        )
        main_checkout = CheckoutProcessBlock(
            owner_kind="main_checkout",
            owner_id="main_checkout",
            project_id=project.id,
            name=project.name,
            database=main_runtime.database_name if main_runtime is not None else None,
            odoo=main_group,
            backend_groups=tuple(owner_backend_groups.get("main_checkout", ())),
            external_contributions=tuple(
                contribution
                for contribution in contributions
                if contribution.owner_kind == "project"
                and contribution.owner_id in {project.id, None}
            ),
            storage=None,
        )

    env_blocks: list[CheckoutProcessBlock] = []
    for environment in sorted(environments, key=lambda item: item.id):
        owner_id = environment.id
        env_group = _runtime_to_group(environment.runtime)
        env_blocks.append(
            CheckoutProcessBlock(
                owner_kind="environment",
                owner_id=owner_id,
                project_id=environment.project_id,
                name=environment.name,
                branch=environment.branch,
                database=environment.database,
                db_mode=environment.db_mode,
                lifecycle_state=environment.lifecycle_state,
                odoo=env_group,
                backend_groups=tuple(owner_backend_groups.get(owner_id, ())),
                external_contributions=tuple(
                    contribution
                    for contribution in contributions
                    if contribution.owner_kind == "environment"
                    and contribution.owner_id in {owner_id, None}
                ),
                storage=environment.storage,
            )
        )

    return ProcessInventory(
        schema_version=_PROCESS_INVENTORY_SCHEMA_VERSION,
        generated_at=snapshot.generated_at,
        sample_time=sample_time,
        project_id=project_id,
        shared=tuple(shared_blocks),
        main_checkout=main_checkout,
        environments=tuple(env_blocks),
        complete=True,
        unavailability_reason=None,
    )


__all__ = [
    "BackendAttributionCollector",
    "CredentialResolver",
    "DatabaseCredentials",
    "build_process_inventory",
]
