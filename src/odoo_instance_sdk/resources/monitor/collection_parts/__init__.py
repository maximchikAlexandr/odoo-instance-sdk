from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from odoo_instance_sdk.internal.postgres_compose import ComposeRunner, SubprocessComposeRunner
from odoo_instance_sdk.internal.proc import ProcessExecutor
from odoo_instance_sdk.internal.process_metrics import CpuPoint
from odoo_instance_sdk.models import (
    ClusterResourceSnapshot,
    GitActivity,
    PostgresClusterState,
    StorageFootprint,
)
from odoo_instance_sdk.resources.monitor.collection_parts.collect import _CollectMixin
from odoo_instance_sdk.resources.monitor.collection_parts.snapshot import _SnapshotMixin
from odoo_instance_sdk.resources.monitor.planning import (
    _default_monitor_executor,
    _DockerProvider,
    _GitProvider,
    _ProcessProvider,
)


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
