from __future__ import annotations

import os
import signal
import subprocess
import sys
from types import SimpleNamespace


class OwnedChildInstance:
    """Minimal lifecycle fixture that signals only its launched direct child."""

    def __init__(self, *, stop_failure: RuntimeError | None = None) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.stopped = False
        self.stop_failure = stop_failure
        self.calls: list[tuple[str, object]] = []

    def run_detached(
        self, *, wait_ready: bool = True, readiness_timeout: float = 180.0
    ) -> subprocess.Popen[bytes]:
        self.calls.append(("start", (wait_ready, readiness_timeout)))
        self.process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
        )
        return self.process

    def stop_environment(self) -> SimpleNamespace:
        assert self.process is not None
        self.calls.append(("stop", self.process.pid))
        if self.stopped:
            return SimpleNamespace(status="already_stopped")
        os.kill(self.process.pid, signal.SIGTERM)
        self.stopped = True
        if self.stop_failure is not None:
            raise self.stop_failure
        return SimpleNamespace(status="stopped")
