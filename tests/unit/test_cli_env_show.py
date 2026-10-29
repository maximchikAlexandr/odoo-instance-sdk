from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.env import checkout as env_commands, show as env_show_catalogue
from odoo_instance_sdk.models import ProjectStorageFootprint, ProjectSummary, RuntimeState
from tests.unit.test_cli_env_list_grouping import (
    _env,
    _healthy_cluster,
    _runtime,
    _snapshot,
)


@pytest.mark.unit
def test_env_show_explicit_uses_one_snapshot_and_preserves_unavailable_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster = msgspec.structs.replace(
        _healthy_cluster(), metrics=None, unavailability_reason="stats_failed"
    )
    project = msgspec.structs.replace(_project(), cluster=cluster)
    environment = _env(
        name="stopped-env",
        runtime=_runtime(
            state=RuntimeState.STOPPED,
            root_pid=None,
            child_pids=(),
            cpu_percent=None,
            memory_bytes=None,
            http_port=None,
        ),
    )
    snapshot = _snapshot((project,), (environment,))
    calls = 0
    projects: list[str] = []

    class FakeMonitor:
        def snapshot(self, *, project_id: str | None = None) -> object:
            nonlocal calls
            calls += 1
            projects.append(str(project_id))
            return snapshot

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.checkout.EnvironmentMonitor",
        FakeMonitor,
    )
    monkeypatch.setattr(
        env_commands, "_resolve_env_show_catalogue", lambda *_a, **_k: (environment.id, project.id)
    )
    runner = CliRunner()
    machine = runner.invoke(cli, ["env", "show", "stopped-env", "--format", "json"])
    assert machine.exit_code == 0, machine.output
    document = json.loads(machine.output)
    assert calls == 1
    assert projects == [project.id]
    assert document["command"] == "env.show"
    assert document["result"]["environment"]["runtime"]["state"] == "stopped"
    assert document["result"]["cluster"]["metrics"] is None

    human = runner.invoke(cli, ["env", "show", "stopped-env"])
    assert human.exit_code == 0, human.output
    assert "Runtime" in human.output and "stopped" in human.output
    assert "Metrics" in human.output and "unavailable" in human.output
    assert "Availability" in human.output and "stats_failed" in human.output
    assert calls == 2
    assert projects == [project.id, project.id]


