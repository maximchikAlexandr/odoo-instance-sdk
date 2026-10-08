from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from odoo_instance_sdk.commands.output import model_to_dict
from odoo_instance_sdk.exceptions import DuplicateStepError, UnplannedStepError
from odoo_instance_sdk.internal.pg.builder import PsqlSpecification
from odoo_instance_sdk.internal.proc import (
    ProcessExecutionError,
    ProcessOutputLimitError,
    ProcessResult,
    ProcessSpawnError,
    ProcessTimeoutError,
    RecordingExecutor,
)
from odoo_instance_sdk.internal.process_inventory import (
    DatabaseCredentials,
    _BackendAttributionInput,
    build_process_inventory,
)
from odoo_instance_sdk.models import (
    BackendProcessGroup,
    BackendSession,
    ClusterEndpoint,
    ClusterSnapshot,
    DatabaseFootprint,
    EnvironmentArtifacts,
    EnvironmentSnapshot,
    EnvironmentState,
    GitActivity,
    GitActivityState,
    PgAdminEligibility,
    PgAdminEligibilityState,
    PidScope,
    PostgresClusterState,
    ProcessContribution,
    ProcessInventory,
    ProjectSummary,
    PythonEnvFootprint,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

_BackendRunner = Callable[[_BackendAttributionInput], tuple[int, str, str]]


def _snapshot(
    projects: tuple[ProjectSummary, ...],
    environments: tuple[EnvironmentSnapshot, ...],
) -> Snapshot:
    return Snapshot(
        schema_version=4,
        generated_at=datetime(2024, 1, 1, tzinfo=UTC),
        projects=projects,
        environments=environments,
    )


def _project(
    project_id: str = "project-1",
    runtime: RuntimeMetrics | None = None,
    cluster: ClusterSnapshot | None = None,
) -> ProjectSummary:
    return ProjectSummary(
        id=project_id,
        name="demo",
        display_hint="demo",
        repository_root="/tmp/demo",
        environment_count=0,
        cluster=cluster,
        runtime=runtime,
    )


def _running_runtime(database: str = "demo") -> RuntimeMetrics:
    return RuntimeMetrics(
        state=RuntimeState.READY,
        root_pid=12345,
        child_pids=(12346, 12347),
        process_count=3,
        cpu_percent=12.5,
        memory_bytes=1024 * 1024 * 100,
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        http_url="http://127.0.0.1:8069",
        http_port=8069,
        database_name=database,
        commit_sha="abc123",
        branch="main",
    )


def _stopped_runtime() -> RuntimeMetrics:
    return RuntimeMetrics(
        state=RuntimeState.STOPPED,
        root_pid=None,
        child_pids=(),
        process_count=0,
        cpu_percent=None,
        memory_bytes=None,
        started_at=None,
        http_url=None,
        http_port=None,
        database_name=None,
        commit_sha=None,
        branch=None,
    )


def _environment(
    env_id: str = "env-1",
    project_id: str = "project-1",
    name: str = "demo",
    database: str | None = "demo",
    runtime: RuntimeMetrics | None = None,
    state: EnvironmentState = EnvironmentState.READY,
) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        id=env_id,
        project_id=project_id,
        name=name,
        branch="main",
        short_sha="abc123",
        db_mode="copy",
        database=database,
        lifecycle_state=state,
        allocated_http_port=8069,
        observed_port=None,
        artifacts=EnvironmentArtifacts(
            worktree_exists=True,
            worktree_registered=True,
            config_exists=True,
            python_exists=True,
            python_contained=True,
            dependency_lock_exists=True,
            backup_exists=None,
        ),
        runtime=runtime or _stopped_runtime(),
        git=GitActivity(
            default_branch="main",
            head_sha="abc123",
            short_sha="abc",
            branch="main",
            ahead=0,
            behind=0,
            diff=None,
            state=GitActivityState.CLEAN,
        ),
        storage=StorageFootprint(
            total_bytes=0,
            complete=True,
            worktree_bytes=0,
            python_environment=PythonEnvFootprint(owned=False, bytes=None),
            database=DatabaseFootprint(
                owned=False,
                postgres_bytes=None,
                filestore_bytes=None,
                total_bytes=None,
            ),
            other_files_bytes=None,
        ),
        pgadmin=PgAdminEligibility(state=PgAdminEligibilityState.ELIGIBLE),
    )


