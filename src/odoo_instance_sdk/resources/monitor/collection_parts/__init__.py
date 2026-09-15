from __future__ import annotations

# ruff: noqa: F821, F401
import asyncio
import contextlib
import json
import shutil
import sqlite3
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import httpx
from msgspec.structs import replace

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    MonitorError,
    PostgresClusterError,
    ProjectManifestNotFoundError,
)
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.address import probe_address
from odoo_instance_sdk.internal.cluster_resources import (
    BatchClusterRequest,
    collect_cluster_resource_batch,
)
from odoo_instance_sdk.internal.db_name import validate_filestore_containment
from odoo_instance_sdk.internal.git_activity import (
    _resolve_identity,
    _validated_base_ref,
    collect_git_activity_from_identity,
)
from odoo_instance_sdk.internal.git_worktree import worktree_list_porcelain
from odoo_instance_sdk.internal.postgres_compose import (
    ComposeRunner,
    SubprocessComposeRunner,
    docker_available,
)
from odoo_instance_sdk.internal.process_metrics import CpuPoint, ProcessTreeResult
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.storage_footprint import (
    DatabaseStorageInput,
    collect_storage_footprint,
)
from odoo_instance_sdk.models import (
    CheckoutInventory,
    ClusterEndpoint,
    ClusterResourceSnapshot,
    ClusterSnapshot,
    DatabaseFootprint,
    EnvironmentArtifacts,
    EnvironmentSnapshot,
    GitActivity,
    GitActivityState,
    GitDiff,
    PgAdminEligibility,
    PgAdminEligibilityState,
    PortObservation,
    PostgresClusterState,
    ProcessInventory,
    ProjectSummary,
    PythonEnvFootprint,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)
from odoo_instance_sdk.resources.environment import EnvironmentState
from odoo_instance_sdk.resources.monitor.collection_parts.collect import _CollectMixin
from odoo_instance_sdk.resources.monitor.collection_parts.snapshot import _SnapshotMixin
from odoo_instance_sdk.resources.monitor.planning import _default_monitor_executor
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, MonitorCatalogSnapshot


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentMonitor(_CollectMixin, _SnapshotMixin):
    """Read-only collector that assembles a typed ``Snapshot`` from the catalog.

    Construction is cheap (no catalog open). ``snapshot()``
    discovers environments from the catalog, groups them by ``git_common_dir``,
    resolves the project cluster, and collects per-environment runtime/git/storage
    with instance-level bounded caches. Component failures are isolated into
    partial snapshot sections; only a catalog SQLite error fails the whole call
    with ``MonitorError``.
    """

    catalog_path: Path | None = None
    process_provider: _ProcessProvider | None = None
    git_provider: _GitProvider | None = None
    docker_provider: _DockerProvider | None = None
    _cpu_points: dict[tuple[int, float], CpuPoint] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _cluster_status_cache: dict[str, tuple[float, PostgresClusterState]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _git_cache: dict[tuple[Path, str, str | None, str], tuple[float, GitActivity]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _storage_cache: dict[str, tuple[float, StorageFootprint]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _cluster_resource_cache: dict[str, tuple[float, ClusterResourceSnapshot]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _docker_runner: ComposeRunner = field(
        default_factory=SubprocessComposeRunner, repr=False, hash=False, compare=False
    )
    _executor: ProcessExecutor = field(
        default_factory=_default_monitor_executor, repr=False, hash=False, compare=False
    )
