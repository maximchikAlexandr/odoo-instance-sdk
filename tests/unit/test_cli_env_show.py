from __future__ import annotations

import json
from pathlib import Path

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands import env as env_commands
from odoo_instance_sdk.models import ProjectSummary, RuntimeState
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

    class FakeMonitor:
        def snapshot(self) -> object:
            nonlocal calls
            calls += 1
            return snapshot

    monkeypatch.setattr(env_commands, "_monitor_class", lambda: FakeMonitor)
    monkeypatch.setattr(
        env_commands,
        "_catalog_worktree_paths",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit must not read paths")
        ),
    )

    runner = CliRunner()
    machine = runner.invoke(cli, ["env", "show", "stopped-env", "--format", "json"])
    assert machine.exit_code == 0, machine.output
    document = json.loads(machine.output)
    assert calls == 1
    assert document["command"] == "env.show"
    assert document["result"]["environment"]["runtime"]["state"] == "stopped"
    assert document["result"]["cluster"]["metrics"] is None

    human = runner.invoke(cli, ["env", "show", "stopped-env"])
    assert human.exit_code == 0, human.output
    assert "runtime=stopped" in human.output
    assert "metrics=unavailable" in human.output
    assert calls == 2


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
        def snapshot(self) -> object:
            return snapshot

    monkeypatch.setattr(env_commands, "_monitor_class", lambda: FakeMonitor)
    monkeypatch.setattr(
        env_commands,
        "_catalog_worktree_paths",
        lambda *_args, **_kwargs: {environment.id: str(worktree)},
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


def _project() -> ProjectSummary:
    return ProjectSummary(
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        environment_count=1,
        cluster=_healthy_cluster(),
        runtime=None,
    )
