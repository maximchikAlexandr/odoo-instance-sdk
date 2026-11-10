from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.integration.real_odoo.cleanup import ResourceLedger
from tests.integration.real_odoo.conftest import E2ERuntime
from tests.integration.real_odoo.test_critical_path import _start_owned


def test_critical_path_registers_public_detached_runtime_for_failure_cleanup() -> None:
    calls: list[tuple[str, Any]] = []

    class Instance:
        def run_detached(self, *, wait_ready: bool, readiness_timeout: float) -> Any:
            calls.append(("start", (wait_ready, readiness_timeout)))
            return SimpleNamespace(pid=43210)

        def stop_environment(self) -> None:
            calls.append(("stop", None))

    ledger = ResourceLedger("run-123")
    runtime = cast("E2ERuntime", SimpleNamespace(run_id="run-123", ledger=ledger))
    instance = Instance()

    process = _start_owned(instance, runtime)
    assert process.pid == 43210
    assert calls == [("start", (True, 180.0))]

    with pytest.raises(RuntimeError, match="post-start check failed"):
        try:
            raise RuntimeError("post-start check failed")
        finally:
            ledger.unwind()

    assert calls == [("start", (True, 180.0)), ("stop", None)]
    assert ledger.records == ()
