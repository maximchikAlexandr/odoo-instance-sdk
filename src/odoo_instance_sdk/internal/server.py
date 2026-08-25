from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from msgspec import structs

from odoo_instance_sdk.models import (
    CommandResult,
    OdooProcess,
    ProcessStatus,
    StartConfig,
)

_SENSITIVE_FIELDS = frozenset({"db_password", "admin_passwd", "config_path", "logfile"})
_FOREGROUND_PROCESS_CLEANUP_TIMEOUT = 5.0


def _cli_flag(field_name: str) -> str:
    if field_name == "dev_mode":
        return "--dev"
    return "--" + field_name.replace("_", "-")


def _build_cli_args(config: StartConfig, *, secret_config_path: str | None = None) -> list[str]:
    args: list[str] = []
    for f in structs.fields(StartConfig):
        if f.name in _SENSITIVE_FIELDS:
            continue
        value = getattr(config, f.name)
        if value is None:
            continue
        flag = _cli_flag(f.name)
        if isinstance(value, list):
            args.extend([flag, ",".join(value)])
        else:
            args.extend([flag, str(value)])
    if config.config_path is not None:
        args.extend(["--config", config.config_path])
    elif secret_config_path is not None:
        args.extend(["--config", secret_config_path])
    return args


def _write_secret_config(config: StartConfig) -> str | None:
    db_password = getattr(config, "db_password", None)
    if db_password is None:
        return None
    fd, path = tempfile.mkstemp(suffix=".conf", prefix="odoo-sdk-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("[options]\n")
            f.write(f"db_password = {db_password}\n")
        os.chmod(path, 0o600)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise
    return path


def _kill_pg(
    proc: subprocess.Popen[bytes],
    *,
    force: bool,
    process_group_id: int | None = None,
) -> None:
    if sys.platform == "win32":
        args = ["taskkill", "/T", "/PID", str(proc.pid)]
        if force:
            args.append("/F")
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(args, capture_output=True, timeout=5, check=False)
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        # A foreground child is spawned with ``start_new_session=True``.  Its
        # PID is therefore the session/process-group ID at spawn time.  Keep
        # using that known value during exceptional cleanup: the leader may
        # already have exited while descendants of its group are still alive,
        # in which case ``os.getpgid(proc.pid)`` cannot recover the group.
        pgid = process_group_id if process_group_id is not None else os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL if force else signal.SIGTERM)


