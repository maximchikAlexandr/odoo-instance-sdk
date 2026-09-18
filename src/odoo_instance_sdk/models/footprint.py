from __future__ import annotations

# ruff: noqa: F821
from datetime import datetime

import msgspec

from odoo_instance_sdk.models._literals import (
    ClusterUnavailabilityReason,
)


class PythonEnvFootprint(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    owned: bool
    bytes: int | None


class DatabaseFootprint(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    owned: bool
    postgres_bytes: int | None
    filestore_bytes: int | None
    total_bytes: int | None


class StorageFootprint(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    total_bytes: int
    complete: bool
    worktree_bytes: int | None
    python_environment: PythonEnvFootprint
    database: DatabaseFootprint
    other_files_bytes: int | None


class EnvironmentArtifacts(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    worktree_exists: bool
    worktree_registered: bool
    config_exists: bool
    python_exists: bool
    python_contained: bool
    dependency_lock_exists: bool
    backup_exists: bool | None


class RuntimeMetrics(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    state: RuntimeState
    root_pid: int | None
    child_pids: tuple[int, ...]
    process_count: int
    cpu_percent: float | None
    memory_bytes: int | None
    started_at: datetime | None
    http_url: str | None
    http_port: int | None
    database_name: str | None
    commit_sha: str | None
    branch: str | None


class ClusterContainer(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    id: str | None
    name: str | None
    image: str | None
    pid: int | None
    pid_scope: PidScope


class ClusterMetrics(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    cpu_percent: float | None
    memory_usage_bytes: int | None
    memory_limit_bytes: int | None
    volume_usage_bytes: int | None
    sampled_at: datetime | None


class ClusterEndpoint(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    host: str
    port: int


class ClusterResourceSnapshot(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    container: ClusterContainer | None
    metrics: ClusterMetrics | None
    unavailability_reason: ClusterUnavailabilityReason | None
    sampled_at: datetime | None
