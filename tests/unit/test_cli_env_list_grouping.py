from __future__ import annotations

import json
from datetime import UTC, datetime
from inspect import getsource
from typing import Any, ClassVar, cast

import pytest
from click.testing import CliRunner
from rich.console import Console, Group
from rich.table import Table

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands import env as env_commands
from odoo_instance_sdk.models import (
    ClusterEndpoint,
    ClusterMetrics,
    ClusterSnapshot,
    DatabaseFootprint,
    EnvironmentArtifacts,
    EnvironmentSnapshot,
    GitActivity,
    GitActivityState,
    GitDiff,
    PgAdminEligibility,
    PgAdminEligibilityState,
    PostgresClusterState,
    ProjectSummary,
    PythonEnvFootprint,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)
from odoo_instance_sdk.resources.environment import EnvironmentState
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from tests.unit.monitor_support import FakeProcessProvider


@pytest.fixture(autouse=True)
def _inject_monitor_process_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

    original_init = EnvironmentMonitor.__init__

    def init(self: EnvironmentMonitor, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("process_provider", FakeProcessProvider())
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EnvironmentMonitor, "__init__", init)


def _runtime(
    *,
    state: RuntimeState = RuntimeState.READY,
    root_pid: int | None = 4242,
    child_pids: tuple[int, ...] = (4243, 4244),
    cpu_percent: float | None = 12.3,
    rss_bytes: int | None = 256 * 1024 * 1024,
    http_port: int | None = 8069,
) -> RuntimeMetrics:
    return RuntimeMetrics(
        state=state,
        root_pid=root_pid,
        child_pids=child_pids,
        process_count=1 + len(child_pids),
        cpu_percent=cpu_percent,
        rss_bytes=rss_bytes,
        started_at=datetime.now(UTC),
        http_url=f"http://127.0.0.1:{http_port}" if http_port else None,
        http_port=http_port,
        database_name="db1",
        commit_sha="abcdef1234567890",
        branch="main",
    )


def _git(
    *,
    state: GitActivityState = GitActivityState.AHEAD,
    ahead: int | None = 2,
    behind: int | None = 0,
    diff: GitDiff | None = None,
) -> GitActivity:
    return GitActivity(
        default_branch="main",
        head_sha="abcdef1234567890",
        short_sha="abcdef1",
        branch="feat/x",
        ahead=ahead,
        behind=behind,
        diff=diff if diff is not None else GitDiff(added=10, deleted=3),
        state=state,
    )


def _storage(*, total_bytes: int = 100 * 1024, complete: bool = True) -> StorageFootprint:
    return StorageFootprint(
        total_bytes=total_bytes,
        complete=complete,
        worktree_bytes=total_bytes,
        python_environment=PythonEnvFootprint(owned=False, bytes=None),
        database=DatabaseFootprint(
            owned=False, postgres_bytes=None, filestore_bytes=None, total_bytes=None
        ),
        other_files_bytes=None,
    )


def _env(
    *,
    env_id: str = "11111111-1111-1111-1111-111111111111",
    project_id: str = "project_comerta_abc12345",
    name: str = "comerta:main",
    branch: str = "feat/x",
    lifecycle_state: EnvironmentState = EnvironmentState.READY,
    runtime: RuntimeMetrics | None = None,
    git: GitActivity | None = None,
    storage: StorageFootprint | None = None,
    db_mode: str = "shared",
    database: str | None = "comerta",
    allocated_http_port: int | None = 8069,
) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        id=env_id,
        project_id=project_id,
        name=name,
        branch=branch,
        short_sha="abcdef1",
        db_mode=db_mode,  # type: ignore[arg-type]
        database=database,
        lifecycle_state=lifecycle_state,
        allocated_http_port=allocated_http_port,
        observed_port=None,
        artifacts=EnvironmentArtifacts(
            worktree_exists=False,
            worktree_registered=False,
            config_exists=False,
            python_exists=False,
            python_contained=True,
            dependency_lock_exists=False,
            backup_exists=None,
        ),
        runtime=runtime or _runtime(),
        git=git or _git(),
        storage=storage or _storage(),
        pgadmin=PgAdminEligibility(state=PgAdminEligibilityState.ELIGIBLE),
    )