def test_stopped_main_checkout_remains_visible_with_unavailable_runtime() -> None:
    snapshot = _snapshot((_project(runtime=_stopped_runtime()),), ())
    inventory = build_process_inventory(snapshot)
    assert inventory.main_checkout is not None
    assert inventory.main_checkout.odoo is not None
    assert inventory.main_checkout.odoo.state is RuntimeState.STOPPED
    assert inventory.main_checkout.odoo.root_pid is None
    assert inventory.main_checkout.odoo.cpu_percent is None
    assert inventory.main_checkout.odoo.memory_bytes is None


def test_running_main_checkout_runtime_is_visible() -> None:
    runtime = _running_runtime()
    snapshot = _snapshot((_project(runtime=runtime),), ())
    inventory = build_process_inventory(snapshot)
    assert inventory.main_checkout is not None
    assert inventory.main_checkout.odoo is not None
    assert inventory.main_checkout.odoo.state is RuntimeState.READY
    assert inventory.main_checkout.odoo.root_pid == 12345
    assert inventory.main_checkout.odoo.process_count == 3
    assert inventory.main_checkout.odoo.cpu_percent == 12.5


def test_multiple_odoo_workers_summed_once() -> None:
    runtime = RuntimeMetrics(
        state=RuntimeState.READY,
        root_pid=100,
        child_pids=(101, 102, 103),
        process_count=4,
        cpu_percent=45.0,
        memory_bytes=4 * 1024,
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        http_url="http://127.0.0.1:8069",
        http_port=8069,
        database_name="demo",
        commit_sha="abc",
        branch="main",
    )
    snapshot = _snapshot((_project(runtime=runtime),), ())
    inventory = build_process_inventory(snapshot)
    assert inventory.main_checkout is not None
    assert inventory.main_checkout.odoo is not None
    assert inventory.main_checkout.odoo.process_count == 4
    assert inventory.main_checkout.odoo.memory_bytes == 4 * 1024