def _process_group_is_alive(process_group_id: int) -> bool:
    """Return whether a POSIX process group still exists.

    ``killpg(..., 0)`` observes the group without signalling it.  A permission
    error still proves that the group exists; only ``ProcessLookupError`` means
    it is gone.
    """
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_group_exit(
    proc: subprocess.Popen[bytes], process_group_id: int, *, timeout: float
) -> bool:
    """Wait a bounded period for the captured POSIX process group to vanish."""
    deadline = time.monotonic() + timeout
    while True:
        # ``Popen.poll`` reaps an exited leader.  Without it a zombie leader
        # keeps its PGID observable even after the actual process group ended.
        proc.poll()
        if not _process_group_is_alive(process_group_id):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def start_process(
    executable: str | Sequence[str],
    config: StartConfig,
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[OdooProcess, subprocess.Popen[bytes], str | None]:
    secret_config_path: str | None = None
    if config.config_path is None:
        secret_config_path = _write_secret_config(config)
    cli_args = _build_cli_args(config, secret_config_path=secret_config_path)
    prefix = [executable] if isinstance(executable, str) else list(executable)
    full_args = [*prefix, *cli_args]

    proc = subprocess.Popen(
        full_args,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )

    odoo_proc = OdooProcess(
        id=uuid.uuid4().hex,
        pid=proc.pid,
        args=full_args,
        started_at=time.time(),
    )

    return odoo_proc, proc, secret_config_path


def stop_process(
    handle: subprocess.Popen[bytes],
    *,
    timeout: float = 10.0,
    secret_config_path: str | None = None,
) -> None:
    if handle.poll() is None:
        _kill_pg(handle, force=False)
        try:
            handle.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_pg(handle, force=True)
            handle.wait()
    cleanup_secret_config(secret_config_path)


def cleanup_secret_config(secret_config_path: str | None) -> None:
    if secret_config_path is not None:
        with contextlib.suppress(OSError):
            os.unlink(secret_config_path)


def get_process_status(
    handle: subprocess.Popen[bytes] | None,
) -> ProcessStatus:
    if handle is None:
        return ProcessStatus(state="exited")
    rc = handle.poll()
    if rc is None:
        return ProcessStatus(state="running")
    return ProcessStatus(state="exited", returncode=rc)


def run_command(
    executable: str | Sequence[str],
    args: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> CommandResult:
    prefix = [executable] if isinstance(executable, str) else list(executable)
    full_args = [*prefix, *args]
    start = time.perf_counter()
    proc = subprocess.run(
        full_args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        args=full_args,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration=time.perf_counter() - start,
    )


def spawn_foreground_process(
    executable: str | Sequence[str],
    args: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    inherit_stdio: bool = True,
) -> subprocess.Popen[bytes]:
    """Spawn a foreground process without waiting. Caller owns ``proc.wait()``.

    The returned process is in its own session (``start_new_session=True``)
    so a Ctrl+C forwarded via ``os.killpg`` reaches the whole tree.
    """
    prefix = [executable] if isinstance(executable, str) else list(executable)
    full_args = [*prefix, *args]
    return subprocess.Popen(
        full_args,
        cwd=cwd,
        env=env,
        start_new_session=True,
        stdin=None if inherit_stdio else subprocess.PIPE,
        stdout=None if inherit_stdio else subprocess.PIPE,
        stderr=None if inherit_stdio else subprocess.PIPE,
    )


def terminate_foreground_process(
    proc: subprocess.Popen[bytes], *, process_group_id: int | None = None
) -> None:
    """Terminate an owned foreground process group and reap its leader.

    ``process_group_id`` is captured at spawn for cleanup after a leader has
    exited.  On POSIX it remains valid for a surviving descendant group.
    """
    if sys.platform == "win32":
        _kill_pg(proc, force=False, process_group_id=process_group_id)
        try:
            proc.wait(timeout=_FOREGROUND_PROCESS_CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill_pg(proc, force=True, process_group_id=process_group_id)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=_FOREGROUND_PROCESS_CLEANUP_TIMEOUT)
        return

    # Do not use leader liveness as a proxy for group liveness: a wrapper can
    # exit while an owned descendant ignores SIGTERM.  The saved PGID is the
    # session created at spawn, and this immediate, bounded cleanup avoids
    # discovering a group through a potentially exited/reused leader PID.
    if process_group_id is None:
        try:
            group_id = os.getpgid(proc.pid)
        except ProcessLookupError:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=_FOREGROUND_PROCESS_CLEANUP_TIMEOUT)
            return
    else:
        group_id = process_group_id
    _kill_pg(proc, force=False, process_group_id=group_id)
    if not _wait_for_process_group_exit(
        proc, group_id, timeout=_FOREGROUND_PROCESS_CLEANUP_TIMEOUT
    ):
        _kill_pg(proc, force=True, process_group_id=group_id)
        _wait_for_process_group_exit(proc, group_id, timeout=_FOREGROUND_PROCESS_CLEANUP_TIMEOUT)

    # Reap independently after addressing the entire owned group.  The caller
    # is already handling an original exception, so cleanup remains best effort.
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=_FOREGROUND_PROCESS_CLEANUP_TIMEOUT)


def wait_foreground_process_with_cleanup(proc: subprocess.Popen[bytes]) -> int:
    """Wait for an owned foreground process, reaping its group on failure.

    This is the single exceptional-wait boundary for manual and
    environment-tracked ``run_foreground`` calls. Cleanup is best-effort: it
    must not replace the original wait exception.
    """
    process_group_id = proc.pid
    try:
        return wait_foreground_process(proc)
    except BaseException:
        with contextlib.suppress(BaseException):
            terminate_foreground_process(proc, process_group_id=process_group_id)
        raise


