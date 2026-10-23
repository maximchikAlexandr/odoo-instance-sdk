from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import GitWorkflowError

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance


def worktree(instance: OdooInstance) -> Path:
    value = getattr(instance.config, "default_cwd", None)
    if value is None:
        start = getattr(instance.config, "start_config", None)
        config_path = getattr(start, "config_path", None)
        value = Path(config_path).parent if config_path else Path.cwd()
    root = Path(value).resolve()
    if not root.is_dir():
        raise GitWorkflowError(f"Git worktree is unavailable: {root}")
    return root