def _healthy_cluster() -> ClusterSnapshot:
    return ClusterSnapshot(
        mode="compose",
        owned=True,
        state=PostgresClusterState.HEALTHY,
        endpoint=ClusterEndpoint(host="127.0.0.1", port=5432),
        container=None,
        metrics=None,
        unavailability_reason=None,
        sampled_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _backend_runner_unique(
    sessions: tuple[BackendSession, ...],
) -> _BackendRunner:
    def runner(request: _BackendAttributionInput) -> tuple[int, str, str]:
        import json

        payload = [
            {
                "pid": session.pid,
                "datname": request.database,
                "state": session.state,
                "application_name": session.application_name,
                "usename": session.user_name,
                "client_addr": session.client_address,
            }
            for session in sessions
        ]
        return 0, json.dumps(payload), ""

    return runner


def test_unique_database_attribution_attaches_backend_group_to_environment() -> None:
    cluster = _healthy_cluster()
    env = _environment(env_id="env-1", database="demo")
    snapshot = _snapshot((_project(cluster=cluster),), (env,))
    sessions = (BackendSession(pid=999, state="idle", application_name="odoo"),)
    runner = _backend_runner_unique(sessions)

    def credentials(database: str) -> DatabaseCredentials | None:
        return DatabaseCredentials(database=database, user="odoo", password="x")

    inventory = build_process_inventory(
        snapshot, backend_runner=runner, credential_resolver=credentials
    )
    assert len(inventory.environments) == 1
    backend_groups = inventory.environments[0].backend_groups
    assert len(backend_groups) == 1
    assert backend_groups[0].reason == "unique_database"
    assert backend_groups[0].connection_count == 1


def test_shared_database_shown_once_under_shared_resources() -> None:
    cluster = _healthy_cluster()
    env_a = _environment(env_id="env-a", database="shared_db")
    env_b = _environment(env_id="env-b", database="shared_db")
    snapshot = _snapshot((_project(cluster=cluster),), (env_a, env_b))
    sessions = (BackendSession(pid=500, state="idle"),)
    runner = _backend_runner_unique(sessions)

    def credentials(database: str) -> DatabaseCredentials | None:
        return DatabaseCredentials(database=database, user="odoo", password="x")

    inventory = build_process_inventory(
        snapshot, backend_runner=runner, credential_resolver=credentials
    )
    assert inventory.shared
    shared_backend = inventory.shared[0].backend_groups
    assert len(shared_backend) == 1
    assert shared_backend[0].reason == "shared_database"
    for env_block in inventory.environments:
        assert not env_block.backend_groups


def test_multiple_postgresql_connections_modelled_as_group() -> None:
    cluster = _healthy_cluster()
    env = _environment(env_id="env-1", database="demo")
    snapshot = _snapshot((_project(cluster=cluster),), (env,))
    sessions = (
        BackendSession(pid=800, state="active", application_name="odoo-1"),
        BackendSession(pid=801, state="idle", application_name="odoo-2"),
        BackendSession(pid=802, state="idle", application_name="odoo-3"),
    )
    runner = _backend_runner_unique(sessions)

    def credentials(database: str) -> DatabaseCredentials | None:
        return DatabaseCredentials(database=database, user="odoo", password="x")

    inventory = build_process_inventory(
        snapshot, backend_runner=runner, credential_resolver=credentials
    )
    backend_groups = inventory.environments[0].backend_groups
    assert len(backend_groups) == 1
    assert backend_groups[0].connection_count == 3


def test_macos_docker_backend_pid_is_vm_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    cluster = _healthy_cluster()
    env = _environment(env_id="env-1", database="demo")
    snapshot = _snapshot((_project(cluster=cluster),), (env,))
    sessions = (BackendSession(pid=700, state="idle"),)
    runner = _backend_runner_unique(sessions)

    def credentials(database: str) -> DatabaseCredentials | None:
        return DatabaseCredentials(database=database, user="odoo", password="x")

    inventory = build_process_inventory(
        snapshot, backend_runner=runner, credential_resolver=credentials
    )
    group = inventory.environments[0].backend_groups[0]
    assert group.pid_scope is PidScope.DOCKER_VM
    assert group.unavailability_reason == "vm_scoped_pid"
    assert group.cpu_percent is None
    assert group.memory_bytes is None


def test_host_pid_verification_does_not_drop_postgres_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.platform", "linux")

    def pid_exists(pid: int) -> bool:
        return pid == 801

    monkeypatch.setattr("psutil.pid_exists", pid_exists)
    cluster = _healthy_cluster()
    env = _environment(env_id="env-1", database="demo")
    snapshot = _snapshot((_project(cluster=cluster),), (env,))
    sessions = (
        BackendSession(pid=800, state="active", application_name="odoo-1"),
        BackendSession(pid=801, state="idle", application_name="odoo-2"),
        BackendSession(pid=802, state="idle", application_name="odoo-3"),
    )
    runner = _backend_runner_unique(sessions)

    def credentials(database: str) -> DatabaseCredentials | None:
        return DatabaseCredentials(database=database, user="odoo", password="x")

    inventory = build_process_inventory(
        snapshot, backend_runner=runner, credential_resolver=credentials
    )
    group = inventory.environments[0].backend_groups[0]
    assert group.connection_count == 3
    assert group.host_pids == (801,)
    assert group.unavailability_reason is None


def test_linux_host_visible_pid_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    cluster = _healthy_cluster()
    env = _environment(env_id="env-1", database="demo")
    snapshot = _snapshot((_project(cluster=cluster),), (env,))
    sessions = (BackendSession(pid=700, state="idle"),)
    runner = _backend_runner_unique(sessions)

    def credentials(database: str) -> DatabaseCredentials | None:
        return DatabaseCredentials(database=database, user="odoo", password="x")

    inventory = build_process_inventory(
        snapshot, backend_runner=runner, credential_resolver=credentials
    )
    group = inventory.environments[0].backend_groups[0]
    assert group.pid_scope is PidScope.HOST


def test_privilege_failure_degrades_only_postgresql_portion() -> None:
    cluster = _healthy_cluster()
    env = _environment(env_id="env-1", database="demo", runtime=_running_runtime())
    snapshot = _snapshot((_project(cluster=cluster),), (env,))

    def runner(request: _BackendAttributionInput) -> tuple[int, str, str]:
        return 1, "", "permission denied for pg_stat_activity"

    def credentials(database: str) -> DatabaseCredentials | None:
        return DatabaseCredentials(database=database, user="odoo", password="x")

    inventory = build_process_inventory(
        snapshot, backend_runner=runner, credential_resolver=credentials
    )
    env_block = inventory.environments[0]
    assert env_block.odoo is not None
    assert env_block.odoo.state is RuntimeState.READY
    assert env_block.backend_groups
    assert env_block.backend_groups[0].unavailability_reason == "privilege_denied"


def test_stale_pid_marked_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    cluster = _healthy_cluster()
    env = _environment(env_id="env-1", database="demo")
    snapshot = _snapshot((_project(cluster=cluster),), (env,))
    sessions = (BackendSession(pid=999999, state="idle"),)
    runner = _backend_runner_unique(sessions)

    def credentials(database: str) -> DatabaseCredentials | None:
        return DatabaseCredentials(database=database, user="odoo", password="x")

    inventory = build_process_inventory(
        snapshot, backend_runner=runner, credential_resolver=credentials
    )
    group = inventory.environments[0].backend_groups[0]
    assert group.unavailability_reason == "stale_pid"


def test_shared_external_process_shown_once() -> None:
    snapshot = _snapshot((_project(),), ())
    contribution = ProcessContribution(
        source="odcli-codex",
        local_identity="codex-session-1",
        owner_kind="shared",
        lifecycle_state="running",
        root_pid=5000,
        pid_scope=PidScope.HOST,
        cpu_percent=5.0,
        memory_bytes=50 * 1024 * 1024,
        sample_time=datetime(2024, 1, 1, tzinfo=UTC),
        availability="available",
    )
    inventory = build_process_inventory(snapshot, contributions=(contribution,))
    assert inventory.shared
    assert inventory.shared[0].external_contributions == (contribution,)


def test_no_double_counting_of_shared_backend_group() -> None:
    cluster = _healthy_cluster()
    env_a = _environment(env_id="env-a", database="shared_db")
    env_b = _environment(env_id="env-b", database="shared_db")
    snapshot = _snapshot((_project(cluster=cluster),), (env_a, env_b))
    sessions = (BackendSession(pid=600, state="idle"),)
    runner = _backend_runner_unique(sessions)

    def credentials(database: str) -> DatabaseCredentials | None:
        return DatabaseCredentials(database=database, user="odoo", password="x")

    inventory = build_process_inventory(
        snapshot, backend_runner=runner, credential_resolver=credentials
    )
    all_backend_groups: list[BackendProcessGroup] = []
    all_backend_groups.extend(inventory.shared[0].backend_groups)
    if inventory.main_checkout is not None:
        all_backend_groups.extend(inventory.main_checkout.backend_groups)
    for env_block in inventory.environments:
        all_backend_groups.extend(env_block.backend_groups)
    shared_db_groups = [group for group in all_backend_groups if group.database == "shared_db"]
    assert len(shared_db_groups) == 1


def test_processes_command_returns_immutable_command() -> None:
    monitor = EnvironmentMonitor()
    command = monitor.processes_command()
    assert command.plan.fingerprint is not None
    assert command.plan.steps
    assert all(step.read_only for step in command.plan.steps if hasattr(step, "read_only"))


def _captured_process_monitor(
    monkeypatch: pytest.MonkeyPatch,
    executor: RecordingExecutor,
    *,
    snapshot: Snapshot | None = None,
    credentials: tuple[DatabaseCredentials, ...] | None = None,
) -> EnvironmentMonitor:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.resolve_psql_executable",
        lambda: None,
    )
    captured_snapshot = (
        snapshot
        if snapshot is not None
        else _snapshot(
            (_project(project_id="project-1", cluster=_healthy_cluster()),),
            (_environment(database="demo"),),
        )
    )
    captured_credentials = (
        credentials
        if credentials is not None
        else (
            DatabaseCredentials(
                database="demo",
                host="127.0.0.1",
                port=5432,
                user="odoo",
                password="super-secret",
            ),
        )
    )

    class SnapshotCommand:
        def run(self) -> Snapshot:
            return captured_snapshot

    monkeypatch.setattr(
        EnvironmentMonitor,
        "snapshot_command",
        lambda self, project_id=None, include_removed=False: SnapshotCommand(),
    )
    monkeypatch.setattr(
        EnvironmentMonitor,
        "_process_inventory_credentials",
        lambda self, project_id=None: captured_credentials,
    )
    return EnvironmentMonitor(_executor=executor)


