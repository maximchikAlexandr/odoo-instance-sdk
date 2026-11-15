from __future__ import annotations

import json
import subprocess
import sys
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.output import action_command
from odoo_instance_sdk.models.monitor import CheckoutInventory, CheckoutRow
from odoo_instance_sdk.project import ProjectConfig
from tests.integration.real_odoo.cleanup import ResourceLedger
from tests.integration.real_odoo.conftest import E2ERuntime
from tests.integration.real_odoo.test_critical_path import (
    _CriticalPathWorkspace,
    _environment_state,
    _expected_stop_context,
    _finalize_restored_case,
    _OwnedProcess,
    _start_owned,
)
from tests.unit.owned_child_lifecycle import OwnedChildInstance


def test_critical_path_reaps_owned_child_after_post_start_failure(
    request: pytest.FixtureRequest,
) -> None:
    ledger = ResourceLedger("run-123")
    runtime = cast("E2ERuntime", SimpleNamespace(run_id="run-123", ledger=ledger))
    instance = OwnedChildInstance()

    owned = _start_owned(instance, runtime)
    request.addfinalizer(ledger.unwind)
    assert owned.process is instance.process
    assert instance.process is not None
    child = instance.process
    assert instance.calls == [("start", (True, 180.0))]

    primary = RuntimeError("post-start check failed")
    with pytest.raises(RuntimeError, match="post-start check failed") as caught:
        ledger.unwind(primary_failure=primary)

    assert instance.calls == [("start", (True, 180.0)), ("stop", child.pid)]
    assert not owned.reaper.is_alive()
    assert owned.reap_result and owned.reap_result[0] != -1
    assert ledger.records == ()
    assert caught.value is primary

    ledger.unwind()
    assert instance.calls == [("start", (True, 180.0)), ("stop", child.pid)]


def test_critical_path_preserves_post_start_and_cleanup_failures(
    request: pytest.FixtureRequest,
) -> None:
    ledger = ResourceLedger("run-cleanup-failure")
    runtime = cast("E2ERuntime", SimpleNamespace(run_id="run-cleanup-failure", ledger=ledger))
    instance = OwnedChildInstance(
        stop_failure=RuntimeError("stop fixture cleanup failed after signaling owned child")
    )
    owned = _start_owned(instance, runtime)
    request.addfinalizer(ledger.unwind)
    child = instance.process
    assert child is not None
    primary = RuntimeError("post-start check failed")

    with pytest.raises(BaseExceptionGroup, match="primary failure and cleanup failures") as caught:
        ledger.unwind(primary_failure=primary)

    assert caught.value.exceptions[0] is primary
    assert isinstance(caught.value.exceptions[1], RuntimeError)
    assert "cleanup failed" in str(caught.value.exceptions[1])
    assert owned.reap_result and owned.reap_result[0] != -1
    assert not owned.reaper.is_alive()
    assert ledger.records == ()


