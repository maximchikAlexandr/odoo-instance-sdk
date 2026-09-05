from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

import pytest

from odoo_instance_sdk.exceptions import PlanValidationError
from odoo_instance_sdk.execution import ExecutionPlan, _PlanObservation, canonical_plan_bytes
from odoo_instance_sdk.internal.pg.server import (
    SERVER_SUMMARY_SQL,
    ServerExecutorCategory,
    ServerSummary,
    build_server_summary_plan,
    classify_server_failure,
    collect_server_summary,
    maintenance_database_candidates,
    server_summary_deadline_observation,
)
from odoo_instance_sdk.internal.postgres_cli import cluster_snapshot
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    PreparedStep,
    ProcessHandle,
    ProcessResult,
    ProcessResultLike,
    RecordingExecutor,
    RunContext,
    StepObserver,
)
from odoo_instance_sdk.models import PostgresClusterState


class _Cluster:
    mode = "external"
    owned = False
    endpoint_host = "127.0.0.1"
    endpoint_port = 5432
    _user = "odoo"
    _password = "secret"


def _result(*, stdout: str = "", stderr: str = "", returncode: int = 0) -> ProcessResult:
    return ProcessResult(
        argv=("psql",),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration=0.0,
        cwd=None,
        environment=(),
    )


def _captured_context(
    monkeypatch: pytest.MonkeyPatch,
    result_factory: Callable[[PreparedProcess], ProcessResultLike],
    *,
    timeout: float = 1.0,
) -> tuple[tuple[PreparedStep, ...], RecordingExecutor, RunContext[object]]:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    steps = build_server_summary_plan(_Cluster(), timeout=timeout).steps
    executor = RecordingExecutor(result_factory=result_factory)
    context: RunContext[object] = RunContext(steps, executor)
    return steps, executor, context


@pytest.mark.unit
def test_server_summary_sql_is_one_static_statement_with_exact_formulas() -> None:
    assert SERVER_SUMMARY_SQL.count("SELECT json_build_object") == 1
    assert "backend_type = 'client backend'" in SERVER_SUMMARY_SQL
    assert "pid <> pg_backend_pid()" in SERVER_SUMMARY_SQL
    assert "state = 'active'" in SERVER_SUMMARY_SQL
    assert "state = 'idle'" in SERVER_SUMMARY_SQL
    assert "datallowconn" in SERVER_SUMMARY_SQL
    assert "NOT datistemplate" in SERVER_SUMMARY_SQL
    assert "has_database_privilege(current_user, datname, 'CONNECT')" in SERVER_SUMMARY_SQL
    assert "floor(extract(epoch FROM clock_timestamp() - postmaster.postmaster_started_at))" in (
        SERVER_SUMMARY_SQL
    )


@pytest.mark.unit
def test_maintenance_candidates_are_ordered_and_deduplicated() -> None:
    assert maintenance_database_candidates("postgres", "postgres") == ("postgres", "template1")
    assert maintenance_database_candidates(None, "project_db") == (
        "project_db",
        "postgres",
        "template1",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sqlstate", "category", "reason"),
    [
        (None, "missing_tool", "psql_missing"),
        (None, "credentials_missing", "credentials_missing"),
        ("28000", "query", "authentication_failed"),
        ("08006", "query", "server_unreachable"),
        (None, "timeout", "timeout"),
        ("3D000", "query", "maintenance_database_unavailable"),
        ("42501", "query", "privilege_denied"),
        ("XX000", "query", "query_failed"),
        (None, "decode", "invalid_response"),
    ],
)
def test_classifier_uses_typed_categories_and_sqlstate(
    sqlstate: str | None, category: str, reason: str
) -> None:
    assert classify_server_failure(sqlstate, cast("ServerExecutorCategory", category)) == reason


@pytest.mark.unit
def test_summary_tries_each_candidate_once_and_shares_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock_now = [0.0]
    attempted: list[str] = []

    def clock() -> float:
        return clock_now[0]

    def record(step: PreparedProcess) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        argv = prepared.argv
        attempted.append(argv[argv.index("-d") + 1])
        clock_now[0] += 0.7499
        return _result(returncode=2, stderr="ERROR: 3D000")

    steps, executor, context = _captured_context(monkeypatch, record)
    public_plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in steps),
        observations=(server_summary_deadline_observation(steps, timeout=1.0),),
    ).with_fingerprint()
    summary = collect_server_summary(timeout=1.0, monotonic=clock, context=context, steps=steps)
    assert summary.server is None
    assert summary.reason == "maintenance_database_unavailable"
    assert attempted == ["postgres", "template1"]
    assert executor.effective_timeouts[0] == pytest.approx(1.0)
    assert executor.effective_environment_snapshots[0] == steps[0].environment_snapshot
    assert executor.effective_timeouts[1] == pytest.approx(0.2501)
    assert dict(executor.effective_environment_snapshots[1])["PGOPTIONS"] == (
        "-c statement_timeout=250"
    )
    assert len(executor.executed) == 2
    assert tuple(step.timeout for step in public_plan.process_steps) == (1.0, 1.0)
    observation = public_plan.observations[0]
    assert isinstance(observation, _PlanObservation)
    assert observation.kind == "deadline-bound-attempt"
    assert observation.scope == "postgres.status.server-summary"
    assert observation.step_ids == tuple(step.step_id for step in steps)
    assert observation.budget_seconds == 1.0
    assert executor.executed[0] is steps[0]
    assert executor.executed[1] is steps[1]
    assert all(step.timeout == 1.0 for step in steps)
    assert dict(steps[0].environment_snapshot)["PGOPTIONS"] == "-c statement_timeout=1000"


