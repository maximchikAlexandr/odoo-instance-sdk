from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig, StartConfig

pytestmark = pytest.mark.serial


@pytest.mark.skipif(os.name != "posix", reason="process-group signals require POSIX")
def test_foreground_sigint_terminates_child_group_and_restores_handler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pid_file = tmp_path / "grandchild.pid"
    grandchild = "import time; time.sleep(60)"
    child = (
        "import pathlib, subprocess, sys, time; "
        f"p=subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(60)"
    )
    before = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.server._build_cli_args", lambda _config: ["-c", child]
    )
    instance = OdooClient(config=OdooClientConfig(executable=sys.executable)).instance(
        base_url="http://127.0.0.1:8069"
    )

    def interrupt_when_ready() -> None:
        deadline = time.monotonic() + 5
        while not pid_file.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        os.kill(os.getpid(), signal.SIGINT)

    timer = threading.Thread(target=interrupt_when_ready, daemon=True)
    timer.start()
    result = instance.run_foreground(StartConfig(http_port=8069, http_interface="127.0.0.1"))
    timer.join(timeout=2)

    assert result == 130
    assert signal.getsignal(signal.SIGINT) == before
    grandchild_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    raise AssertionError(f"grandchild {grandchild_pid} still exists after SIGINT")