def _healthy_cluster() -> ClusterSnapshot:
    return ClusterSnapshot(
        mode="compose",
        owned=True,
        state=PostgresClusterState.HEALTHY,
        endpoint=ClusterEndpoint(host="127.0.0.1", port=5432),
        container=None,
        metrics=ClusterMetrics(
            cpu_percent=4.2,
            memory_usage_bytes=512 * 1024 * 1024,
            memory_limit_bytes=None,
            volume_usage_bytes=12 * 1024**3,
            sampled_at=datetime.now(UTC),
        ),
        unavailability_reason=None,
        sampled_at=datetime.now(UTC),
    )


def _snapshot(
    projects: tuple[ProjectSummary, ...],
    environments: tuple[EnvironmentSnapshot, ...],
) -> Snapshot:
    return Snapshot(
        schema_version=3,
        generated_at=datetime.now(UTC),
        projects=projects,
        environments=environments,
    )


def _patch_snapshot(monkeypatch: pytest.MonkeyPatch, snapshot: Snapshot) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot",
        lambda self, project_id=None, *, include_removed=False: snapshot,
    )


def _render_snapshot(snapshot: Snapshot) -> str:
    console = Console(record=True, color_system=None, width=300)
    console.print(env_commands._render_env_list_rich(snapshot))
    return console.export_text()


@pytest.mark.unit
def test_env_list_human_shows_project_header_and_cluster_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster = _healthy_cluster()
    project = ProjectSummary(
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        environment_count=1,
        cluster=cluster,
    )
    env = _env()
    _patch_snapshot(monkeypatch, _snapshot((project,), (env,)))

    result = CliRunner().invoke(cli, ["env", "list", "--all-projects"])
    assert result.exit_code == 0, result.output
    assert "Project comerta" in result.output
    assert "PostgreSQL" in result.output
    assert "healthy" in result.output
    assert "cpu=4.2%" in result.output
    assert "ram=512.0 MiB" in result.output
    assert "disk=12.0 GiB" in result.output


@pytest.mark.unit
def test_env_list_human_env_row_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = _healthy_cluster()
    project = ProjectSummary(
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        environment_count=1,
        cluster=cluster,
    )
    env = _env(name="myenv", branch="feat/x")
    _patch_snapshot(monkeypatch, _snapshot((project,), (env,)))

    result = CliRunner().invoke(cli, ["env", "list", "--all-projects"])
    assert result.exit_code == 0, result.output
    out = _render_snapshot(_snapshot((project,), (env,)))
    # The row is a Rich Table projection, not a positional string contract.
    assert "myenv" in out
    assert "feat/x" in out
    assert "ready" in out
    # ODOO_PID = root_pid (+child count)
    assert "4242 (+2)" in out
    assert "12.3%" in out
    # GIT_AHEAD / GIT_DIFF
    assert "↑2 ↓0" in out
    assert "+10 -3" in out
    assert "worktree,registered,config,python,lock" in out


@pytest.mark.unit
def test_env_list_human_table_uses_rich_columns_and_json_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectSummary(
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        environment_count=1,
        cluster=_healthy_cluster(),
    )
    original_name = "very-long\r\nname-that-must-stay-in-json"
    env = _env(name=original_name)
    _patch_snapshot(monkeypatch, _snapshot((project,), (env,)))

    human = CliRunner().invoke(cli, ["env", "list", "--all-projects"])
    assert human.exit_code == 0, human.output
    assert "\x1b" not in human.output
    rendered = _render_snapshot(_snapshot((project,), (env,)))
    assert all(column in rendered for column in env_commands._ENV_LIST_COLUMNS)
    assert "\\x0d\\x0a" in rendered

    encoded = CliRunner().invoke(cli, ["env", "list", "--all-projects", "--json"])
    assert encoded.exit_code == 0, encoded.output
    assert (
        json.loads(encoded.output)["result"]["environments"][0]["name"]
        == r"very-long\x0d\x0aname-that-must-stay-in-json"
    )