def test_restored_case_reverts_config_when_owned_stop_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "odoo.conf"
    config.write_text("db_name = restored\ndbfilter = restored\n", encoding="utf-8")
    stop_failure = RuntimeError("owned process stop failed")

    def fail_stop(_instance: object, _owned: object) -> None:
        raise stop_failure

    monkeypatch.setattr(
        "tests.integration.real_odoo.test_critical_path._stop_owned",
        fail_stop,
    )
    state = cast(
        "_CriticalPathWorkspace",
        SimpleNamespace(
            generated_config=config,
            runtime=SimpleNamespace(topology=SimpleNamespace(target_sentinel_database="sentinel")),
            environment_id="env-1",
            client=SimpleNamespace(
                environments=SimpleNamespace(get=lambda _environment_id: "sentinel-env"),
                instance=SimpleNamespace(
                    from_environment=lambda environment: f"instance:{environment}"
                ),
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="owned process stop failed") as caught:
        _finalize_restored_case(state, object(), cast("_OwnedProcess", object()))

    assert caught.value is stop_failure
    assert "db_name = sentinel" in config.read_text(encoding="utf-8")
    assert "dbfilter = sentinel" in config.read_text(encoding="utf-8")
    assert state.instance == "instance:sentinel-env"


@pytest.fixture
def public_env_stop_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    environment_id = "env-public-row"
    project_id = "project-public-row"
    worktree_path = str(tmp_path / "worktree")
    row = CheckoutRow(
        kind="environment",
        project_id=project_id,
        environment_id=environment_id,
        name="critical-path",
        worktree_path=worktree_path,
        odoo_status="running",
    )
    inventory = CheckoutInventory(
        schema_version=1,
        generated_at=datetime.now(UTC),
        sample_time=datetime.now(UTC),
        project_id=None,
        rows=(row,),
        clusters=(),
    )

    class FakeEnvironments:
        def checkout_inventory(self, **_kwargs: object) -> CheckoutInventory:
            return inventory

    fake_client = SimpleNamespace(environments=FakeEnvironments())
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.checkout.OdooClient",
        lambda **_kwargs: fake_client,
    )
    state = SimpleNamespace(
        project=tmp_path,
        catalog=SimpleNamespace(
            list_projects=lambda: [{"project_id": project_id, "repository_root": str(tmp_path)}]
        ),
    )
    runtime = SimpleNamespace(
        owner_kind="environment",
        owner_id=environment_id,
        project_id=project_id,
        environment_id=environment_id,
        environment_name="critical-path",
        root=Path(worktree_path),
    )
    stop_commands = deque(
        (
            action_command("instance.stop", lambda: {"status": "stopped"}),
            action_command("instance.stop", lambda: {"status": "already_stopped"}),
        )
    )
    resolved = SimpleNamespace(
        runtime=runtime,
        instance=SimpleNamespace(stop_runtime_command=stop_commands.popleft),
        output_provenance={"project_source": "explicit", "environment_source": "explicit"},
    )
    monkeypatch.setattr("odoo_instance_sdk.cli.cli_context.ready_instance", lambda _ctx: resolved)
    return SimpleNamespace(
        runner=CliRunner(),
        state=state,
        environment_id=environment_id,
        project_id=project_id,
        row=row,
        stop_commands=stop_commands,
    )


def test_public_env_list_shape_drives_full_stop_and_repeat_payload(
    public_env_stop_cli: SimpleNamespace,
) -> None:
    listing = public_env_stop_cli.runner.invoke(
        cli, ["env", "list", "--all-projects", "--format", "json"]
    )
    assert listing.exit_code == 0, listing.output
    listed_payload = json.loads(listing.stdout)["result"]

    resolved_row = _environment_state(listed_payload, public_env_stop_cli.environment_id)
    assert resolved_row == public_env_stop_cli.row
    expected = _expected_stop_context(public_env_stop_cli.state, resolved_row)

    first_stop = public_env_stop_cli.runner.invoke(cli, ["stop", "--format", "json"])
    assert first_stop.exit_code == 0, first_stop.output
    assert json.loads(first_stop.stdout)["result"] == {**expected, "status": "stopped"}

    repeated_stop = public_env_stop_cli.runner.invoke(cli, ["stop", "--format", "json"])
    assert repeated_stop.exit_code == 0, repeated_stop.output
    assert json.loads(repeated_stop.stdout)["result"] == {
        **expected,
        "status": "already_stopped",
    }
    assert not public_env_stop_cli.stop_commands


def test_public_init_manifest_boundary_precedes_checkout_binding(tmp_path: Path) -> None:
    initialized = CliRunner().invoke(
        cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--odoo-bin",
            "/pinned/bootstrap/odoo-bin",
            "--python",
            "/pinned/bootstrap/python",
            "--project",
            str(tmp_path),
        ],
    )
    assert initialized.exit_code == 0, initialized.output
    manifest = tmp_path / ".odcli" / "project.toml"
    init_config = msgspec.structs.replace(
        ProjectConfig.load(tmp_path), source_config=Path(".odcli/odoo.conf")
    )
    manifest.write_text(init_config.to_manifest(), encoding="utf-8")
    init_source_config = ProjectConfig.load(tmp_path).source_config
    assert init_source_config == Path(".odcli/odoo.conf")

    current_generated_config = tmp_path / ".odcli" / "environments" / "env-1" / "odoo.conf"
    registered = msgspec.structs.replace(
        init_config,
        odoo_bin=tmp_path / "worktrees" / "env-1" / "odoo-bin",
        python=tmp_path / "environments" / "env-1" / "bin" / "python",
        source_config=current_generated_config,
    )
    manifest.write_text(registered.to_manifest(), encoding="utf-8")

    persisted = ProjectConfig.load(tmp_path)
    assert persisted.source_config == current_generated_config
    assert persisted.odoo_bin == tmp_path / "worktrees" / "env-1" / "odoo-bin"
    assert persisted.python == tmp_path / "environments" / "env-1" / "bin" / "python"
    assert init_source_config != persisted.source_config


