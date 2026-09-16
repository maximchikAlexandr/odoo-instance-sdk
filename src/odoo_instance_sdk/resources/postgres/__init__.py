"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

import time as time
from typing import Any

from odoo_instance_sdk.internal import paths as _paths  # noqa: F401
from odoo_instance_sdk.internal.address import probe_address as probe_address
from odoo_instance_sdk.internal.postgres_compose import docker_available as docker_available
from odoo_instance_sdk.resources.postgres.backup_restore import *  # noqa: F403
from odoo_instance_sdk.resources.postgres.backup_restore_parts import (
    PostgresCluster as PostgresCluster,
)
from odoo_instance_sdk.resources.postgres.diagnostics import *  # noqa: F403
from odoo_instance_sdk.resources.postgres.lifecycle import (
    _resolve_project_id as _resolve_project_id,
)

_LAZY_EXPORTS: dict[str, tuple[str, str | None]] = {
    "docker_available": ("odoo_instance_sdk.internal.postgres_compose", "docker_available"),
    "probe_address": ("odoo_instance_sdk.internal.address", "probe_address"),
    "time": ("time", None),
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