def wait_foreground_process(proc: subprocess.Popen[bytes]) -> int:
    """Block until ``proc`` exits, terminating its owned group on Ctrl+C.

    Ctrl+C uses the same bounded TERM/KILL/reap cleanup as exceptional wait
    failures, then returns 130. Restores the previous SIGINT handler on return.
    """
    interrupted = False
    # start_new_session=True makes the leader PID the PGID at spawn.  Retain it
    # before a signal can make the leader disappear; descendants can survive
    # that exit and must still receive the bounded TERM/KILL/reap sequence.
    process_group_id = proc.pid
    prev_handler = signal.getsignal(signal.SIGINT)

    def _terminate_after_interrupt() -> None:
        with contextlib.suppress(BaseException):
            terminate_foreground_process(proc, process_group_id=process_group_id)

    def _on_sigint(signum: int, frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        _terminate_after_interrupt()

    if sys.platform != "win32":
        signal.signal(signal.SIGINT, _on_sigint)
    try:
        exit_code = proc.wait()
    except KeyboardInterrupt:
        interrupted = True
        _terminate_after_interrupt()
        exit_code = 130
    finally:
        if sys.platform != "win32":
            signal.signal(signal.SIGINT, prev_handler)
    if interrupted:
        exit_code = 130
    return exit_code


def run_foreground_process(
    executable: str | Sequence[str],
    args: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    inherit_stdio: bool = True,
) -> int:
    proc = spawn_foreground_process(executable, args, cwd=cwd, env=env, inherit_stdio=inherit_stdio)
    return wait_foreground_process_with_cleanup(proc)


_RESULT_SNIPPET = (
    "import json as _odcli_rj\n"
    "def _odcli_serialize_result(_r):\n"
    "    if _r is None or isinstance(_r, (bool, int, float, str)):\n"
    "        return _r\n"
    "    if isinstance(_r, (list, tuple)):\n"
    "        try:\n"
    "            return [_odcli_serialize_result(_x) for _x in _r]\n"
    "        except Exception:\n"
    "            return _odcli_sanitize(_r)\n"
    "    if isinstance(_r, dict):\n"
    "        try:\n"
    "            return {str(_k): _odcli_serialize_result(_v) for _k, _v in _r.items()}\n"
    "        except Exception:\n"
    "            return _odcli_sanitize(_r)\n"
    "    if hasattr(_r, 'ids') and (hasattr(_r, '_name') or hasattr(_r, '_model')):\n"
    "        _name = getattr(_r, '_name', None)\n"
    "        if _name is None and hasattr(_r, '_model'):\n"
    "            _name = getattr(_r._model, '_name', None)\n"
    "        try:\n"
    "            return {'model': _name, 'ids': list(_r.ids), 'count': len(_r)}\n"
    "        except Exception:\n"
    "            return _odcli_sanitize(_r)\n"
    "    return _odcli_sanitize(_r)\n"
    "def _odcli_sanitize(_o, _max=500):\n"
    "    try:\n"
    "        _t = repr(_o)\n"
    "    except Exception:\n"
    "        _t = '<unrepresentable>'\n"
    "    _t = ' '.join(str(_t).split())\n"
    "    if len(_t) > _max:\n"
    "        _t = _t[:_max] + '...<truncated>'\n"
    "    for _kw in ('password', 'passwd', 'token', 'secret', 'api_key', 'apikey'):\n"
    "        if _kw in _t.lower():\n"
    "            _t = '<redacted>'\n"
    "            break\n"
    "    return _t\n"
)


def _build_shell_wrapper(source: str, argv: list[str], *, commit: bool, nonce: str) -> str:
    marker_open = f"__ODCLI_PAYLOAD__{nonce}__"
    marker_close = f"__END_PAYLOAD__{nonce}__"
    import json

    source_repr = json.dumps(source)
    argv_repr = json.dumps(argv)
    payload_dict = "{'ok': True, 'commit': " + repr(commit) + "}"
    result_emit = (
        "    if 'result' in globals() and result is not None:\n"
        "        _payload.update({'result': _odcli_serialize_result(result)})\n"
    )
    return (
        "import json as _json, sys as _sys\n"
        f"_sys.argv = [_sys.argv[0], *_json.loads({argv_repr!r})]\n"
        f"_source = _json.loads({source_repr!r})\n"
        f"{_RESULT_SNIPPET}"
        "try:\n"
        "    exec(compile(_source, '<odcli-shell-script>', 'exec'), globals())\n"
        "finally:\n"
        "    try:\n"
        "        if env is not None and hasattr(env, 'cr') and env.cr is not None:\n"
        f"            env.cr.commit() if {commit!r} else env.cr.rollback()\n"
        "    except Exception:\n"
        "        pass\n"
        f"    _payload = {payload_dict}\n"
        f"{result_emit}"
        f"    print({marker_open!r}, _json.dumps(_payload), {marker_close!r})\n"
    )


def parse_payload(stdout: str, nonce: str | None = None) -> dict[str, Any] | None:
    import json as _json
    import re as _re

    if nonce is not None:
        marker_open = f"__ODCLI_PAYLOAD__{nonce}__"
        marker_close = f"__END_PAYLOAD__{nonce}__"
        start = stdout.rfind(marker_open)
        if start == -1:
            return None
        start += len(marker_open)
        end = stdout.rfind(marker_close, start)
        if end == -1:
            return None
        body = stdout[start:end].strip()
    else:
        match = None
        for m in _re.finditer(
            r"__ODCLI_PAYLOAD__([0-9a-fA-F]+)__\s*(.*?)\s*__END_PAYLOAD__\1__",
            stdout,
            _re.DOTALL,
        ):
            match = m
        if match is None:
            return None
        body = match.group(2).strip()
    if not body:
        return None
    try:
        return cast("dict[str, Any]", _json.loads(body))
    except ValueError:
        return None


def _run_captured_shell(
    executable: str | Sequence[str],
    cli_args: list[str],
    *,
    source: str,
    argv: list[str],
    timeout: float | None,
    commit: bool,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    import secrets as _secrets

    nonce = _secrets.token_hex(8)
    wrapper = _build_shell_wrapper(source, argv, commit=commit, nonce=nonce)
    prefix = [executable] if isinstance(executable, str) else list(executable)
    full_args = [*prefix, *cli_args]
    start = time.perf_counter()
    proc = subprocess.run(
        full_args,
        cwd=cwd,
        env=env,
        input=wrapper,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        args=full_args,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration=time.perf_counter() - start,
    )
