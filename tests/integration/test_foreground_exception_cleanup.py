"""Real POSIX process-group regression tests for foreground cleanup."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance

pytestmark = [pytest.mark.integration, pytest.mark.serial]


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX process groups")
def test_wait_error_kills_sigterm_ignoring_descendant_after_leader_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exceptional cleanup observes the group, not only the exited leader."""
    child_pid_file = tmp_path / "child.pid"
    child_code = (
        "import pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[1]).write_text(str(__import__('os').getpid()))\n"
        "time.sleep(60)"
    )
    leader_code = (
        "import pathlib, subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}, sys.argv[1]])\n"
    )
    config = StartConfig(http_port=8069, http_interface="127.0.0.1")
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=config,
            command_prefix=(sys.executable, "-c", leader_code, str(child_pid_file)),
            default_cwd=tmp_path,
        ),
        _client=OdooClient(config=OdooClientConfig(executable="odoo")),
        _environment_id="owned-environment",
    )
    monkeypatch.setattr(OdooInstance, "_persist_runtime_identity", lambda *args: None)
    monkeypatch.setattr(OdooInstance, "_clear_runtime_identity", lambda *args: None)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.server._FOREGROUND_PROCESS_CLEANUP_TIMEOUT", 0.2
    )

    child_pid: int | None = None
    process_group_id: int | None = None

    def leader_exits_then_wait_fails(proc: Any) -> int:
        nonlocal child_pid, process_group_id
        deadline = time.monotonic() + 5
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text())
        process_group_id = proc.pid
        proc.wait(timeout=5)
        assert os.getpgid(child_pid) == process_group_id
        raise RuntimeError("wait blew up")

    try:
        with (
            patch(
                "odoo_instance_sdk.internal.server.wait_foreground_process",
                side_effect=leader_exits_then_wait_fails,
            ),
            pytest.raises(RuntimeError, match="wait blew up"),
        ):
            instance.run_foreground()

        assert child_pid is not None
        assert process_group_id is not None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.killpg(process_group_id, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("owned process group survived exceptional foreground cleanup")

        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        # Do not leak an ignored-SIGTERM child should an assertion fail.
        if process_group_id is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process_group_id, signal.SIGKILL)
        if child_pid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(child_pid, signal.SIGKILL)


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX process groups")
def test_manual_wait_error_kills_ready_sigterm_ignoring_descendant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Manual run_foreground owns the same exceptional cleanup guarantee."""
    child_pid_file = tmp_path / "manual-child.pid"
    child_code = (
        "import pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[1]).write_text(str(__import__('os').getpid()))\n"
        "time.sleep(60)"
    )
    leader_code = (
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}, sys.argv[1]])\n"
    )
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(http_port=8069, http_interface="127.0.0.1"),
            command_prefix=(sys.executable, "-c", leader_code, str(child_pid_file)),
            default_cwd=tmp_path,
        ),
        _client=OdooClient(config=OdooClientConfig(executable="odoo")),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.server._FOREGROUND_PROCESS_CLEANUP_TIMEOUT", 0.2
    )
    child_pid: int | None = None
    process_group_id: int | None = None

    def wait_then_fail(proc: Any) -> int:
        nonlocal child_pid, process_group_id
        deadline = time.monotonic() + 5
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child_pid_file.exists(), "child did not signal readiness after SIGTERM_IGN"
        child_pid = int(child_pid_file.read_text())
        process_group_id = proc.pid
        proc.wait(timeout=5)
        raise RuntimeError("manual wait blew up")

    try:
        with (
            patch(
                "odoo_instance_sdk.internal.server.wait_foreground_process",
                side_effect=wait_then_fail,
            ),
            pytest.raises(RuntimeError, match="manual wait blew up"),
        ):
            instance.run_foreground()
        assert child_pid is not None and process_group_id is not None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process_group_id, 0)
                time.sleep(0.02)
                continue
            break
        else:
            pytest.fail("manual owned process group survived exceptional cleanup")
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if process_group_id is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process_group_id, signal.SIGKILL)
        if child_pid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(child_pid, signal.SIGKILL)
