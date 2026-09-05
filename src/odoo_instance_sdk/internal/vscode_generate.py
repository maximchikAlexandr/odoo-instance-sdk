from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.internal.server import _build_cli_args
from odoo_instance_sdk.models import StartConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.commands.context import RuntimeView
    from odoo_instance_sdk.execution import JsonValue

_MUTATING_FLAGS = {"-u", "-i", "--update", "--init", "--stop-after-init"}


def build_launch_profile(runtime: RuntimeView) -> dict[str, JsonValue]:
    """Build one debugpy profile from the resolved owner-neutral runtime."""
    odoo_bin = _odoo_bin_from_prefix(runtime.command_prefix)
    args = _build_profile_args(runtime.start_config, runtime.database or "")

    return {
        "name": f"Odoo {runtime.environment_name or runtime.project_id}",
        "type": "python",
        "request": "launch",
        "python": str(runtime.python_path),
        "program": odoo_bin,
        "cwd": str(runtime.root),
        "args": list(args),
        "justMyCode": False,
        "console": "integratedTerminal",
    }


def _odoo_bin_from_prefix(command_prefix: tuple[str, ...]) -> str:
    if not command_prefix:
        raise RuntimeError("resolved runtime has no command prefix")
    return command_prefix[-1]


def _build_profile_args(start_cfg: StartConfig, bound_db: str) -> list[str]:
    raw_args = _build_cli_args(start_cfg)
    args: list[str] = []
    skip_next = False
    for tok in raw_args:
        if skip_next:
            skip_next = False
            continue
        if tok in _MUTATING_FLAGS:
            skip_next = True
            continue
        if tok == "--db-name":
            args.append("--database")
        else:
            args.append(tok)
    if bound_db and "--database" not in args:
        args.extend(["--database", bound_db])
    return args


def launch_json(profile: dict[str, JsonValue]) -> str:
    envelope = {"version": "0.2.0", "configurations": [profile]}
    return json.dumps(envelope, indent=2) + "\n"


def write_launch_json(project_path: Path, content: str) -> Path:
    vscode_dir = project_path / ".vscode"
    target = vscode_dir / "launch.json"
    if target.exists():
        raise RuntimeError("refuses merge/rewrite existing JSONC")
    vscode_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(vscode_dir), prefix=".launch-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, target)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return target