def test_critical_path_cleanup_node_has_isolated_fixture_closure_and_timeout() -> None:
    script = """
import pytest

class LayoutProbe:
    def pytest_collection_finish(self, session):
        for item in session.items:
            timeout = item.get_closest_marker("timeout")
            print("LAYOUT|" + item.name + "|" + str(timeout.args[0] if timeout else None)
                  + "|" + ",".join(item.fixturenames))

raise SystemExit(pytest.main([
    "--collect-only", "-q", "-o", "addopts=",
    "tests/integration/real_odoo/test_critical_path.py::test_critical_path_environment_removal_is_idempotent",
    "tests/integration/real_odoo/test_critical_path.py::test_critical_path_environment_registration_and_paths_are_stable",
], plugins=[LayoutProbe()]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [line for line in result.stdout.splitlines() if line.startswith("LAYOUT|")]
    assert len(rows) == 2, result.stdout
    assert "environment_removal_is_idempotent" in rows[0]
    assert "environment_registration_and_paths_are_stable" in rows[1]
    cleanup = next(row for row in rows if "environment_removal_is_idempotent" in row)
    ordinary = next(row for row in rows if "environment_registration_and_paths_are_stable" in row)
    assert cleanup.split("|", 2)[1:2] == ["test_critical_path_environment_removal_is_idempotent"]
    assert cleanup.split("|", 3)[2] == "600"
    cleanup_fixtures = set(cleanup.split("|", 3)[3].split(","))
    assert "critical_path_cleanup_runtime" in cleanup_fixtures
    assert "critical_path_workspace" not in cleanup_fixtures
    assert "target_runtime" not in cleanup_fixtures
    assert ordinary.split("|", 3)[2] == "600"
    ordinary_fixtures = set(ordinary.split("|", 3)[3].split(","))
    assert "critical_path_workspace" in ordinary_fixtures


@pytest.mark.parametrize(
    "node_name",
    (
        "test_critical_path_backup_metadata_and_source_identity",
        "test_critical_path_restored_attachment_is_readable",
        "test_critical_path_restored_model_marker_is_present",
        "test_critical_path_restored_resource_inventory_is_healthy",
        "test_critical_path_restored_runtime_stop_is_repeatable_and_owned",
    ),
)
def test_each_restored_node_has_independent_fixture_and_timeout_plan(node_name: str) -> None:
    script = """
import json
import sys
import pytest

class LayoutProbe:
    def pytest_collection_finish(self, session):
        for item in session.items:
            fixtures = item._fixtureinfo.name2fixturedefs
            scopes = {
                name: definitions[-1].scope
                for name, definitions in fixtures.items()
                if name in {
                    "critical_path_case",
                    "critical_path_runtime",
                    "critical_path_restored_database",
                    "critical_path_restored_runtime",
                    "critical_path_workspace",
                }
            }
            timeout = item.get_closest_marker("timeout")
            print("LAYOUT|" + json.dumps({
                "name": item.name,
                "timeout": timeout.args[0] if timeout else None,
                "fixtures": scopes,
            }, sort_keys=True))

raise SystemExit(pytest.main([
    "--setup-plan", "-q", "-o", "addopts=", sys.argv[1],
], plugins=[LayoutProbe()]))
"""
    node = "tests/integration/real_odoo/test_critical_path.py::" + node_name
    result = subprocess.run(
        [sys.executable, "-c", script, node],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [
        line.removeprefix("LAYOUT|")
        for line in result.stdout.splitlines()
        if line.startswith("LAYOUT|")
    ]
    assert len(rows) == 1, result.stdout
    layout = json.loads(rows[0])
    assert layout["name"] == node_name
    assert layout["timeout"] == 600
    assert layout["fixtures"]["critical_path_case"] == "function"
    assert layout["fixtures"]["critical_path_restored_database"] == "module"
    assert layout["fixtures"]["critical_path_restored_runtime"] == "function"
    assert layout["fixtures"]["critical_path_runtime"] == "module"
    assert layout["fixtures"]["critical_path_workspace"] == "module"
