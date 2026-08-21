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

from msgspec import structs

from odoo_instance_sdk.models import (
    CommandResult,
    OdooProcess,
    ProcessStatus,
    StartConfig,
)

_SENSITIVE_FIELDS = frozenset({"db_password", "admin_passwd", "config_path"})


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


def _kill_pg(proc: subprocess.Popen[bytes], *, force: bool) -> None:
    if sys.platform == "win32":
        args = ["taskkill", "/T", "/PID", str(proc.pid)]
        if force:
            args.append("/F")
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(args, capture_output=True, timeout=5, check=False)
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL if force else signal.SIGTERM)


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


def run_foreground_process(
    executable: str | Sequence[str],
    args: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    inherit_stdio: bool = True,
) -> int:
    prefix = [executable] if isinstance(executable, str) else list(executable)
    full_args = [*prefix, *args]
    proc = subprocess.Popen(
        full_args,
        cwd=cwd,
        env=env,
        start_new_session=True,
        stdin=None if inherit_stdio else subprocess.PIPE,
        stdout=None if inherit_stdio else subprocess.PIPE,
        stderr=None if inherit_stdio else subprocess.PIPE,
    )
    interrupted = False
    prev_handler = signal.getsignal(signal.SIGINT)

    def _on_sigint(signum: int, frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        with contextlib.suppress(OSError, ProcessLookupError):
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGINT)

    if sys.platform != "win32":
        signal.signal(signal.SIGINT, _on_sigint)
    try:
        exit_code = proc.wait()
    except KeyboardInterrupt:
        interrupted = True
        with contextlib.suppress(OSError, ProcessLookupError):
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        proc.wait()
        exit_code = 130
    finally:
        if sys.platform != "win32":
            signal.signal(signal.SIGINT, prev_handler)
    if interrupted and exit_code == 0:
        exit_code = 130
    return exit_code


def _build_shell_wrapper(source: str, argv: list[str], *, commit: bool, nonce: str) -> str:
    marker_open = f"__ODCLI_PAYLOAD__{nonce}__"
    marker_close = f"__END_PAYLOAD__{nonce}__"
    import json

    source_repr = json.dumps(source)
    argv_repr = json.dumps(argv)
    return (
        "import json as _json, sys as _sys\n"
        f"_sys.argv = [_sys.argv[0], *_json.loads({argv_repr!r})]\n"
        f"_source = _json.loads({source_repr!r})\n"
        "try:\n"
        "    exec(compile(_source, '<odcli-shell-script>', 'exec'), globals())\n"
        "finally:\n"
        "    try:\n"
        "        if env is not None and hasattr(env, 'cr') and env.cr is not None:\n"
        f"            env.cr.commit() if {commit!r} else env.cr.rollback()\n"
        "    except Exception:\n"
        "        pass\n"
        f"print({marker_open!r}, _json.dumps({{'ok': True, 'commit': {commit!r}}}), "
        f"{marker_close!r})\n"
    )


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
