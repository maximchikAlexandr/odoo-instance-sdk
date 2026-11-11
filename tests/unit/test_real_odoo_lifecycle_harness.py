from __future__ import annotations

import json
import subprocess
import sys
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
    _environment_state,
    _expected_stop_context,
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


def test_public_env_list_shape_drives_full_stop_and_repeat_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    listing = CliRunner().invoke(cli, ["env", "list", "--all-projects", "--format", "json"])
    assert listing.exit_code == 0, listing.output
    listed_payload = json.loads(listing.stdout)["result"]

    state = SimpleNamespace(
        project=tmp_path,
        catalog=SimpleNamespace(
            list_projects=lambda: [{"project_id": project_id, "repository_root": str(tmp_path)}]
        ),
    )
    resolved_row = _environment_state(listed_payload, environment_id)
    expected = _expected_stop_context(state, resolved_row)
    statuses = iter(("stopped", "already_stopped"))
    runtime = SimpleNamespace(
        owner_kind="environment",
        owner_id=environment_id,
        project_id=project_id,
        environment_id=environment_id,
        environment_name="critical-path",
        root=Path(worktree_path),
    )
    resolved = SimpleNamespace(
        runtime=runtime,
        instance=SimpleNamespace(
            stop_runtime_command=lambda: action_command(
                "instance.stop", lambda: {"status": next(statuses)}
            )
        ),
        output_provenance={"project_source": "explicit", "environment_source": "explicit"},
    )
    monkeypatch.setattr("odoo_instance_sdk.cli.cli_context.ready_instance", lambda _ctx: resolved)
    for expected_status in ("stopped", "already_stopped"):
        stopped = CliRunner().invoke(cli, ["stop", "--format", "json"])
        assert stopped.exit_code == 0, stopped.output
        assert json.loads(stopped.stdout)["result"] == {
            **expected,
            "status": expected_status,
        }


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
