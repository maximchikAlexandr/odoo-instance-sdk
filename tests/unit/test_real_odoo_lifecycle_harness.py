from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from typing import cast

import pytest

from tests.integration.real_odoo.cleanup import ResourceLedger
from tests.integration.real_odoo.conftest import E2ERuntime
from tests.integration.real_odoo.test_critical_path import _start_owned
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