@pytest.mark.unit
def test_env_show_without_selector_resolves_registered_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    project = _project()
    environment = _env(name="cwd-env")
    snapshot = _snapshot((project,), (environment,))

    class FakeMonitor:
        def snapshot(self, *, project_id: str | None = None) -> object:
            assert project_id == project.id
            return snapshot

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.checkout.EnvironmentMonitor",
        FakeMonitor,
    )
    monkeypatch.setattr(
        env_commands, "_resolve_env_show_catalogue", lambda *_a, **_k: (environment.id, project.id)
    )
    monkeypatch.chdir(worktree)

    result = CliRunner().invoke(cli, ["env", "show", "--format", "json"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["result"]["environment"]["id"] == environment.id
    assert document["provenance"]["environment_source"] == "cwd"


@pytest.mark.unit
def test_snapshot_selector_rejects_ambiguous_names() -> None:
    first = _env(env_id="11111111-1111-1111-1111-111111111111", name="same")
    second = _env(env_id="22222222-2222-2222-2222-222222222222", name="same")
    snapshot = _snapshot((_project(),), (first, second))

    with pytest.raises(ValueError, match="Ambiguous environment selector"):
        env_commands.select_snapshot_environment(snapshot, "same")


@pytest.mark.unit
@pytest.mark.parametrize("selector", ["11111111-1111-1111-1111-111111111111", "first"])
def test_env_show_catalogue_resolves_uuid_and_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, selector: str
) -> None:
    rows = [
        _catalogue_row("11111111-1111-1111-1111-111111111111", "first", "project_a", tmp_path),
        _catalogue_row("22222222-2222-2222-2222-222222222222", "second", "project_b", tmp_path),
    ]
    _install_catalogue(monkeypatch, tmp_path, rows)

    assert env_show_catalogue.resolve_env_show_catalogue(selector, cwd=tmp_path) == (
        "11111111-1111-1111-1111-111111111111",
        "project_a",
    )


@pytest.mark.unit
def test_env_show_catalogue_resolves_cwd_and_fails_ambiguity_or_absence_before_sampling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    rows = [_catalogue_row("env-1", "one", "project_a", worktree)]
    _install_catalogue(monkeypatch, tmp_path, rows)
    assert env_show_catalogue.resolve_env_show_catalogue(None, cwd=worktree) == (
        "env-1",
        "project_a",
    )
    with pytest.raises(ValueError, match="Environment not found"):
        env_show_catalogue.resolve_env_show_catalogue("missing", cwd=worktree)

    rows.append(_catalogue_row("env-2", "one", "project_a", worktree))
    _install_catalogue(monkeypatch, tmp_path, rows)
    with pytest.raises(ValueError, match="Ambiguous environment selector"):
        env_show_catalogue.resolve_env_show_catalogue("one", cwd=worktree)
    with pytest.raises(ValueError, match="Ambiguous current environment"):
        env_show_catalogue.resolve_env_show_catalogue(None, cwd=worktree)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("selector", "names", "diagnostic"),
    [
        pytest.param("missing", ("same",), "Environment not found", id="missing"),
        pytest.param(
            "same",
            ("same", "same"),
            "Ambiguous environment selector",
            id="ambiguous",
        ),
    ],
)
def test_env_show_catalogue_failures_do_not_construct_monitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    selector: str,
    names: tuple[str, ...],
    diagnostic: str,
) -> None:
    rows = [
        _catalogue_row(f"env-{index}", name, "project_a", tmp_path)
        for index, name in enumerate(names, start=1)
    ]
    _install_catalogue(monkeypatch, tmp_path, rows)

    class ForbiddenMonitor:
        def __init__(self) -> None:
            raise AssertionError("monitor must not be constructed before catalogue resolution")

    monkeypatch.setattr(env_commands, "EnvironmentMonitor", ForbiddenMonitor)
    result = CliRunner().invoke(cli, ["env", "show", selector, "--format", "json"])
    assert result.exit_code != 0
    assert diagnostic in result.output


def _catalogue_row(env_id: str, name: str, project_id: str, worktree: Path) -> sqlite3.Row:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT ? AS id, ? AS name, ? AS project_id, ? AS worktree_path, "
        "? AS repository_root, ? AS git_common_dir",
        (
            env_id,
            name,
            project_id,
            str(worktree),
            str(worktree.parent),
            str(worktree.parent / ".git"),
        ),
    ).fetchone()
    connection.close()
    assert row is not None
    return cast("sqlite3.Row", row)


def _install_catalogue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[sqlite3.Row]
) -> None:
    from odoo_instance_sdk.storage import backup_catalog

    catalog_path = tmp_path / "catalog.sqlite"
    catalog_path.touch()

    class FakeCatalog:
        def __init__(self, *, db_path: Path) -> None:
            assert db_path == catalog_path

        def list_environments(self, *, include_removed: bool) -> list[sqlite3.Row]:
            return rows

        def close(self) -> None:
            pass

    monkeypatch.setattr(backup_catalog, "BackupCatalog", FakeCatalog)
    monkeypatch.setattr(env_show_catalogue, "get_catalog_path", lambda **_kwargs: catalog_path)


def _project() -> ProjectSummary:
    return ProjectSummary(
        storage=ProjectStorageFootprint(total_bytes=None, complete=False),
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        repository_root="/tmp/comerta",
        environment_count=1,
        cluster=_healthy_cluster(),
        runtime=None,
    )
