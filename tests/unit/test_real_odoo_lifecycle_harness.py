from __future__ import annotations

import os
import signal
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.integration.real_odoo.cleanup import ResourceLedger
from tests.integration.real_odoo.conftest import E2ERuntime
from tests.integration.real_odoo.test_critical_path import _start_owned


def test_critical_path_registers_public_detached_runtime_for_failure_cleanup() -> None:
    calls: list[tuple[str, Any]] = []

    class Instance:
        process: subprocess.Popen[bytes] | None = None

        def run_detached(self, *, wait_ready: bool, readiness_timeout: float) -> Any:
            calls.append(("start", (wait_ready, readiness_timeout)))
            self.process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
            )
            return self.process

        def stop_environment(self) -> SimpleNamespace:
            assert self.process is not None
            calls.append(("stop", self.process.pid))
            if self.process.poll() is None:
                os.kill(self.process.pid, signal.SIGTERM)
                raise RuntimeError("stop fixture reported a cleanup failure")
            return SimpleNamespace(status="already_stopped")

    ledger = ResourceLedger("run-123")
    runtime = cast("E2ERuntime", SimpleNamespace(run_id="run-123", ledger=ledger))
    instance = Instance()

    owned = _start_owned(instance, runtime)
    assert owned.process is instance.process
    assert instance.process is not None
    child = instance.process
    assert calls == [("start", (True, 180.0))]

    primary = RuntimeError("post-start check failed")
    with pytest.raises(BaseExceptionGroup, match="primary failure and cleanup failures") as caught:
        ledger.unwind(primary_failure=primary)

    assert calls == [("start", (True, 180.0)), ("stop", child.pid)]
    assert not owned.reaper.is_alive()
    assert owned.reap_result and owned.reap_result[0] != -1
    assert ledger.records == ()
    assert caught.value.exceptions[0] is primary
    cleanup_failure = caught.value.exceptions[1]
    assert isinstance(cleanup_failure, RuntimeError)
    assert "cleanup failure" in str(cleanup_failure)

    ledger.unwind()
    assert calls == [("start", (True, 180.0)), ("stop", child.pid)]
