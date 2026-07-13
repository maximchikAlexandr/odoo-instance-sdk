from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk._health import poll_health
from odoo_instance_sdk.exceptions import CommandTimeoutError
from odoo_instance_sdk.models import (
    CommandResult,
    OdooProcess,
    ProcessStatus,
    ReadinessResult,
    StartConfig,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


_START_CONFIG_CLI_MAP: tuple[tuple[str, str], ...] = (
    ("http_port", "--http-port"),
    ("http_interface", "--http-interface"),
    ("config_path", "--config"),
    ("addons_path", "--addons-path"),
    ("data_dir", "--data-dir"),
    ("dbfilter", "--db-filter"),
    ("workers", "--workers"),
    ("max_cron_threads", "--max-cron-threads"),
    ("log_level", "--log-level"),
    ("log_handler", "--log-handler"),
    ("dev_mode", "--dev"),
    ("db_host", "--db-host"),
    ("db_port", "--db-port"),
    ("db_user", "--db-user"),
    ("db_password", "--db-password"),
    ("db_name", "--db-name"),
    ("load_language", "--load-language"),
)


def _build_cli_args(config: StartConfig) -> list[str]:
    """Build Odoo CLI arguments from typed StartConfig."""
    args: list[str] = []
    for field_name, cli_flag in _START_CONFIG_CLI_MAP:
        value = getattr(config, field_name)
        if value is None:
            continue
        if isinstance(value, list):
            args.extend([cli_flag, ",".join(value)])
        else:
            args.extend([cli_flag, str(value)])
    return args


def _kill_pg(proc: subprocess.Popen[bytes], *, force: bool) -> None:
    """Terminate (SIGTERM/taskkill) or force-kill (SIGKILL/taskkill /F) the process group."""
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


@dataclass
class ServerResource:
    """Server lifecycle and CLI resource."""

    _client: OdooClient
    _handles: dict[str, subprocess.Popen[bytes]] = field(default_factory=dict)

    def register(self, proc: OdooProcess, handle: subprocess.Popen[bytes]) -> None:
        """Register a process and its subprocess handle."""
        self._client._processes[proc.id] = proc
        self._handles[proc.id] = handle

    def get_handle(self, proc_id: str) -> subprocess.Popen[bytes] | None:
        """Return the subprocess handle for a registered process, or None."""
        return self._handles.get(proc_id)

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        executable = os.fspath(self._client.config.executable)
        full_args = [executable, *args]

        start = time.perf_counter()
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            full_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_pg(proc, force=True)
            raise CommandTimeoutError(f"Command timed out after {timeout}s")

        duration = time.perf_counter() - start
        stdout, stderr = proc.communicate()

        return CommandResult(
            args=full_args,
            returncode=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            duration=duration,
        )

    def start(
        self,
        config: StartConfig,
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> OdooProcess:
        executable = os.fspath(self._client.config.executable)
        cli_args = _build_cli_args(config)
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

        self.register(odoo_proc, proc)
        return odoo_proc

    def status(self, proc: OdooProcess) -> ProcessStatus:
        self._client._get_process(proc.id)

        handle = self.get_handle(proc.id)
        if handle is None:
            return ProcessStatus(state="exited")

        rc = handle.poll()
        if rc is None:
            return ProcessStatus(state="running")
        return ProcessStatus(state="exited", returncode=rc)

    def stop(
        self,
        proc: OdooProcess,
        *,
        timeout: float = 10.0,
    ) -> None:
        handle = self._handles.pop(proc.id, None)
        self._client._processes.pop(proc.id, None)
        if handle is None:
            return

        if handle.poll() is not None:
            return

        _kill_pg(handle, force=False)
        try:
            handle.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_pg(handle, force=True)
            handle.wait()

    def wait_ready(
        self,
        proc: OdooProcess,
        *,
        timeout: float = 60.0,
    ) -> ReadinessResult:
        self._client._get_process(proc.id)

        def _alive() -> bool:
            handle = self.get_handle(proc.id)
            return handle is not None and handle.poll() is None

        return poll_health(
            self._client.config,
            timeout=timeout,
            alive_check=_alive,
        )