@pytest.mark.unit
def test_env_list_stopped_row_shows_dashes(monkeypatch: pytest.MonkeyPatch) -> None:
    project = ProjectSummary(
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        environment_count=1,
        cluster=_healthy_cluster(),
    )
    env = _env(
        name="stopped-env",
        runtime=_runtime(
            state=RuntimeState.STOPPED,
            root_pid=None,
            child_pids=(),
            cpu_percent=None,
            rss_bytes=None,
            http_port=None,
        ),
        git=_git(state=GitActivityState.ORPHAN, ahead=None, behind=None, diff=None),
    )
    _patch_snapshot(monkeypatch, _snapshot((project,), (env,)))

    result = CliRunner().invoke(cli, ["env", "list", "--all-projects"])
    assert result.exit_code == 0, result.output
    out = _render_snapshot(_snapshot((project,), (env,)))
    assert "stopped-env" in out
    # RUNTIME=stopped, ODOO_PID/CPU/RAM all dashes for stopped.
    row_line = next(ln for ln in out.splitlines() if "stopped-env" in ln)
    assert "stopped" in row_line
    # ODOO_PID = —
    assert "  —  " in row_line
    # GIT_AHEAD / GIT_DIFF = — for orphan
    assert "↑" not in row_line


@pytest.mark.unit
def test_env_list_json_emits_snapshot_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = _healthy_cluster()
    project = ProjectSummary(
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        environment_count=1,
        cluster=cluster,
    )
    env = _env()
    _patch_snapshot(monkeypatch, _snapshot((project,), (env,)))

    result = CliRunner().invoke(cli, ["env", "list", "--all-projects", "--json"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["command"] == "env.list"
    payload = envelope["result"]
    # Snapshot contract parity.
    assert payload["schema_version"] == 3
    assert "generated_at" in payload
    assert "projects" in payload and "environments" in payload
    proj = payload["projects"][0]
    assert proj["id"] == "project_comerta_abc12345"
    assert proj["name"] == "comerta"
    assert proj["cluster"]["state"] == "healthy"
    assert proj["cluster"]["metrics"]["cpu_percent"] == 4.2
    env_row = payload["environments"][0]
    assert env_row["id"] == "11111111-1111-1111-1111-111111111111"
    assert "runtime" in env_row
    assert "git" in env_row
    assert "storage" in env_row
    assert env_row["runtime"]["state"] == "ready"
    assert env_row["runtime"]["cpu_percent"] == 12.3


@pytest.mark.unit
def test_env_list_all_json_omits_removed_human_includes_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectSummary(
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        environment_count=1,
        cluster=_healthy_cluster(),
    )
    env = _env()
    removed_env = _env(
        env_id="22222222-2222-2222-2222-222222222222",
        name="gone-env",
        branch="feat/gone",
        lifecycle_state=EnvironmentState.REMOVED,
        runtime=_runtime(
            state=RuntimeState.STOPPED,
            root_pid=None,
            child_pids=(),
            cpu_percent=None,
            rss_bytes=None,
            http_port=None,
        ),
    )
    all_snapshot = _snapshot((project,), (env, removed_env))
    active_snapshot = _snapshot((project,), (env,))
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot",
        lambda self, project_id=None, *, include_removed=False: (
            all_snapshot if include_removed else active_snapshot
        ),
    )

    # --json --all: only non-removed snapshot; removed is NOT in the payload.
    json_result = CliRunner().invoke(cli, ["env", "list", "--all-projects", "--all", "--json"])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)["result"]
    ids = [e["id"] for e in payload["environments"]]
    assert ids == ["11111111-1111-1111-1111-111111111111"]

    # --all human: removed row appears with STATE=removed.
    human_result = CliRunner().invoke(cli, ["env", "list", "--all-projects", "--all"])
    assert human_result.exit_code == 0, human_result.output
    rendered = _render_snapshot(all_snapshot)
    assert "gone-env" in rendered
    assert "removed" in rendered
    assert rendered.index("Project comerta") < rendered.index("gone-env")


