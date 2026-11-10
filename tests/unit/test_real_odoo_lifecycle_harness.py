from __future__ import annotations

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
