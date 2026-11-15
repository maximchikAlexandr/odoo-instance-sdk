"""Supervisor-backed start, stop, restart, status, and logs for the monitor."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.http.app import _is_loopback_host, _is_port_free, _select_port
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    RunContext,
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.proc.run import ProcessResult
from odoo_instance_sdk.internal.supervisor_runtime import (
    MONITOR_PROGRAM,
    WINDOWS_MESSAGE,
    abort_launch,
    activate_program,
    finish_launch,
    follow_program,
    launch_steps,
    monitor_state_path,
    parse_status,
    program_installed,
    require_unix_supervisor,
    stop_program,
    stop_steps,
    supervisor_home,
    supervisor_reachable,
    supervisorctl_argv,
    windows_unsupported,
)
from odoo_instance_sdk.resources.publication import PublicationSettings

MonitorResult = dict[str, str | int | None]
_PORT_STEP = "monitor.port.check"
_READY_STEP = "monitor.ready"
_STATE_STEP = "monitor.state.write"
_BROWSER_STEP = "monitor.browser.open"


@dataclass(frozen=True, slots=True)
class _SavedMonitor:
    """Saved loopback address for the monitor program."""

    host: str
    port: int
    headless: bool
    settings: str | None


def monitor_argv(
    *,
    host: str,
    port: int,
    headless: bool,
    settings: Path | None,
) -> tuple[str, ...]:
    """Return the frozen interpreter command for the monitor program."""
    argv = [
        sys.executable,
        "-m",
        "odoo_instance_sdk.internal.monitor_program",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if headless:
        argv.append("--headless")
    if settings is not None:
        argv.extend(("--settings", str(settings)))
    return tuple(argv)


def serve_monitor_command(
    *,
    host: str,
    port: int | None,
    headless: bool,
    no_open: bool,
    settings: Path | None,
    detach: bool,
    browser_url: str | None,
    external: bool = False,
) -> Command[int]:
    """Return the one snapshot that starts the monitor and optionally follows it."""
    if windows_unsupported():
        raise SystemExit(WINDOWS_MESSAGE)
    _require_dashboard_extra()
    if not _is_loopback_host(host):
        raise SystemExit("monitor command only supports loopback hosts; refusing network bind")
    chosen_host, chosen_port = _resolve_endpoint(host, port)
    argv = monitor_argv(
        host=chosen_host,
        port=chosen_port,
        headless=headless,
        settings=settings,
    )
    return _monitor_command(
        (*_monitor_effects(), *launch_steps(MONITOR_PROGRAM, follow=not detach)),
        lambda context: _start(
            context,
            argv=argv,
            host=chosen_host,
            port=chosen_port,
            headless=headless,
            settings=settings,
            detach=detach,
            no_open=no_open,
            browser_url=browser_url,
            external=external,
        ),
    )


def serve_monitor(
    *,
    host: str,
    port: int | None,
    headless: bool,
    no_open: bool,
    settings: Path | None,
    detach: bool,
    browser_url: str | None,
    external: bool = False,
) -> None:
    """Start the saved monitor program and optionally follow its log."""
    code = serve_monitor_command(
        host=host,
        port=port,
        headless=headless,
        no_open=no_open,
        settings=settings,
        detach=detach,
        browser_url=browser_url,
        external=external,
    ).run()
    if code == 130:
        raise SystemExit(130)


def stop_monitor_command() -> Command[MonitorResult]:
    """Stop only the monitor program. A repeated stop is success."""
    return _monitor_command(stop_steps(MONITOR_PROGRAM), _stop)


def restart_monitor_command() -> Command[MonitorResult]:
    """Restart only the monitor program without rereading its command."""
    steps = (
        PreparedStep(
            step_id="instance.supervisor.restart",
            argv=supervisorctl_argv("restart", MONITOR_PROGRAM),
            timeout=30.0,
            mode="captured",
            mutating=True,
        ),
    )
    return _monitor_command(steps, _restart)


def monitor_status_command() -> Command[MonitorResult]:
    """Read Supervisor status for the monitor without using its HTTP API."""
    steps = (
        PreparedStep(
            step_id="instance.supervisor.status",
            argv=supervisorctl_argv("status", MONITOR_PROGRAM),
            timeout=30.0,
            mode="captured",
            read_only=True,
        ),
    )
    return _monitor_command(steps, _status)


def monitor_logs_command() -> Command[MonitorResult]:
    """Read the captured monitor log without using its HTTP API."""
    action = PreparedAction(
        step_id="monitor.logs.read",
        action="read_monitor_log",
        description="Read the captured monitor program log",
        read_only=True,
    )
    return _monitor_command((action,), _logs)


_ResultT = TypeVar("_ResultT")


def _monitor_command(
    steps: Sequence[PreparedStep | PreparedAction],
    execute: Callable[[RunContext[_ResultT]], _ResultT],
) -> Command[_ResultT]:
    return Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in steps)),
        execute,
        steps,
        executor=SubprocessExecutor(),
    )


def _monitor_effects() -> tuple[PreparedAction, ...]:
    return (
        PreparedAction(
            step_id=_PORT_STEP,
            action="check_monitor_port",
            description="Reject an occupied saved monitor port",
            read_only=True,
        ),
        PreparedAction(
            step_id=_READY_STEP,
            action="wait_monitor_ready",
            description="Wait until the monitor HTTP API answers",
            read_only=True,
        ),
        PreparedAction(
            step_id=_STATE_STEP,
            action="write_monitor_state",
            description="Save the monitor endpoint",
            mutating=True,
        ),
        PreparedAction(
            step_id=_BROWSER_STEP,
            action="open_monitor_browser",
            description="Open the monitor after readiness",
        ),
    )


def _start(
    context: RunContext[int],
    *,
    argv: Sequence[str],
    host: str,
    port: int,
    headless: bool,
    settings: Path | None,
    detach: bool,
    no_open: bool,
    browser_url: str | None,
    external: bool,
) -> int:
    try:
        activate_program(
            context,
            program=MONITOR_PROGRAM,
            argv=argv,
            cwd=None,
            before_start=lambda: _assert_port_available(context, host, port),
        )
        if context.planned(_PORT_STEP) and not context.consumed(_PORT_STEP):
            context.skip(_PORT_STEP)
        context.action(_READY_STEP)
        _wait_ready(host, port)
        context.complete_action(_READY_STEP)
        context.action(_STATE_STEP)
        _save_state(host, port, headless, settings)
        context.complete_action(_STATE_STEP)
        finish_launch(context)
    except BaseException:
        abort_launch(context)
        raise
    _open_browser(
        context,
        host=host,
        port=port,
        headless=headless,
        no_open=no_open,
        browser_url=browser_url,
        external=external,
        settings=settings,
    )
    if detach:
        return 0
    return follow_program(context)


def _stop(context: RunContext[MonitorResult]) -> MonitorResult:
    require_unix_supervisor()
    if not program_installed(MONITOR_PROGRAM) or not supervisor_reachable():
        for step in stop_steps(MONITOR_PROGRAM):
            context.skip(step.step_id)
        return {"status": "already_stopped", "program": MONITOR_PROGRAM}
    outcome = stop_program(context, MONITOR_PROGRAM, expected_argv=_saved_argv())
    return {"status": outcome, "program": MONITOR_PROGRAM}


def _restart(context: RunContext[MonitorResult]) -> MonitorResult:
    require_unix_supervisor()
    if not program_installed(MONITOR_PROGRAM) or not supervisor_reachable():
        raise InstanceConfigurationError("monitor program is not running")
    context.process("instance.supervisor.restart")
    return {"status": "restarted", "program": MONITOR_PROGRAM}


def _status(context: RunContext[MonitorResult]) -> MonitorResult:
    require_unix_supervisor()
    if not supervisor_reachable():
        context.skip("instance.supervisor.status")
        return {"status": "STOPPED", "program": MONITOR_PROGRAM, "pid": None}
    status = context.process("instance.supervisor.status")
    if not isinstance(status, ProcessResult):
        raise InstanceConfigurationError("monitor status did not return a process result")
    text = _output(status)
    state = parse_status(text, MONITOR_PROGRAM)
    return {"status": state.state, "program": MONITOR_PROGRAM, "pid": state.pid}


def _logs(context: RunContext[MonitorResult]) -> MonitorResult:
    context.action("monitor.logs.read")
    path = supervisor_home().stdout_path(MONITOR_PROGRAM)
    text = path.read_text(encoding="utf-8", errors="replace")[-8000:] if path.is_file() else ""
    context.complete_action("monitor.logs.read")
    return {"status": "ok", "program": MONITOR_PROGRAM, "log": text}


def _resolve_endpoint(host: str, port: int | None) -> tuple[str, int]:
    saved = _load_state()
    if saved is None:
        return host, _select_port(host, port)
    changing = host != saved.host or (port is not None and port != saved.port)
    if not changing:
        return saved.host, saved.port
    return host, _select_port(host, port)


def _assert_port_available(context: RunContext[int], host: str, port: int) -> None:
    context.action(_PORT_STEP)
    if not _is_port_free(host, port):
        raise SystemExit(f"saved monitor port {port} is already in use")
    context.complete_action(_PORT_STEP)


def _open_browser(
    context: RunContext[int],
    *,
    host: str,
    port: int,
    headless: bool,
    no_open: bool,
    browser_url: str | None,
    external: bool,
    settings: Path | None,
) -> None:
    if headless or no_open:
        context.skip(_BROWSER_STEP)
        return
    context.action(_BROWSER_STEP)
    target = browser_url
    if target is None and external:
        publication = PublicationSettings.load(settings)
        target = f"https://{publication.panel_host_label}.{publication.domain_suffix}/"
    if target is None:
        url_host = f"[{host}]" if ":" in host else host
        target = f"http://{url_host}:{port}/"
    webbrowser.open(target)
    context.complete_action(_BROWSER_STEP)


def _require_dashboard_extra() -> None:
    if importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("uvicorn") is None:
        raise SystemExit(
            "monitor command requires the dashboard extra: pip install odoo-instance-sdk[dashboard]"
        )


def _wait_ready(host: str, port: int) -> None:
    url_host = f"[{host}]" if ":" in host else host
    url = f"http://{url_host}:{port}/healthz"
    deadline = time.monotonic() + 30.0
    last = "monitor did not become ready"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last = str(exc)
        time.sleep(0.1)
    raise InstanceConfigurationError(last)


def _save_state(host: str, port: int, headless: bool, settings: Path | None) -> None:
    path = monitor_state_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "host": host,
            "port": port,
            "headless": headless,
            "settings": None if settings is None else str(settings),
        }
    )
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o600)


def _load_state() -> _SavedMonitor | None:
    path = monitor_state_path()
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return None
    host = loaded.get("host")
    port = loaded.get("port")
    if not isinstance(host, str) or isinstance(port, bool) or not isinstance(port, int):
        return None
    headless = loaded.get("headless")
    settings = loaded.get("settings")
    return _SavedMonitor(
        host=host,
        port=port,
        headless=headless if isinstance(headless, bool) else False,
        settings=settings if isinstance(settings, str) else None,
    )


def _saved_argv() -> tuple[str, ...] | None:
    saved = _load_state()
    if saved is None:
        return None
    return monitor_argv(
        host=saved.host,
        port=saved.port,
        headless=saved.headless,
        settings=_saved_settings(saved),
    )


def _saved_settings(saved: _SavedMonitor | None = None) -> Path | None:
    state = _load_state() if saved is None else saved
    if state is None or not state.settings:
        return None
    return Path(state.settings)


def _output(result: ProcessResult) -> str:
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    return f"{stdout}\n{stderr}"