def test_processes_command_preplans_and_consumes_backend_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)

    def result_factory(step: object) -> ProcessResult:
        prepared = step
        return ProcessResult(
            argv=prepared.argv,  # type: ignore[attr-defined]
            returncode=0,
            stdout=json.dumps([{"pid": 700, "datname": "demo", "state": "idle"}]),
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,  # type: ignore[attr-defined]
            environment=prepared.environment,  # type: ignore[attr-defined]
        )

    executor = RecordingExecutor(result_factory=result_factory)
    monitor = _captured_process_monitor(monkeypatch, executor)
    command = monitor.processes_command(project_id="project-1")

    process_steps = command.plan.process_steps
    assert len(process_steps) == 1
    assert process_steps[0].step_id == "process_inventory.project-1.demo.pg_stat_activity"
    assert process_steps[0].executable == "psql"
    public_plan = repr(command.plan)
    assert "super-secret" not in public_plan
    assert "super-secret" not in command.plan.fingerprint
    assert "super-secret" not in repr(model_to_dict(command.plan))

    inventory = command.run()

    assert len(executor.executed) == 1
    assert inventory.environments[0].backend_groups[0].connection_count == 1
    assert inventory.environments[0].backend_groups[0].unavailability_reason is None


def _spawn_failure(argv: tuple[str, ...]) -> ProcessExecutionError:
    return ProcessSpawnError(
        argv,
        "spawn denied super-secret",
        duration=0.0,
        secrets=("super-secret",),
    )


