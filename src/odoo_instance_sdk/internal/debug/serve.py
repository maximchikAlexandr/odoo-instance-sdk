"""Daemon entry point for one headless debug session."""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Mapping
from pathlib import Path
from types import FrameType
from typing import cast

import msgspec

from odoo_instance_sdk.exceptions import DebugError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.debug.controller import DebugController
from odoo_instance_sdk.internal.debug.session import serve_rpc, write_session_file


def main(argv: list[str]) -> int:
    """Own one session directory until stop, failure, or termination."""
    if len(argv) != 2:
        return 2
    directory = Path(argv[1])
    _reopen_stdio(directory / "daemon.log")
    controller = DebugController(directory)
    _install_signals(controller)
    try:
        snapshot = controller.bootstrap()
    except DebugError as exc:
        write_session_file(
            directory / "error.json",
            {"code": exc.code, "message": str(exc), "hint": exc.hint, "state": exc.state},
        )
        controller.stop_children()
        return 1
    write_session_file(
        directory / "ready.json",
        cast("Mapping[str, JsonValue]", msgspec.to_builtins(snapshot)),
    )
    serve_rpc(directory / "session.sock", controller.handle, controller.stopped)
    controller.cleanup()
    return 0


def _reopen_stdio(path: Path) -> None:
    """Detach daemon diagnostics from the short-lived parent pipes."""
    log = path.open("ab")
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    log.close()


def _install_signals(controller: DebugController) -> None:
    def _exit(signum: int, _frame: FrameType | None) -> None:
        controller.cleanup()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, _exit)
    signal.signal(signal.SIGINT, _exit)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