@pytest.mark.unit
def test_summary_does_not_start_next_attempt_after_shared_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock_now = [0.0]

    def clock() -> float:
        return clock_now[0]

    def record(_step: PreparedProcess) -> ProcessResult:
        clock_now[0] = 1.0
        return _result(returncode=2, stderr="ERROR: 3D000")

    steps, executor, context = _captured_context(monkeypatch, record)
    summary = collect_server_summary(timeout=1.0, monotonic=clock, context=context, steps=steps)

    assert summary.reason == "timeout"
    assert [step.step_id for step in executor.executed] == ["postgres.status.server-summary.0"]
    assert context.consumed("postgres.status.server-summary.1")


class _LegacyExecutor:
    def execute(
        self,
        _step: PreparedProcess,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResult:
        del observer, observe_output
        raise AssertionError("server summary must not execute")

    def spawn(
        self,
        _step: PreparedProcess,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessHandle:
        del observer, observe_output
        raise AssertionError("server summary must not spawn")


@pytest.mark.unit
def test_legacy_executor_collector_gets_explicit_deadline_capability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    steps = build_server_summary_plan(_Cluster()).steps
    context: RunContext[object] = RunContext(steps, _LegacyExecutor())

    with pytest.raises(PlanValidationError, match="execute_with_deadline"):
        collect_server_summary(
            context=context,
            steps=steps,
            timeout=1.0,
            monotonic=lambda: 0.0,
        )
    assert not context.consumed(steps[0].step_id)


@pytest.mark.unit
def test_summary_privilege_failure_precedes_missing_database_regardless_of_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter(
        [
            _result(returncode=2, stderr="ERROR: 42501"),
            _result(returncode=2, stderr="ERROR: 3D000"),
        ]
    )
    steps, _executor, context = _captured_context(
        monkeypatch, lambda _step: next(outcomes), timeout=10.0
    )
    summary = collect_server_summary(monotonic=lambda: 0.0, context=context, steps=steps)
    assert summary.reason == "privilege_denied"


@pytest.mark.unit
def test_summary_success_decodes_without_disclosing_candidate_or_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "version": "16.4",
        "postmaster_started_at": "2026-01-01T00:00:00Z",
        "uptime_seconds": 42,
        "connections_total": 3,
        "connections_active": 1,
        "connections_idle": 2,
        "max_connections": 100,
        "connectable_databases": 1,
    }
    steps, _executor, context = _captured_context(
        monkeypatch, lambda _step: _result(stdout=json.dumps(payload)), timeout=10.0
    )
    summary = collect_server_summary(monotonic=lambda: 0.0, context=context, steps=steps)
    assert summary.reason is None
    assert summary.server is not None
    assert summary.server.uptime_seconds == 42
    assert "secret" not in repr(summary)
    assert "postgres" not in repr(summary)


@pytest.mark.unit
def test_server_summary_plan_captures_absolute_argv_and_same_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    steps = build_server_summary_plan(_Cluster(), timeout=3.0).steps
    assert tuple(step.argv[0] for step in steps) == ("/psql", "/psql")
    assert tuple(step.step_id for step in steps) == (
        "postgres.status.server-summary.0",
        "postgres.status.server-summary.1",
    )
    assert all(step.timeout == 3.0 for step in steps)


@pytest.mark.unit
def test_deadline_observation_can_compute_remaining_without_run_state() -> None:
    observation = server_summary_deadline_observation(
        (
            PreparedStep(step_id="one", argv=("psql",)),
            PreparedStep(step_id="two", argv=("psql",)),
        ),
        timeout=1.0,
    )
    assert isinstance(observation, _PlanObservation)
    assert observation.budget_seconds == 1.0
    assert observation.step_ids == ("one", "two")


@pytest.mark.unit
def test_deadline_observation_in_plan_is_immutable_and_fingerprint_stable() -> None:
    observation = server_summary_deadline_observation(
        (
            PreparedStep(step_id="one", argv=("psql",)),
            PreparedStep(step_id="two", argv=("psql",)),
        ),
        timeout=1.0,
    )
    plan = ExecutionPlan(observations=(observation,)).with_fingerprint()
    fingerprint = plan.fingerprint

    with pytest.raises(AttributeError):
        observation.budget_seconds = 2.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        observation.step_ids = ("changed",)  # type: ignore[misc]

    assert plan.fingerprint == fingerprint
    assert canonical_plan_bytes(plan) == canonical_plan_bytes(
        ExecutionPlan(observations=(observation,)).with_fingerprint()
    )


@pytest.mark.unit
def test_server_summary_database_is_private_but_public_projection_is_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "maintenance-db-canary"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.server._project_database", lambda _cluster: (canary, None)
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    first = build_server_summary_plan(_Cluster()).steps
    assert canary in first[0].argv
    public = first[0].public_projection()
    assert canary not in public.argv
    assert canary not in public.display
    first_plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in first),
        observations=(server_summary_deadline_observation(first),),
    ).with_fingerprint()
    assert canary not in canonical_plan_bytes(first_plan).decode()

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.server._project_database",
        lambda _cluster: ("another-maintenance-db", None),
    )
    second = build_server_summary_plan(_Cluster()).steps
    second_plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in second),
        observations=(server_summary_deadline_observation(second),),
    ).with_fingerprint()
    assert first_plan.fingerprint == second_plan.fingerprint


@pytest.mark.unit
def test_cluster_snapshot_only_projects_prepared_server_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_process(*_args: object, **_kwargs: object) -> ServerSummary:
        raise AssertionError("status projection must not execute server psql")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.server.collect_server_summary", unexpected_process
    )
    snapshot = cluster_snapshot(_Cluster(), PostgresClusterState.HEALTHY)  # type: ignore[arg-type]
    assert snapshot.server is None
    assert snapshot.server_unavailability_reason is None
