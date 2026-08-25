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
    group_file = tmp_path / "process-group.pid"
    # Both the session leader and its descendant ignore the cooperative
    # signals.  Ctrl+C must therefore use the bounded TERM/KILL group cleanup,
    # rather than forward SIGINT and wait indefinitely for the leader.
    grandchild = (
        "import os, pathlib, signal, sys, time; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        f"pathlib.Path({str(group_file)!r}).write_text(str(os.getpgrp())); "
        "time.sleep(60)"
    )
    child = (
        "import signal, subprocess, sys, time; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"p=subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
        "time.sleep(60)"
    )
    before = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.server._build_cli_args", lambda _config: ["-c", child]
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.server._FOREGROUND_PROCESS_CLEANUP_TIMEOUT", 0.2
    )
    instance = OdooClient(config=OdooClientConfig(executable=sys.executable)).instance(
        base_url="http://127.0.0.1:8069"
    )

    def interrupt_when_ready() -> None:
        deadline = time.monotonic() + 5
        while not (pid_file.is_file() and group_file.is_file()) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.is_file() and group_file.is_file(), "foreground tree did not become ready"
        os.kill(os.getpid(), signal.SIGINT)

    timer = threading.Thread(target=interrupt_when_ready, daemon=True)
    timer.start()
    result = instance.run_foreground(StartConfig(http_port=8069, http_interface="127.0.0.1"))
    timer.join(timeout=2)

    assert result == 130
    assert signal.getsignal(signal.SIGINT) == before
    grandchild_pid = int(pid_file.read_text())
    process_group_id = int(group_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        raise AssertionError(f"owned process group {process_group_id} still exists after SIGINT")
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild_pid, 0)