def _timeout_failure(argv: tuple[str, ...]) -> ProcessExecutionError:
    return ProcessTimeoutError(
        argv,
        5.0,
        duration=5.0,
        secrets=("super-secret",),
        stderr_tail="timed out with super-secret",
    )


def _output_limit_failure(argv: tuple[str, ...]) -> ProcessExecutionError:
    return ProcessOutputLimitError(
        argv,
        "output exceeded configured limit super-secret",
        duration=0.0,
        secrets=("super-secret",),
    )


def _two_backend_monitor(
    monkeypatch: pytest.MonkeyPatch,
    executor: RecordingExecutor,
) -> EnvironmentMonitor:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)
    snapshot = _snapshot(
        (_project(project_id="project-1", cluster=_healthy_cluster()),),
        (
            _environment(database="demo", runtime=_running_runtime("demo")),
            _environment(env_id="env-2", name="healthy", database="healthy"),
        ),
    )
    credentials = (
        DatabaseCredentials(
            database="demo", host="127.0.0.1", port=5432, user="odoo", password="super-secret"
        ),
        DatabaseCredentials(
            database="healthy", host="127.0.0.1", port=5432, user="odoo", password="healthy"
        ),
    )
    return _captured_process_monitor(
        monkeypatch, executor, snapshot=snapshot, credentials=credentials
    )


