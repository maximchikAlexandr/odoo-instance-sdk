"""Persist and clear the runtime row around a foreground launch."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance.auxiliary_restore import (
    _process_create_time,
    _runtime_owner,
    _worktree_ref,
)
from odoo_instance_sdk.resources.instance.runtime import (
    T,
    _encode_launch_identity,
    _RuntimeCatalog,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedStep, RunContext
    from odoo_instance_sdk.resources.instance import OdooInstance


def clear_foreground_runtime(instance: OdooInstance) -> None:
    """Best-effort cleanup of the persisted runtime identity.

    Catalog cleanup failures are diagnostic only.
    """
    binding = instance._runtime_binding
    environment_id = instance._environment_id
    if binding is None and environment_id is None:
        return
    try:
        catalog = cast("_RuntimeCatalog", instance._client.get_catalog())
        catalog._clear_runtime(*_runtime_owner(binding, environment_id))
    except Exception as error:
        print(f"failed to clear environment runtime: {error}", file=sys.stderr)


def persist_foreground_runtime(
    instance: OdooInstance,
    root_pid: int,
    config: StartConfig,
    cwd: str | Path | None,
    step: PreparedStep,
    executable_prefix_length: int,
    context: RunContext[T] | None = None,
) -> None:
    """Persist the exact runtime identity before foreground waiting begins."""
    binding = instance._runtime_binding
    environment_id = instance._environment_id
    if binding is None and environment_id is None:
        return
    create_time = _process_create_time(root_pid)
    launch_identity_json = _encode_launch_identity(
        step, executable_prefix_length=executable_prefix_length
    )
    checkout_branch, commit_sha = _worktree_ref(cwd, context=context)
    http_url = f"http://{config.http_interface}:{config.http_port}"
    catalog = cast("_RuntimeCatalog", instance._client.get_catalog())
    if binding is not None:
        catalog._register_project(
            binding.project_id, binding.repository_root, binding.git_common_dir
        )
        catalog._upsert_runtime(
            binding.owner_kind,
            binding.owner_id,
            root_pid=root_pid,
            create_time=create_time,
            started_at=datetime.now(UTC).isoformat(),
            checkout_branch=checkout_branch,
            commit_sha=commit_sha,
            http_url=http_url,
            http_port=config.http_port,
            database_name=config.db_name or "",
            launch_identity_json=launch_identity_json,
        )
        return
    assert environment_id is not None
    catalog._upsert_runtime(
        "environment",
        environment_id,
        root_pid=root_pid,
        create_time=create_time,
        started_at=datetime.now(UTC).isoformat(),
        checkout_branch=checkout_branch,
        commit_sha=commit_sha,
        http_url=http_url,
        http_port=config.http_port,
        database_name=config.db_name or "",
        launch_identity_json=launch_identity_json,
    )
