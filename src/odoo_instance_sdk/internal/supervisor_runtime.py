"""Private per-user Supervisor for owned Odoo and monitor programs."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shlex
import socket
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.paths import get_user_root
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    RunContext,
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.proc.run import ProcessResult

WINDOWS_MESSAGE = "Supervisor-managed run, stop, and monitor are not supported on Windows"
MONITOR_PROGRAM = "odcli-monitor"
_PROGRAM_ACTION = "instance.supervisor.program"
_DAEMON_STEP = "instance.supervisor.daemon"
_UPDATE_STEP = "instance.supervisor.update"
_START_STEP = "instance.supervisor.start"
_STATUS_STEP = "instance.supervisor.status"
_CONFIRM_STEP = "instance.supervisor.confirm"
_TAIL_STEP = "instance.supervisor.tail"
_STOP_STEP = "instance.supervisor.stop"
_STATES = frozenset(
    {"RUNNING", "STARTING", "STOPPING", "STOPPED", "EXITED", "FATAL", "BACKOFF", "UNKNOWN"}
)


_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True, slots=True)
class ProgramState:
    """One parsed `supervisorctl status` row."""

    name: str
    state: str
    pid: int | None

    @property
    def running(self) -> bool:
        """Return whether Supervisor reports the program process as up."""
        return self.state == "RUNNING" and self.pid is not None


@dataclass(frozen=True, slots=True)
class SupervisorHome:
    """Owner-only files for the private daemon."""

    root: Path
    config_path: Path
    socket_path: Path
    pid_path: Path
    log_path: Path
    programs_dir: Path

    def program_path(self, program: str) -> Path:
        """Return the configuration file for one program."""
        return self.programs_dir / f"{program}.conf"

    def stdout_path(self, program: str) -> Path:
        """Return the captured stdout log for one program."""
        return self.programs_dir / f"{program}.stdout.log"


def windows_unsupported() -> bool:
    """Return whether managed Supervisor lifecycle is unavailable."""
    return sys.platform == "win32"


def require_unix_supervisor() -> None:
    """Fail closed on Windows or when the Unix package is missing."""
    if windows_unsupported():
        raise InstanceConfigurationError(WINDOWS_MESSAGE)
    if importlib.util.find_spec("supervisor") is None:
        raise InstanceConfigurationError(
            "OdCLI requires the supervisor package on Unix; reinstall odoo-instance-sdk"
        )


def production_executor(executor: ProcessExecutor) -> bool:
    """Return whether this run uses the real process boundary."""
    return type(executor) is SubprocessExecutor


def supervisor_program_name(owner_kind: str, owner_id: str) -> str:
    """Return the stable program name for one catalogue owner."""
    cleaned = "".join(char if char.isalnum() or char in "-_" else "-" for char in owner_id)
    return f"odoo-{owner_kind}-{cleaned}"[:200]


def owned_program_name(
    *,
    owner_kind: str | None,
    owner_id: str | None,
    environment_id: str | None,
) -> str | None:
    """Return the Supervisor program for a catalogue owner, when one exists."""
    if owner_kind is not None and owner_id is not None:
        return supervisor_program_name(owner_kind, owner_id)
    if environment_id:
        return supervisor_program_name("environment", environment_id)
    return None


def _socket_path(root: Path) -> Path:
    candidate = root / "supervisor.sock"
    if len(str(candidate).encode()) <= 100:
        return candidate
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    return Path("/tmp") / "odcli-supervisor" / digest / "supervisor.sock"


def supervisor_home() -> SupervisorHome:
    """Return private Supervisor paths without creating them."""
    root = get_user_root(ensure_exists=False) / "supervisor"
    return SupervisorHome(
        root=root,
        config_path=root / "supervisord.conf",
        socket_path=_socket_path(root),
        pid_path=root / "supervisord.pid",
        log_path=root / "supervisord.log",
        programs_dir=root / "programs",
    )


def launch_steps(program: str, *, follow: bool) -> tuple[PreparedAction | PreparedStep, ...]:
    """Return the immutable Supervisor steps for one program start."""
    steps: list[PreparedAction | PreparedStep] = [
        PreparedAction(
            step_id=_PROGRAM_ACTION,
            action="write_supervisor_program",
            description="Write the owned Supervisor program configuration",
            details={"program": program},
            mutating=True,
        ),
        *_control_steps(program, follow=follow),
    ]
    return tuple(steps)


def stop_steps(program: str, *, prefix: str = "") -> tuple[PreparedStep, ...]:
    """Return the immutable stop snapshot for one program."""
    return (
        _step(f"{prefix}{_STATUS_STEP}", ("status", program), read_only=True),
        _step(f"{prefix}{_STOP_STEP}", ("stop", program), mutating=True),
    )


def skip_launch(context: RunContext[_ResultT], *, follow: bool) -> None:
    """Consume Supervisor steps when the recording double spawns directly."""
    for step_id in (
        _PROGRAM_ACTION,
        _DAEMON_STEP,
        _STATUS_STEP,
        _UPDATE_STEP,
        _START_STEP,
        _CONFIRM_STEP,
        _STOP_STEP,
    ):
        _skip_if_planned(context, step_id)
    if follow:
        _skip_if_planned(context, _TAIL_STEP)


def activate_program(
    context: RunContext[_ResultT],
    *,
    program: str,
    argv: Sequence[str],
    cwd: str | None,
    environment: Sequence[tuple[str, str]] = (),
    secrets: Sequence[str] = (),
    before_start: Callable[[], None] | None = None,
) -> int:
    """Write the program, start the private daemon, and return the child pid."""
    require_unix_supervisor()
    home = _ensure_home()
    previous = _configured_command(home, program)
    state = _status_after_daemon(context, home, program, secrets)
    if state.running and previous not in (None, tuple(argv)):
        _skip_stop(context)
        raise InstanceConfigurationError(
            f"Supervisor program {program} is already running with a different command; "
            "stop it before changing the launch"
        )
    if not state.running and before_start is not None:
        before_start()
    _write_program(home, program, argv=argv, cwd=cwd, environment=environment)
    context.action(_PROGRAM_ACTION)
    context.complete_action(_PROGRAM_ACTION)
    if state.running:
        for step_id in (_UPDATE_STEP, _START_STEP, _CONFIRM_STEP):
            _skip_if_planned(context, step_id)
        _skip_stop(context)
        return int(state.pid or 0)
    _process(context, _UPDATE_STEP)
    _process(context, _START_STEP)
    confirmed = _require_running(
        redact_program_error(_text(_process(context, _CONFIRM_STEP)), secrets),
        program,
    )
    return int(confirmed.pid or 0)


def finish_launch(context: RunContext[_ResultT]) -> None:
    """Leave a started program running by not consuming its stop step."""
    _skip_stop(context)


def _skip_stop(context: RunContext[_ResultT]) -> None:
    if context.planned(_STOP_STEP) and not context.consumed(_STOP_STEP):
        context.skip(_STOP_STEP)


def abort_launch(context: RunContext[_ResultT]) -> None:
    """Stop a program started by this launch when a later step fails."""
    if context.planned(_STOP_STEP) and not context.consumed(_STOP_STEP):
        context.process(_STOP_STEP)


def follow_program(context: RunContext[_ResultT]) -> int:
    """Follow one program log until it ends or the client presses Ctrl-C."""
    try:
        result = _process(context, _TAIL_STEP)
    except KeyboardInterrupt:
        return 130
    if result.returncode == 130:
        return 130
    return 0


def stop_program(
    context: RunContext[_ResultT],
    program: str,
    *,
    expected_argv: Sequence[str] | None,
    sensitive_indices: Sequence[int] = (),
    executable_prefix_length: int = 0,
    status_step: str = _STATUS_STEP,
    stop_step: str = _STOP_STEP,
) -> str:
    """Stop one owned program. A repeated stop is success."""
    require_unix_supervisor()
    home = supervisor_home()
    if expected_argv is not None:
        configured = _configured_command(home, program)
        if configured is not None and not _same_command(
            configured,
            expected_argv,
            sensitive_indices=sensitive_indices,
            executable_prefix_length=executable_prefix_length,
        ):
            raise InstanceConfigurationError(
                f"Supervisor program {program} does not match the captured launch identity"
            )
    status = parse_status(_text(_process(context, status_step)), program)
    if status.state in {"STOPPED", "EXITED", "UNKNOWN", ""}:
        _skip_if_planned(context, stop_step)
        return "already_stopped"
    outcome = _text(_process(context, stop_step))
    if "not running" in outcome.casefold() or "no such process" in outcome.casefold():
        return "already_stopped"
    return "stopped"


def parse_status(text: str, program: str) -> ProgramState:
    """Parse one program from supervisorctl status text."""
    folded = text.casefold()
    if "no such process" in folded or "no such file" in folded:
        return ProgramState(program, "STOPPED", None)
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != program or parts[1] not in _STATES:
            continue
        pid = None
        if "pid" in parts:
            index = parts.index("pid")
            if index + 1 < len(parts):
                token = parts[index + 1].rstrip(",")
                pid = int(token) if token.isdigit() else None
        return ProgramState(program, parts[1], pid)
    return ProgramState(program, "STOPPED", None)


def supervisor_reachable() -> bool:
    """Return whether the private daemon accepts a socket connection."""
    return _socket_ready(supervisor_home().socket_path)


def program_installed(program: str) -> bool:
    """Return whether this owner already has a program file."""
    return supervisor_home().program_path(program).is_file()


def _control_steps(program: str, *, follow: bool) -> tuple[PreparedStep, ...]:
    steps = [
        _step(_DAEMON_STEP, (), daemon=True),
        _step(_STATUS_STEP, ("status", program), read_only=True),
        _step(_UPDATE_STEP, ("update", program), mutating=True),
        _step(_START_STEP, ("start", program), mutating=True),
        _step(_CONFIRM_STEP, ("status", program), read_only=True),
        _step(_STOP_STEP, ("stop", program), mutating=True),
    ]
    if follow:
        steps.append(
            PreparedStep(
                step_id=_TAIL_STEP,
                argv=_ctl("tail", "-f", program),
                mode="foreground",
                inherit_stdio=True,
                long_running=True,
            )
        )
    return tuple(steps)


def _step(
    step_id: str,
    args: tuple[str, ...],
    *,
    read_only: bool = False,
    mutating: bool = False,
    daemon: bool = False,
) -> PreparedStep:
    argv = _daemon_argv() if daemon else _ctl(*args)
    return PreparedStep(
        step_id=step_id,
        argv=argv,
        timeout=30.0,
        mode="captured",
        read_only=read_only,
        mutating=mutating,
    )


def _daemon_argv() -> tuple[str, ...]:
    config = str(supervisor_home().config_path)
    return (sys.executable, "-m", "supervisor.supervisord", "-c", config)


def supervisorctl_argv(*args: str) -> tuple[str, ...]:
    """Return one immutable supervisorctl command for the private daemon."""
    return _ctl(*args)


def _ctl(*args: str) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "supervisor.supervisorctl",
        "-c",
        str(supervisor_home().config_path),
        *args,
    )


def _ensure_home() -> SupervisorHome:
    home = supervisor_home()
    home.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(home.socket_path.parent, 0o700)
    os.chmod(home.root, 0o700)
    home.programs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(home.programs_dir, 0o700)
    _write_private(
        home.config_path,
        "\n".join(
            (
                "[unix_http_server]",
                f"file={home.socket_path}",
                "chmod=0700",
                "",
                "[supervisord]",
                f"logfile={home.log_path}",
                f"pidfile={home.pid_path}",
                f"childlogdir={home.programs_dir}",
                "nodaemon=false",
                "umask=077",
                "",
                "[rpcinterface:supervisor]",
                "supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface",
                "",
                "[supervisorctl]",
                f"serverurl=unix://{home.socket_path}",
                "",
                "[include]",
                f"files = {home.programs_dir}/*.conf",
                "",
            )
        ),
    )
    return home


def _write_program(
    home: SupervisorHome,
    program: str,
    *,
    argv: Sequence[str],
    cwd: str | None,
    environment: Sequence[tuple[str, str]],
) -> None:
    if any("\n" in arg or "\r" in arg for arg in argv):
        raise InstanceConfigurationError("Supervisor program command cannot contain newlines")
    if cwd is not None and any(char in cwd for char in "\n\r"):
        raise InstanceConfigurationError("Supervisor program directory cannot contain newlines")
    lines = [
        f"[program:{program}]",
        f"command={shlex.join(argv)}",
        "autostart=false",
        "autorestart=unexpected",
        "startsecs=1",
        "startretries=3",
        "exitcodes=0",
        "stopsignal=TERM",
        "stopwaitsecs=10",
        "stopasgroup=true",
        "killasgroup=true",
        "redirect_stderr=true",
        f"stdout_logfile={home.stdout_path(program)}",
        "stdout_logfile_maxbytes=10MB",
        "stdout_logfile_backups=1",
    ]
    if cwd:
        lines.insert(2, f"directory={cwd}")
    encoded = _environment_line(environment)
    if encoded:
        lines.append(f"environment={encoded}")
    _write_private(home.program_path(program), "\n".join(lines) + "\n")


def _environment_line(environment: Sequence[tuple[str, str]]) -> str:
    parts: list[str] = []
    for key, value in environment:
        if (
            not key
            or not (key[0].isalpha() or key[0] == "_")
            or any(not (char.isalnum() or char == "_") for char in key)
        ):
            raise InstanceConfigurationError(
                "Supervisor program environment key must be an identifier"
            )
        if any(char in value for char in ',\n\r"'):
            raise InstanceConfigurationError("Supervisor program environment cannot encode a value")
        parts.append(f'{key}="{value}"')
    return ",".join(parts)


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _same_command(
    configured: Sequence[str],
    expected: Sequence[str],
    *,
    sensitive_indices: Sequence[int],
    executable_prefix_length: int,
) -> bool:
    from odoo_instance_sdk.resources.instance.runtime import _project_runtime_argv

    if executable_prefix_length <= 0:
        return tuple(configured) == tuple(expected)
    return _project_runtime_argv(
        configured,
        sensitive_argv_indices=sensitive_indices,
        executable_prefix_length=executable_prefix_length,
    ) == tuple(expected)


def _configured_command(home: SupervisorHome, program: str) -> tuple[str, ...] | None:
    path = home.program_path(program)
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("command="):
            return tuple(shlex.split(line.removeprefix("command=")))
    return None


def _status_after_daemon(
    context: RunContext[_ResultT],
    home: SupervisorHome,
    program: str,
    secrets: Sequence[str] = (),
) -> ProgramState:
    if not _socket_ready(home.socket_path):
        started = _text(_process(context, _DAEMON_STEP))
        deadline = time.monotonic() + 5
        while not _socket_ready(home.socket_path):
            if time.monotonic() >= deadline:
                detail = started.strip() or "the daemon exited without opening its socket"
                raise InstanceConfigurationError(
                    redact_program_error(
                        f"private Supervisor did not become reachable: {detail}",
                        secrets,
                    )
                )
            time.sleep(0.05)
    else:
        _skip_if_planned(context, _DAEMON_STEP)
    for path in (home.log_path, home.stdout_path(program)):
        if path.is_file():
            os.chmod(path, 0o600)
    return parse_status(
        redact_program_error(_text(_process(context, _STATUS_STEP)), secrets),
        program,
    )


def _socket_ready(path: Path) -> bool:
    if not path.exists():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.2)
        probe.connect(str(path))
    except OSError:
        return False
    else:
        return True
    finally:
        probe.close()


def _require_running(text: str, program: str) -> ProgramState:
    state = parse_status(text, program)
    if state.running:
        return state
    raise InstanceConfigurationError(
        f"Supervisor program {program} entered {state.state or 'UNKNOWN'} and was not started"
    )


def _process(context: RunContext[_ResultT], step_id: str) -> ProcessResult:
    result = context.process(step_id)
    if not isinstance(result, ProcessResult):
        raise InstanceConfigurationError("Supervisor step did not return a process result")
    return result


def _skip_if_planned(context: RunContext[_ResultT], step_id: str) -> None:
    if context.planned(step_id):
        context.skip(step_id)


def _text(result: ProcessResult) -> str:
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    return f"{stdout}\n{stderr}"


def monitor_state_path() -> Path:
    """Return the owner-only saved monitor endpoint file."""
    return supervisor_home().root / "monitor.json"


def redact_program_error(message: str, secrets: Sequence[str]) -> str:
    """Remove known secret values from a Supervisor diagnostic."""
    redacted = message
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted
