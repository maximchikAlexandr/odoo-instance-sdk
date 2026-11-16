"""Spawn the captured debug target and one reproduction process."""

from __future__ import annotations

import re
import time
from pathlib import Path

from odoo_instance_sdk.exceptions import DebugError
from odoo_instance_sdk.internal.proc.run import ProcessHandle
from odoo_instance_sdk.internal.proc.spawn import spawn

_PORT = re.compile(r"Adapter is accepting incoming client connections on 127\.0\.0\.1:(\d+)")


class TargetLauncher:
    """Long-lived process owner used by the debug daemon."""

    def start(self, argv: tuple[str, ...], cwd: Path) -> ProcessHandle:
        """Spawn one captured argv through the shared process boundary."""
        handle = spawn(argv, cwd=cwd, inherit_stdio=False)
        if handle.stdin is not None:
            handle.stdin.close()
        return handle

    def listen_port(self, handle: ProcessHandle, timeout: float) -> int:
        """Read the loopback port that debugpy prints before accepting DAP."""
        deadline = time.monotonic() + timeout
        log_dir = _log_directory(handle.argv)
        while time.monotonic() < deadline:
            tails = handle.drain_tails()
            matches = list(
                _PORT.finditer(f"{tails['stderr']}\n{tails['stdout']}\n{_read_logs(log_dir)}")
            )
            if matches:
                return int(matches[-1].group(1))
            if handle.poll() is not None:
                raise DebugError("protocol-failure", "debug target exited before listening")
            time.sleep(0.05)
        raise DebugError("protocol-failure", "debug target did not report a listen port")

    def stop(self, handle: ProcessHandle | None) -> int | None:
        """Terminate one owned process group and return its exit code."""
        if handle is None:
            return None
        handle.terminate()
        return handle.poll()


def _log_directory(argv: tuple[str, ...]) -> Path | None:
    for index, token in enumerate(argv):
        if token == "--log-to" and index + 1 < len(argv):
            return Path(argv[index + 1])
    return None


def _read_logs(directory: Path | None) -> str:
    if directory is None or not directory.is_dir():
        return ""
    chunks: list[str] = []
    for path in sorted(directory.glob("*.log")):
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)
