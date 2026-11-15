"""Private Supervisor lifecycle against a temporary daemon."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pytest

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.monitor_lifecycle import _resolve_endpoint, _save_state
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    RunContext,
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.supervisor_runtime import (
    WINDOWS_MESSAGE,
    ProgramState,
    SupervisorHome,
    _write_program,
    abort_launch,
    activate_program,
    finish_launch,
    launch_steps,
    monitor_state_path,
    parse_status,
    redact_program_error,
    stop_program,
    stop_steps,
    supervisor_home,
    supervisor_program_name,
    supervisor_reachable,
    supervisorctl_argv,
)

_T = TypeVar("_T")


def _run(
    steps: tuple[PreparedStep | PreparedAction, ...],
    execute: Callable[[RunContext[_T]], _T],
) -> _T:
    return Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in steps)),
        execute,
        steps,
        executor=SubprocessExecutor(),
    ).run()


def _shutdown() -> None:
    if not supervisor_reachable():
        return
    subprocess.run(supervisorctl_argv("shutdown"), check=False, capture_output=True, text=True)


@pytest.mark.unit
def test_program_name_and_status_parser() -> None:
    assert supervisor_program_name("environment", "Feature/One") == "odoo-environment-Feature-One"
    assert parse_status("odcli-monitor RUNNING pid 15, uptime 0:00:01", "odcli-monitor").pid == 15
    assert parse_status("no such process", "odcli-monitor").state == "STOPPED"
    assert "Windows" in WINDOWS_MESSAGE


@pytest.mark.unit
def test_program_config_rejects_unsafe_fields_and_redacts_secrets(tmp_path: Path) -> None:
    home = SupervisorHome(
        root=tmp_path,
        config_path=tmp_path / "supervisord.conf",
        socket_path=tmp_path / "supervisor.sock",
        pid_path=tmp_path / "supervisord.pid",
        log_path=tmp_path / "supervisord.log",
        programs_dir=tmp_path / "programs",
    )
    with pytest.raises(InstanceConfigurationError, match="directory"):
        _write_program(home, "demo", argv=("echo", "ok"), cwd="bad\npath", environment=())
    with pytest.raises(InstanceConfigurationError, match="identifier"):
        _write_program(
            home,
            "demo",
            argv=("echo", "ok"),
            cwd=None,
            environment=(("BAD KEY", "x"),),
        )
    assert redact_program_error("saw secret", ("secret",)) == "saw ***"


@pytest.mark.unit
def test_saved_monitor_address_is_reused_until_explicitly_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_state("127.0.0.1", 8111, True, None)
    assert monitor_state_path().stat().st_mode & 0o777 == 0o600
    assert _resolve_endpoint("127.0.0.1", None) == ("127.0.0.1", 8111)

    def reject_silent_change(host: str, port: int | None) -> int:
        raise AssertionError((host, port))

    monkeypatch.setattr("odoo_instance_sdk.http.app._select_port", reject_silent_change)
    assert _resolve_endpoint("127.0.0.1", None) == ("127.0.0.1", 8111)


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="Supervisor is Unix-only")
def test_private_daemon_owns_one_program_and_manual_stop_stays_stopped() -> None:
    root = Path(tempfile.mkdtemp(prefix="odcli-sup-", dir="/tmp"))
    previous = os.environ.get("ODCLI_USER_ROOT")
    os.environ["ODCLI_USER_ROOT"] = str(root)
    program = supervisor_program_name("environment", "demo")
    argv = (sys.executable, "-c", "import time; time.sleep(30)")
    try:
        steps = launch_steps(program, follow=False)

        def launch(context: RunContext[int]) -> int:
            pid = activate_program(context, program=program, argv=argv, cwd=str(root))
            finish_launch(context)
            return pid

        pid = _run(steps, launch)
        again = _run(steps, launch)
        assert again == pid
        home = supervisor_home()
        assert home.config_path.stat().st_mode & 0o777 == 0o600
        assert home.program_path(program).stat().st_mode & 0o777 == 0o600
        assert home.root.stat().st_mode & 0o777 == 0o700
        os.kill(int(pid), signal.SIGKILL)
        deadline = time.monotonic() + 15
        restarted = pid
        while time.monotonic() < deadline:
            text = subprocess.run(
                supervisorctl_argv("status", program),
                check=False,
                capture_output=True,
                text=True,
            )
            state = parse_status(text.stdout + text.stderr, program)
            if state.running and state.pid not in (None, pid):
                restarted = int(state.pid)
                break
            time.sleep(0.2)
        assert restarted != pid

        def stop(context: RunContext[str]) -> str:
            return stop_program(context, program, expected_argv=argv)

        assert _run(stop_steps(program), stop) == "stopped"
        assert _run(stop_steps(program), stop) == "already_stopped"
        time.sleep(1.5)
        text = subprocess.run(
            supervisorctl_argv("status", program),
            check=False,
            capture_output=True,
            text=True,
        )
        assert parse_status(text.stdout + text.stderr, program).running is False
    finally:
        _shutdown()
        if previous is None:
            os.environ.pop("ODCLI_USER_ROOT", None)
        else:
            os.environ["ODCLI_USER_ROOT"] = previous


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="Supervisor is Unix-only")
def test_failed_launch_does_not_stop_a_program_it_did_not_start() -> None:
    root = Path(tempfile.mkdtemp(prefix="odcli-sup-", dir="/tmp"))
    previous = os.environ.get("ODCLI_USER_ROOT")
    os.environ["ODCLI_USER_ROOT"] = str(root)
    program = supervisor_program_name("environment", "kept")
    argv = (sys.executable, "-c", "import time; time.sleep(30)")
    started = supervisor_program_name("environment", "started")
    try:

        def launch(context: RunContext[int]) -> int:
            pid = activate_program(context, program=program, argv=argv, cwd=str(root))
            finish_launch(context)
            return pid

        pid = _run(launch_steps(program, follow=False), launch)

        def reject(context: RunContext[int]) -> int:
            try:
                activate_program(
                    context,
                    program=program,
                    argv=(sys.executable, "-c", "import time; time.sleep(1)"),
                    cwd=str(root),
                )
            except BaseException:
                abort_launch(context)
                raise
            finish_launch(context)
            return 0

        with pytest.raises(InstanceConfigurationError, match="different command"):
            _run(launch_steps(program, follow=False), reject)
        assert _program_state(program).pid == pid

        def fail_after_start(context: RunContext[int], owned: str) -> int:
            try:
                activate_program(context, program=owned, argv=argv, cwd=str(root))
            except BaseException:
                abort_launch(context)
                raise
            abort_launch(context)
            raise InstanceConfigurationError("readiness failed")

        with pytest.raises(InstanceConfigurationError, match="readiness"):
            _run(
                launch_steps(program, follow=False),
                lambda context: fail_after_start(context, program),
            )
        assert _program_state(program).pid == pid

        with pytest.raises(InstanceConfigurationError, match="readiness"):
            _run(
                launch_steps(started, follow=False),
                lambda context: fail_after_start(context, started),
            )
        assert _program_state(started).running is False
        assert _program_state(program).pid == pid
    finally:
        _shutdown()
        if previous is None:
            os.environ.pop("ODCLI_USER_ROOT", None)
        else:
            os.environ["ODCLI_USER_ROOT"] = previous


def _program_state(program: str) -> ProgramState:
    text = subprocess.run(
        supervisorctl_argv("status", program),
        check=False,
        capture_output=True,
        text=True,
    )
    return parse_status(text.stdout + text.stderr, program)


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="Supervisor is Unix-only")
def test_startup_failure_is_not_success() -> None:
    root = Path(tempfile.mkdtemp(prefix="odcli-sup-", dir="/tmp"))
    previous = os.environ.get("ODCLI_USER_ROOT")
    os.environ["ODCLI_USER_ROOT"] = str(root)
    program = supervisor_program_name("environment", "fatal")
    argv = (sys.executable, "-c", "import sys; sys.exit(1)")
    try:
        steps = launch_steps(program, follow=False)

        def launch(context: RunContext[int]) -> int:
            return activate_program(context, program=program, argv=argv, cwd=str(root))

        with pytest.raises(InstanceConfigurationError, match=r"FATAL|BACKOFF"):
            _run(steps, launch)
    finally:
        _shutdown()
        if previous is None:
            os.environ.pop("ODCLI_USER_ROOT", None)
        else:
            os.environ["ODCLI_USER_ROOT"] = previous
