from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from msgspec import structs

from odoo_instance_sdk.models import (
    CommandResult,
    OdooProcess,
    ProcessStatus,
    StartConfig,
)

_SENSITIVE_FIELDS = frozenset({"db_password", "admin_passwd"})


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
    if secret_config_path is not None:
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
    executable: str,
    config: StartConfig,
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[OdooProcess, subprocess.Popen[bytes], str | None]:
    secret_config_path = _write_secret_config(config)
    cli_args = _build_cli_args(config, secret_config_path=secret_config_path)
    full_args = [executable, *cli_args]

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
    executable: str,
    args: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> CommandResult:
    start = time.perf_counter()
    proc = subprocess.run(
        [executable, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        args=[executable, *args],
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration=time.perf_counter() - start,
    )