@pytest.mark.unit
def test_env_list_all_orders_active_and_removed_rows_per_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human --all is a deterministic project-scoped rendering, not a side list."""
    project_a = ProjectSummary(
        id="project_a",
        name="alpha",
        display_hint="a",
        environment_count=1,
        cluster=_healthy_cluster(),
    )
    project_b = ProjectSummary(
        id="project_b",
        name="beta",
        display_hint="b",
        environment_count=1,
        cluster=_healthy_cluster(),
    )
    active_a = _env(
        env_id="11111111-1111-1111-1111-111111111111", project_id="project_a", name="a-active"
    )
    active_b = _env(
        env_id="22222222-2222-2222-2222-222222222222", project_id="project_b", name="b-active"
    )
    removed_a = _env(
        env_id="33333333-3333-3333-3333-333333333333",
        project_id="project_a",
        name="a-removed",
        lifecycle_state=EnvironmentState.REMOVED,
        runtime=_runtime(
            state=RuntimeState.STOPPED,
            root_pid=None,
            child_pids=(),
            cpu_percent=None,
            rss_bytes=None,
            http_port=None,
        ),
    )
    removed_b = _env(
        env_id="44444444-4444-4444-4444-444444444444",
        project_id="project_b",
        name="b-removed",
        lifecycle_state=EnvironmentState.REMOVED,
        runtime=_runtime(
            state=RuntimeState.STOPPED,
            root_pid=None,
            child_pids=(),
            cpu_percent=None,
            rss_bytes=None,
            http_port=None,
        ),
    )
    _patch_snapshot(
        monkeypatch,
        _snapshot((project_a, project_b), (active_a, removed_a, active_b, removed_b)),
    )

    result = CliRunner().invoke(cli, ["env", "list", "--all-projects", "--all"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert [
        line
        for line in lines
        if line.startswith(("a-active", "a-removed", "b-active", "b-removed"))
    ] == [
        line
        for name in ("a-active", "a-removed", "b-active", "b-removed")
        for line in lines
        if line.startswith(name)
    ]


@pytest.mark.unit
def test_env_list_uses_one_snapshot_without_constructing_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import Mock

    snapshot = _snapshot((), ())
    monitor_snapshot = Mock(return_value=snapshot)
    client_constructor = Mock(side_effect=AssertionError("env list must not construct OdooClient"))
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot", monitor_snapshot
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.env.OdooClient", client_constructor)

    result = CliRunner().invoke(cli, ["env", "list", "--all-projects", "--json"])

    assert result.exit_code == 0, result.output
    monitor_snapshot.assert_called_once_with(project_id=None, include_removed=False)
    client_constructor.assert_not_called()


def test_env_list_source_has_no_transport_side_collection() -> None:
    from odoo_instance_sdk.commands.env import env_list

    source = getsource(cast("Any", env_list.callback))
    assert "OdooClient" not in source
    assert "backups" not in source
    assert "environments.list" not in source
    assert "probe_address" not in source
    assert "worktree_list_porcelain" not in source


@pytest.mark.unit
def test_env_list_external_cluster_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = ClusterSnapshot(
        mode="external",
        owned=False,
        state=PostgresClusterState.HEALTHY,
        endpoint=ClusterEndpoint(host="127.0.0.1", port=5432),
        container=None,
        metrics=None,
        unavailability_reason="external_not_owned",
        sampled_at=None,
    )
    project = ProjectSummary(
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        environment_count=1,
        cluster=cluster,
    )
    env = _env()
    _patch_snapshot(monkeypatch, _snapshot((project,), (env,)))
    result = CliRunner().invoke(cli, ["env", "list", "--all-projects"])
    assert result.exit_code == 0, result.output
    cluster_line = next(
        ln for ln in result.output.splitlines() if ln.strip().startswith("PostgreSQL")
    )
    assert "external" in cluster_line
    assert "healthy" in cluster_line


@pytest.mark.unit
def test_rich_renderer_is_pure_sorted_and_retains_all_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_b = ProjectSummary(
        id="project_b",
        name="beta",
        display_hint="b",
        environment_count=1,
        cluster=None,
    )
    project_a = ProjectSummary(
        id="project_a",
        name="alpha",
        display_hint="a",
        environment_count=1,
        cluster=None,
    )
    env_b = _env(
        env_id="22222222-2222-2222-2222-222222222222",
        project_id="project_b",
        name="beta-env",
        branch="beta-branch",
    )
    env_a = _env(
        env_id="11111111-1111-1111-1111-111111111111",
        project_id="project_a",
        name="alpha-env",
        branch="alpha-branch",
    )
    snapshot = _snapshot((project_b, project_a), (env_b, env_a))
    monkeypatch.setattr(
        EnvironmentMonitor,
        "snapshot",
        lambda *_args, **_kwargs: pytest.fail("renderer must not collect inventory"),
    )

    renderable = env_commands._render_env_list_rich(snapshot)
    assert isinstance(renderable, Group)
    assert sum(isinstance(item, Table) for item in renderable.renderables) == 2
    console = Console(record=True, color_system=None, width=300)
    console.print(renderable)
    output = console.export_text()
    assert output.index("Project alpha") < output.index("Project beta")
    assert output.index("alpha-env") < output.index("beta-env")
    for value in (
        "NAME",
        "BRANCH",
        "STATE",
        "RUNTIME",
        "OBSERVED",
        "ODOO_PID",
        "CPU",
        "RAM",
        "GIT_AHEAD",
        "GIT_DIFF",
        "SIZE",
        "DB_MODE",
        "DATABASE",
        "PORT",
        "ARTIFACTS",
    ):
        assert value in output


class _FakeLive:
    instances: ClassVar[list[_FakeLive]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.updates: list[object] = []
        self.transient = _kwargs.get("transient")
        self.entered = False
        self.exited = False
        self.terminal_restored = False
        self.active_renderable: object | None = None
        self.__class__.instances.append(self)

    def __enter__(self) -> _FakeLive:
        self.entered = True
        return self

    def __exit__(self, *_args: object) -> None:
        self.exited = True
        self.terminal_restored = True
        self.active_renderable = None

    def update(self, renderable: object, **_kwargs: object) -> None:
        self.updates.append(renderable)
        self.active_renderable = renderable


@pytest.mark.unit
def test_env_list_watch_refreshes_once_per_sample_and_cleans_up_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot((), ())
    calls: list[tuple[str | None, bool]] = []

    def collect(_self: object, project_id: str | None = None, *, include_removed: bool) -> Snapshot:
        calls.append((project_id, include_removed))
        if len(calls) <= 2:
            return snapshot
        raise KeyboardInterrupt

    _FakeLive.instances.clear()
    sleep_calls: list[float] = []

    def sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(env_commands, "Live", _FakeLive)
    monkeypatch.setattr(EnvironmentMonitor, "snapshot", collect)
    monkeypatch.setattr("odoo_instance_sdk.commands.env.time.sleep", sleep)
    monkeypatch.setattr(Console, "is_terminal", property(lambda _self: True))

    result = CliRunner().invoke(
        cli,
        ["env", "list", "--watch", "--interval", "0.1", "--all-projects", "--all"],
    )
    assert result.exit_code == 130, result.output
    assert calls == [(None, True), (None, True), (None, True)]
    assert len(_FakeLive.instances) == 1
    live = _FakeLive.instances[0]
    assert live.entered and live.exited and live.terminal_restored
    assert live.transient is True
    assert live.active_renderable is None
    assert len(live.updates) == 2
    assert sleep_calls == [0.1, 0.1]


@pytest.mark.unit
def test_env_list_watch_keeps_last_sample_and_sanitizes_retry_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot((), ())
    calls = 0

    def collect(*_args: object, **_kwargs: object) -> Snapshot:
        nonlocal calls
        calls += 1
        if calls == 1:
            return snapshot
        if calls == 2:
            raise RuntimeError("password=hunter2\nretry \x1b[2J \x1b]0;OSC\x07 \x9b31m")
        raise KeyboardInterrupt

    _FakeLive.instances.clear()
    monkeypatch.setattr(env_commands, "Live", _FakeLive)
    monkeypatch.setattr(EnvironmentMonitor, "snapshot", collect)
    monkeypatch.setattr("odoo_instance_sdk.commands.env.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(Console, "is_terminal", property(lambda _self: True))

    result = CliRunner().invoke(cli, ["env", "list", "--watch", "--interval", "0.1"])
    assert result.exit_code == 130, result.output
    updates = _FakeLive.instances[0].updates
    assert len(updates) == 2
    retry_console = Console(record=True, color_system=None, width=120)
    retry_console.print(updates[-1])
    retry_output = retry_console.export_text()
    assert "Retrying:" in retry_output
    assert "hunter2" not in retry_output
    assert "\x1b[2J" not in retry_output
    assert "\x1b]0;OSC\x07" not in retry_output
    assert "\x9b31m" not in retry_output
    assert _FakeLive.instances[0].active_renderable is None


@pytest.mark.unit
def test_env_list_watch_initial_failure_is_sanitized_and_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def collect(*_args: object, **_kwargs: object) -> Snapshot:
        raise RuntimeError("password=hunter2\ninitial failure")

    _FakeLive.instances.clear()
    monkeypatch.setattr(env_commands, "Live", _FakeLive)
    monkeypatch.setattr(EnvironmentMonitor, "snapshot", collect)
    monkeypatch.setattr(Console, "is_terminal", property(lambda _self: True))

    result = CliRunner().invoke(cli, ["env", "list", "--watch", "--interval", "0.1"])
    assert result.exit_code == 1, result.output
    assert "initial failure" in result.output
    assert "hunter2" not in result.output
    assert _FakeLive.instances[0].updates == []
    assert _FakeLive.instances[0].exited


@pytest.mark.unit
def test_env_list_watch_retains_project_selector_across_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot((), ())
    calls: list[tuple[str | None, bool]] = []

    def collect(_self: object, project_id: str | None = None, *, include_removed: bool) -> Snapshot:
        calls.append((project_id, include_removed))
        if len(calls) <= 2:
            return snapshot
        raise KeyboardInterrupt

    _FakeLive.instances.clear()
    monkeypatch.setattr(env_commands, "Live", _FakeLive)
    monkeypatch.setattr(EnvironmentMonitor, "snapshot", collect)
    monkeypatch.setattr(
        env_commands,
        "_resolve_monitor_project_id",
        lambda _ctx, all_projects: None if all_projects else "project_a",
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.env.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(Console, "is_terminal", property(lambda _self: True))

    result = CliRunner().invoke(cli, ["env", "list", "--watch", "--interval", "0.1"])
    assert result.exit_code == 130, result.output
    assert calls == [("project_a", False), ("project_a", False), ("project_a", False)]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "exit_code"),
    [
        (("--format", "json"), 2),
        (("--format", "toon"), 2),
        (("--interval", "0.05"), 2),
    ],
)
def test_env_list_watch_rejects_before_collection(
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    exit_code: int,
) -> None:
    monkeypatch.setattr(
        EnvironmentMonitor,
        "snapshot",
        lambda *_args, **_kwargs: pytest.fail("watch validation must precede collection"),
    )
    result = CliRunner().invoke(cli, ["env", "list", "--watch", *args])
    assert result.exit_code == exit_code, result.output
    assert "schema_version" not in result.output


@pytest.mark.unit
def test_env_list_watch_rejects_non_tty_before_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        EnvironmentMonitor,
        "snapshot",
        lambda *_args, **_kwargs: pytest.fail("non-TTY validation must precede collection"),
    )
    result = CliRunner().invoke(cli, ["env", "list", "--watch"])
    assert result.exit_code == 1, result.output
    assert "interactive terminal" in result.output


@pytest.mark.unit
def test_rich_renderer_neutralizes_tty_control_sequences() -> None:
    esc_csi = "\x1b[2J"
    osc = "\x1b]0;OSC\x07"
    c1_csi = "\x9b31m"
    payload = f"{esc_csi}{osc}{c1_csi}"
    project = ProjectSummary(
        id="project_malicious",
        name=f"project-{payload}",
        display_hint="malicious",
        environment_count=1,
        cluster=_healthy_cluster(),
    )
    env = _env(name=f"env-{payload}", branch=f"branch-{payload}", database=f"db-{payload}")

    console = Console(record=True, force_terminal=True, width=300)
    console.print(env_commands._render_env_list_rich(_snapshot((project,), (env,))))
    output = console.export_text()

    assert esc_csi not in output
    assert osc not in output
    assert c1_csi not in output
    assert "\x07" not in output
    assert "\\x1b[2J" in output
    assert "\\x1b]0;OSC\\x07" in output
    assert "\\x9b31m" in output