def _assert_two_backend_preservation(
    inventory: ProcessInventory,
    executor: RecordingExecutor,
    reason: str,
) -> None:
    groups = {
        group.database: group
        for environment in inventory.environments
        for group in environment.backend_groups
    }
    assert groups["demo"].unavailability_reason == reason
    assert groups["healthy"].unavailability_reason is None
    assert inventory.environments[0].odoo is not None
    assert inventory.environments[0].odoo.state is RuntimeState.READY
    assert len(executor.executed) == 2


@pytest.mark.parametrize(
    ("error_factory", "reason"),
    [
        (_spawn_failure, "psql_missing"),
        (_timeout_failure, "timeout"),
        (_output_limit_failure, "query_failed"),
    ],
)
def test_processes_command_maps_captured_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
    error_factory: Callable[[tuple[str, ...]], ProcessExecutionError],
    reason: str,
) -> None:
    errors: list[BaseException] = []

    def result_factory(step: object) -> ProcessResult:
        prepared = step
        if ".demo." in prepared.step_id:  # type: ignore[attr-defined]
            error = error_factory(prepared.argv)  # type: ignore[attr-defined]
            errors.append(error)
            raise error
        return ProcessResult(
            argv=prepared.argv,  # type: ignore[attr-defined]
            returncode=0,
            stdout=json.dumps([{"pid": 701, "datname": "healthy", "state": "idle"}]),
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,  # type: ignore[attr-defined]
            environment=prepared.environment,  # type: ignore[attr-defined]
        )

    executor = RecordingExecutor(result_factory=result_factory)
    monitor = _two_backend_monitor(monkeypatch, executor)

    inventory = monitor.processes_command(project_id="project-1").run()

    _assert_two_backend_preservation(inventory, executor, reason)
    assert errors
    assert "super-secret" not in str(errors[0])


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "reason"),
    [
        (1, "", "permission denied for pg_stat_activity", "privilege_denied"),
        (0, "{}", "", "invalid_response"),
    ],
)
def test_processes_command_maps_completed_backend_failures(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    stderr: str,
    reason: str,
) -> None:
    def result_factory(step: object) -> ProcessResult:
        prepared = step
        if ".healthy." not in prepared.step_id:  # type: ignore[attr-defined]
            return ProcessResult(
                argv=prepared.argv,  # type: ignore[attr-defined]
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                duration=0.0,
                cwd=prepared.cwd,  # type: ignore[attr-defined]
                environment=prepared.environment,  # type: ignore[attr-defined]
            )
        return ProcessResult(
            argv=prepared.argv,  # type: ignore[attr-defined]
            returncode=0,
            stdout=json.dumps([{"pid": 702, "datname": "healthy", "state": "idle"}]),
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,  # type: ignore[attr-defined]
            environment=prepared.environment,  # type: ignore[attr-defined]
        )

    executor = RecordingExecutor(result_factory=result_factory)
    monitor = _two_backend_monitor(monkeypatch, executor)

    inventory = monitor.processes_command(project_id="project-1").run()

    _assert_two_backend_preservation(inventory, executor, reason)


def _mismatch_execution(
    execute: Callable[[PsqlSpecification], ProcessResult],
    specification: PsqlSpecification,
) -> ProcessResult:
    bad_step = replace(
        specification.prepared_step,
        argv=(*specification.prepared_step.argv, "--mismatch"),
    )
    return execute(replace(specification, prepared_step=bad_step))


