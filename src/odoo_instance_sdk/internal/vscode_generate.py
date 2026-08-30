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
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.resources.environment import DevelopmentEnvironment

_MUTATING_FLAGS = {"-u", "-i", "--update", "--init", "--stop-after-init"}


def build_launch_profile(client: OdooClient, env: DevelopmentEnvironment) -> dict[str, JsonValue]:
    from odoo_instance_sdk.resources.environment import (
        EnvironmentDatabaseMode,
    )

    odoo_bin = _resolve_odoo_bin(client, env)
    python_bin = _resolve_python_binary(env)
    start_cfg, bound_db = _resolve_start_config(env, env.db_mode == EnvironmentDatabaseMode.SHARED)
    args = _build_profile_args(start_cfg, bound_db)

    return {
        "name": f"Odoo {env.name}",
        "type": "python",
        "request": "launch",
        "python": python_bin,
        "program": odoo_bin,
        "cwd": env.worktree_path,
        "args": list(args),
        "justMyCode": False,
        "console": "integratedTerminal",
    }


def _resolve_odoo_bin(client: OdooClient, env: DevelopmentEnvironment) -> str:
    from odoo_instance_sdk.resources.environment import _decode_runtime_json

    row = client.get_catalog().get_environment(str(env.id))
    runtime_raw: str | None = None
    if row is not None:
        try:
            raw = row["runtime_json"]
            runtime_raw = raw if isinstance(raw, str) else None
        except (KeyError, IndexError):
            runtime_raw = None
    runtime = _decode_runtime_json(runtime_raw)
    odoo_bin = runtime.get("odoo_bin")
    if odoo_bin is None:
        raise RuntimeError(f"No odoo_bin recorded for environment {env.id}")
    return odoo_bin


def _resolve_start_config(env: DevelopmentEnvironment, shared: bool) -> tuple[StartConfig, str]:
    config_path = Path(env.generated_config_path)
    if not config_path.is_file():
        raise RuntimeError(f"generated config missing: {config_path}")
    start_cfg = StartConfig.from_odoo_config(config_path)
    bound_db = env.source_db_name if shared else env.target_db_name
    if bound_db is None:
        bound_db = start_cfg.db_name or ""
    return start_cfg, bound_db


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


def _resolve_python_binary(env: DevelopmentEnvironment) -> str:
    py_path = Path(env.python_environment_path)
    if py_path.is_dir():
        return str(py_path / "bin" / "python")
    return str(py_path)
