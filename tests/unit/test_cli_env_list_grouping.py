from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import (
    ClusterEndpoint,
    ClusterMetrics,
    ClusterSnapshot,
    DatabaseFootprint,
    EnvironmentSnapshot,
    GitActivity,
    GitActivityState,
    GitDiff,
    PostgresClusterState,
    ProjectSummary,
    PythonEnvFootprint,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)
from odoo_instance_sdk.resources.environment import EnvironmentState
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
        runtime=runtime or _runtime(),
        git=git or _git(),
        storage=storage or _storage(),
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
        schema_version=1,
        generated_at=datetime.now(UTC),
        projects=projects,
        environments=environments,
    )


def _patch_snapshot(monkeypatch: pytest.MonkeyPatch, snapshot: Snapshot) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.cli_env.EnvironmentMonitor.snapshot",
        lambda self, project_id=None: snapshot,
    )


def _patch_empty_catalog_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make client.environments.get / .list return nothing for the human path."""
    from unittest.mock import MagicMock

    fake_client = MagicMock()
    fake_client.environments.list.return_value = []
    fake_client.environments.get.return_value = None
    fake_client.backups.list.return_value = []
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.cli_env.OdooClient",
        lambda *a, **k: fake_client,
    )


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
    _patch_empty_catalog_env(monkeypatch)

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
    _patch_empty_catalog_env(monkeypatch)

    result = CliRunner().invoke(cli, ["env", "list", "--all-projects"])
    assert result.exit_code == 0, result.output
    out = result.output
    # Environment row carries the columns from the cli-odcli spec.
    assert "myenv" in out
    assert "feat/x" in out
    assert "ready" in out
    # ODOO_PID = root_pid (+child count)
    assert "4242 (+2)" in out
    assert "12.3%" in out
    # GIT_AHEAD / GIT_DIFF
    assert "↑2 ↓0" in out
    assert "+10 -3" in out
    row = next(line for line in out.splitlines() if line.startswith("myenv  "))
    assert row.split("  ") == [
        "myenv",
        "feat/x",
        "ready",
        "ready",
        "—",
        "4242 (+2)",
        "12.3%",
        "256.0 MiB",
        "↑2 ↓0",
        "+10 -3",
        "100.0 KiB",
        "shared",
        "comerta",
        "8069",
        "—",
    ]


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
    _patch_empty_catalog_env(monkeypatch)

    result = CliRunner().invoke(cli, ["env", "list", "--all-projects"])
    assert result.exit_code == 0, result.output
    out = result.output
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
    _patch_empty_catalog_env(monkeypatch)

    result = CliRunner().invoke(cli, ["env", "list", "--all-projects", "--json"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["command"] == "env.list"
    payload = envelope["result"]
    # Snapshot contract parity.
    assert payload["schema_version"] == 1
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
    _patch_snapshot(monkeypatch, _snapshot((project,), (env,)))

    # Fake catalog returns one removed environment for the --all human path.
    from unittest.mock import MagicMock

    removed_env = MagicMock()
    removed_env.name = "gone-env"
    removed_env.branch = "feat/gone"
    removed_env.id = UUID("22222222-2222-2222-2222-222222222222")
    removed_env.state = EnvironmentState.REMOVED
    removed_env.db_mode = "shared"
    removed_env.source_db_name = None
    removed_env.target_db_name = None
    removed_env.repository_root = "/tmp/gone-repo"
    removed_env.git_common_dir = "/tmp/gone-repo/.git"
    removed_env.generated_config_path = "/tmp/gone-repo/missing.conf"

    fake_client = MagicMock()
    fake_client.environments.list.return_value = [removed_env]
    fake_client.environments.get.return_value = None
    fake_client.backups.list.return_value = []
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.cli_env.OdooClient",
        lambda *a, **k: fake_client,
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
    assert "gone-env" in human_result.output
    assert "removed" in human_result.output
    assert human_result.output.index("Project gone-repo") < human_result.output.index("gone-env")
    row = next(line for line in human_result.output.splitlines() if line.startswith("gone-env  "))
    columns = row.split("  ")
    assert columns == [
        "gone-env",
        "feat/gone",
        "removed",
        "—",
        "—",
        "—",
        "—",
        "—",
        "—",
        "—",
        "—",
        "shared",
        "",
        "8069",
        "worktree,registered,con…",
    ]


@pytest.mark.unit
def test_env_list_all_orders_active_and_removed_rows_per_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human --all is a deterministic project-scoped rendering, not a side list."""
    from unittest.mock import MagicMock

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
    _patch_snapshot(monkeypatch, _snapshot((project_a, project_b), (active_a, active_b)))

    def removed(name: str, root: str, uid: str) -> MagicMock:
        item = MagicMock()
        item.name, item.branch, item.id = name, "main", UUID(uid)
        item.state, item.db_mode = EnvironmentState.REMOVED, "shared"
        item.source_db_name = item.target_db_name = None
        item.repository_root, item.git_common_dir = root, f"{root}/.git"
        item.generated_config_path = f"{root}/missing.conf"
        return item

    fake_client = MagicMock()
    fake_client.environments.list.return_value = [
        removed("a-removed", "/alpha", "33333333-3333-3333-3333-333333333333"),
        removed("b-removed", "/beta", "44444444-4444-4444-4444-444444444444"),
    ]
    fake_client.environments.get.return_value = None
    fake_client.backups.list.return_value = []
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.cli_env.OdooClient", lambda *_, **__: fake_client
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.cli_env.repo_key",
        lambda root, _common: "a" if Path(root).name == "alpha" else "b",
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
    _patch_empty_catalog_env(monkeypatch)

    result = CliRunner().invoke(cli, ["env", "list", "--all-projects"])
    assert result.exit_code == 0, result.output
    cluster_line = next(
        ln for ln in result.output.splitlines() if ln.strip().startswith("PostgreSQL")
    )
    assert "external" in cluster_line
    assert "healthy" in cluster_line