def _duplicate_execution(
    execute: Callable[[PsqlSpecification], ProcessResult],
    specification: PsqlSpecification,
) -> ProcessResult:
    result = execute(specification)
    execute(specification)
    return result


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (_mismatch_execution, UnplannedStepError),
        (_duplicate_execution, DuplicateStepError),
    ],
)
def test_processes_command_propagates_postgresql_ledger_invariants(
    monkeypatch: pytest.MonkeyPatch,
    operation: Callable[
        [Callable[[PsqlSpecification], ProcessResult], PsqlSpecification], ProcessResult
    ],
    expected: type[UnplannedStepError | DuplicateStepError],
) -> None:
    def result_factory(step: object) -> ProcessResult:
        prepared = step
        return ProcessResult(
            argv=prepared.argv,  # type: ignore[attr-defined]
            returncode=0,
            stdout=json.dumps([{"pid": 700, "datname": "demo", "state": "idle"}]),
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,  # type: ignore[attr-defined]
            environment=prepared.environment,  # type: ignore[attr-defined]
        )

    executor = RecordingExecutor(result_factory=result_factory)
    monitor = _captured_process_monitor(monkeypatch, executor)
    from odoo_instance_sdk.internal.pg import transport

    original_execute = transport.execute_psql

    def invalid_execute(specification: PsqlSpecification) -> ProcessResult:
        return operation(original_execute, specification)

    monkeypatch.setattr(transport, "execute_psql", invalid_execute)
    with pytest.raises(expected):
        monitor.processes_command(project_id="project-1").run()


def test_processes_delegates_to_processes_command_without_second_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    class FakeCommand:
        def run(self) -> ProcessInventory:
            return ProcessInventory(
                schema_version=1,
                generated_at=datetime(2024, 1, 1, tzinfo=UTC),
                sample_time=datetime(2024, 1, 1, tzinfo=UTC),
                project_id=None,
            )

    def fake_processes_command(
        self: EnvironmentMonitor, project_id: str | None = None
    ) -> FakeCommand:
        calls["count"] += 1
        return FakeCommand()

    monkeypatch.setattr(EnvironmentMonitor, "processes_command", fake_processes_command)
    monitor = EnvironmentMonitor()
    monitor.processes()
    assert calls["count"] == 1


def test_process_inventory_model_is_frozen() -> None:
    inventory = ProcessInventory(
        schema_version=1,
        generated_at=datetime(2024, 1, 1, tzinfo=UTC),
        sample_time=datetime(2024, 1, 1, tzinfo=UTC),
        project_id=None,
    )
    with pytest.raises(Exception):
        inventory.schema_version = 2  # type: ignore[misc]


def test_process_contribution_rejects_unknown_source() -> None:
    with pytest.raises(ValueError):
        ProcessContribution(
            source="unknown",  # type: ignore[arg-type]
            local_identity="x",
            owner_kind="shared",
            lifecycle_state="running",
        )


def test_process_contribution_rejects_empty_identity() -> None:
    with pytest.raises(ValueError):
        ProcessContribution(
            source="odcli-codex",
            local_identity="",
            owner_kind="shared",
            lifecycle_state="running",
        )


def test_process_inventory_has_three_ownership_kinds_in_order() -> None:
    runtime = _running_runtime()
    cluster = _healthy_cluster()
    env = _environment(env_id="env-1", database="demo", runtime=_running_runtime())
    snapshot = _snapshot((_project(runtime=runtime, cluster=cluster),), (env,))
    inventory = build_process_inventory(snapshot)
    assert inventory.shared
    assert inventory.main_checkout is not None
    assert inventory.main_checkout.owner_kind == "main_checkout"
    assert inventory.environments
    assert all(block.owner_kind == "environment" for block in inventory.environments)


def test_removed_environments_excluded_from_process_inventory() -> None:
    env = _environment(env_id="env-1", database="demo", state=EnvironmentState.REMOVED)
    snapshot = _snapshot((_project(),), (env,))
    inventory = build_process_inventory(snapshot)
    assert not inventory.environments


def test_project_not_found_returns_incomplete_inventory() -> None:
    snapshot = _snapshot((), ())
    inventory = build_process_inventory(snapshot, project_id="missing-project")
    assert inventory.complete is False
    assert inventory.unavailability_reason == "project_not_found"
    assert inventory.main_checkout is None
    assert not inventory.environments
