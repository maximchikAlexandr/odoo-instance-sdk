"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

import shutil as shutil
import time as time
from typing import Any

import httpx as httpx

from odoo_instance_sdk.internal.address import probe_address as probe_address
from odoo_instance_sdk.internal.git_activity import (
    _resolve_identity as _resolve_identity,
)
from odoo_instance_sdk.internal.git_activity import (
    collect_git_activity_from_identity as collect_git_activity_from_identity,
)
from odoo_instance_sdk.internal.git_worktree import (
    worktree_list_porcelain as worktree_list_porcelain,
)
from odoo_instance_sdk.internal.postgres_compose import docker_available as docker_available
from odoo_instance_sdk.internal.repo_key import repo_key as repo_key
from odoo_instance_sdk.resources.monitor.collection_parts import *  # noqa: F403
from odoo_instance_sdk.resources.monitor.collection_parts import (
    EnvironmentMonitor as EnvironmentMonitor,
)
from odoo_instance_sdk.resources.monitor.planning import (
    SnapshotSelection as SnapshotSelection,
)
from odoo_instance_sdk.resources.monitor.planning import (
    _recorded_git_activity as _recorded_git_activity,
)
from odoo_instance_sdk.resources.monitor.planning import (
    select_snapshot_environment as select_snapshot_environment,
)

_LAZY_EXPORTS: dict[str, tuple[str, str | None]] = {
    "_PROBE_TIMEOUT_SECONDS": (
        "odoo_instance_sdk.resources.monitor.planning",
        "_PROBE_TIMEOUT_SECONDS",
    ),
    "_resolve_identity": ("odoo_instance_sdk.internal.git_activity", "_resolve_identity"),
    "collect_git_activity_from_identity": (
        "odoo_instance_sdk.internal.git_activity",
        "collect_git_activity_from_identity",
    ),
    "docker_available": ("odoo_instance_sdk.internal.postgres_compose", "docker_available"),
    "httpx": ("httpx", None),
    "probe_address": ("odoo_instance_sdk.internal.address", "probe_address"),
    "repo_key": ("odoo_instance_sdk.internal.repo_key", "repo_key"),
    "shutil": ("shutil", None),
    "time": ("time", None),
    "worktree_list_porcelain": (
        "odoo_instance_sdk.internal.git_worktree",
        "worktree_list_porcelain",
    ),
}


def __getattr__(name: str) -> Any:
    spec = _LAZY_EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = spec
    from importlib import import_module

    module = import_module(module_name)
    value = module if attr_name is None else getattr(module, attr_name)
    globals()[name] = value
    return value
