from __future__ import annotations

from typing import Annotated, Literal

import msgspec


class DatabaseInventoryItem(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One exact database identity and its read-only local relationships."""

    cluster: str
    cluster_id: str | None
    name: str
    logical_size_bytes: int | None
    active_sessions: int
    is_default: bool
    environment_ids: tuple[str, ...] = ()
    runtime_bindings: tuple[str, ...] = ()
    restore_backup_ids: tuple[str, ...] = ()
    origin: Literal["restore", "unknown"] = "unknown"


class DatabaseInventoryResult(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """Deterministic project-cluster database inventory."""

    cluster: str
    databases: tuple[DatabaseInventoryItem, ...]
    tracked: Annotated[bool, "odcli-structural"] = False
