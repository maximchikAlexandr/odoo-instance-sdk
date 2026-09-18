"""The single boundary for SDK-owned child-process effects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from odoo_instance_sdk.internal.proc.run import ProcessHandle, SubprocessExecutor, prepared_step


def spawn(
    executable: str | Sequence[str],
    args: Sequence[str] = (),
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    mode: str = "foreground",
    inherit_stdio: bool = True,
) -> ProcessHandle:
    step = prepared_step(
        executable,
        args,
        cwd=cwd,
        env=env,
        mode=mode,
        start_new_session=True,
        inherit_stdio=inherit_stdio,
    )
    return SubprocessExecutor().spawn(step)
