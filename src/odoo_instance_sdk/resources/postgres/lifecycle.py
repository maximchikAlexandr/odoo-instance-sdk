from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TypeVar

from odoo_instance_sdk.exceptions import (
    PostgresClusterError,
)
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.models import StartConfig

T = TypeVar("T")

_DEFAULT_TIMEOUT = 60.0
_DEFAULT_STOP_TIMEOUT = 30.0
_RESOURCE_SNAPSHOT_TIMEOUT = 5.0


def _resolve_project_id(repository_root: Path) -> str:
    """Return one stable Git identity in captured and direct execution modes."""
    resolved = repository_root.resolve()
    try:
        if os.path.lexists(str(resolved / ".git")):
            return repo_key(resolved, git_common_dir(resolved))
    except OSError:
        pass
    return f"{resolved.name or 'repo'}_{hashlib.sha256(str(resolved).encode()).hexdigest()[:8]}"


def _resolve_endpoint_external(source_config: Path | None) -> tuple[str, int]:
    if source_config is None:
        raise PostgresClusterError(
            "external postgres mode requires source_config in manifest; rerun init --config"
        )
    start_cfg = StartConfig.from_odoo_config(source_config)
    host = start_cfg.db_host or "127.0.0.1"
    port = start_cfg.db_port or 5432
    return host, port
