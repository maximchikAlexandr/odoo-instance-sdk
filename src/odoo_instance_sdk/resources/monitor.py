from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    MonitorError,
    PostgresClusterError,
    ProjectManifestNotFoundError,
)
from odoo_instance_sdk.internal.cluster_resources import _compute_cluster_resource
from odoo_instance_sdk.internal.git_activity import _compute_git_activity
from odoo_instance_sdk.internal.paths import get_catalog_path
from odoo_instance_sdk.internal.process_metrics import CpuPoint, ProcessTreeResult
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.storage_footprint import _compute_storage_footprint
from odoo_instance_sdk.models import (
    ClusterEndpoint,
    ClusterResourceSnapshot,
    ClusterSnapshot,
    DatabaseFootprint,
    EnvironmentSnapshot,
    GitActivity,
    GitActivityState,
    PostgresClusterState,
    ProjectSummary,
    PythonEnvFootprint,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)
from odoo_instance_sdk.resources.environment import EnvironmentState
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

_EXPENSIVE_TTL = 15.0
_CLUSTER_STATUS_TTL = 5.0
_SCHEME_VERSION = 1


class _ProcessProvider(Protocol):
    def collect(
        self, root_pid: int, create_time: float, *, prev_cpu_point: CpuPoint | None
    ) -> tuple[ProcessTreeResult, CpuPoint] | None: ...


class _GitProvider(Protocol):
    def collect(self, worktree: Path) -> GitActivity: ...


class _DockerProvider(Protocol):
    def collect(
        self,
        *,
        compose_file: Path,
        compose_project_name: str,
        service: str,
        state: PostgresClusterState,
    ) -> ClusterResourceSnapshot: ...


def _orphan_git() -> GitActivity:
    return GitActivity(
        default_branch="main",
        head_sha=None,
        short_sha=None,
        branch="unknown",
        ahead=None,
        behind=None,
        diff=None,
        state=GitActivityState.ORPHAN,
    )


def _empty_storage() -> StorageFootprint:
    return StorageFootprint(
        total_bytes=0,
        complete=False,
        worktree_bytes=None,
        python_environment=PythonEnvFootprint(owned=False, bytes=None),
        database=DatabaseFootprint(
            owned=False, postgres_bytes=None, filestore_bytes=None, total_bytes=None
        ),
        other_files_bytes=None,
    )


def _stopped_runtime() -> RuntimeMetrics:
    return RuntimeMetrics(
        state=RuntimeState.STOPPED,
        root_pid=None,
        child_pids=(),
        process_count=0,
        cpu_percent=None,
        rss_bytes=None,
        started_at=None,
        http_url=None,
        http_port=None,
        database_name=None,
        commit_sha=None,
        branch=None,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentMonitor:
    """Read-only collector that assembles a typed ``Snapshot`` from the catalog.

    Construction is cheap (no psutil import, no catalog open). ``snapshot()``
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
    # ponytail: frozen dataclass — dict contents are mutable, the field binding is not.
    # CPU points never expire (keyed by pid+create_time; stale when process dies).
    _cpu_points: dict[tuple[int, float], CpuPoint] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _cluster_status_cache: dict[str, tuple[float, PostgresClusterState]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    # ponytail: git cache keyed by worktree.resolve() alone (not (worktree,HEAD,tip))
    # — HEAD changes within TTL aren't reflected until expiry. Acceptable for MVP
    # polling at 2s with 15s TTL; the exact (worktree,HEAD,tip) key would require a
    # cheap rev-parse pre-pass before the cache check. Upgrade: add that pre-pass.
    _git_cache: dict[Path, tuple[float, GitActivity]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _storage_cache: dict[str, tuple[float, StorageFootprint]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _cluster_resource_cache: dict[str, tuple[float, ClusterResourceSnapshot]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )

    def snapshot(self, project_id: str | None = None) -> Snapshot:
        """Perform one coherent collection pass and return an immutable ``Snapshot``."""
        generated_at = datetime.now(UTC)
        db_path = self.catalog_path if self.catalog_path is not None else get_catalog_path()
        try:
            catalog = BackupCatalog(db_path=db_path)
        except BackupCatalogError as exc:
            raise MonitorError(str(exc)) from exc

        try:
            try:
                rows = catalog.list_environments(include_removed=False)
            except BackupCatalogError as exc:
                raise MonitorError(str(exc)) from exc

            # Group rows by git_common_dir (shared within a project).
            groups: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                gcd = str(row["git_common_dir"])
                groups.setdefault(gcd, []).append(row)

            projects: list[ProjectSummary] = []
            environments: list[EnvironmentSnapshot] = []

            for gcd in sorted(groups):
                group_rows = groups[gcd]
                first = group_rows[0]
                repo_root = Path(str(first["repository_root"]))
                git_common = Path(gcd)
                key = repo_key(repo_root, git_common)
                pid = f"project_{key}"
                if project_id is not None and pid != project_id:
                    continue

                cluster_snapshot = self._collect_cluster(repo_root)
                projects.append(
                    ProjectSummary(
                        id=pid,
                        name=repo_root.name,
                        display_hint=key,
                        environment_count=len(group_rows),
                        cluster=cluster_snapshot,
                    )
                )
                for row in sorted(group_rows, key=lambda r: str(r["id"])):
                    environments.append(self._collect_environment(row, pid, catalog))
        finally:
            catalog.close()

        environments.sort(key=lambda e: e.id)

        if project_id is not None and not projects:
            return Snapshot(
                schema_version=_SCHEME_VERSION,
                generated_at=generated_at,
                projects=(),
                environments=(),
            )

        return Snapshot(
            schema_version=_SCHEME_VERSION,
            generated_at=generated_at,
            projects=tuple(projects),
            environments=tuple(environments),
        )

    async def watch(
        self, interval: float = 2.0, project_id: str | None = None
    ) -> AsyncIterator[Snapshot]:
        """Thin async generator over ``snapshot()`` + ``asyncio.sleep``.

        ``interval`` must be ``>= 0.1`` else ``ValueError``. Consumer cancellation
        (``CancelledError``/``break``/``aclose``) stops the generator cleanly; no
        background threads or processes are left behind.
        """
        if interval < 0.1:
            raise ValueError(f"interval must be >= 0.1, got {interval}")
        while True:
            yield self.snapshot(project_id=project_id)
            await asyncio.sleep(interval)

    # ------------------------------------------------------------------ cluster

    def _collect_cluster(self, repo_root: Path) -> ClusterSnapshot | None:
        try:
            cluster = PostgresCluster.from_project(repo_root)
        except (ProjectManifestNotFoundError, PostgresClusterError, OSError):
            # ponytail: any manifest load failure (missing manifest, corrupt TOML,
            # OSError, invalid cluster config) → cluster=None per spec; environments
            # continue. A bare Exception would swallow programming errors, so we
            # list the concrete failure modes.
            return None

        state = self._cached_status(cluster)
        endpoint = ClusterEndpoint(host=cluster.endpoint_host, port=cluster.endpoint_port)

        if cluster.mode == "external":
            return ClusterSnapshot(
                mode="external",
                owned=False,
                state=state,
                endpoint=endpoint,
                container=None,
                metrics=None,
                unavailability_reason="external_not_owned",
                sampled_at=None,
            )

        crs = self._collect_cluster_resource(cluster, state)
        return ClusterSnapshot(
            mode="compose",
            owned=True,
            state=state,
            endpoint=endpoint,
            container=crs.container,
            metrics=crs.metrics,
            unavailability_reason=crs.unavailability_reason,
            sampled_at=crs.sampled_at,
        )

    def _cached_status(self, cluster: PostgresCluster) -> PostgresClusterState:
        name = cluster.compose_project_name
        now = time.monotonic()
        cached = self._cluster_status_cache.get(name)
        if cached is not None and now - cached[0] < _CLUSTER_STATUS_TTL:
            return cached[1]
        state = cluster.status()
        self._cluster_status_cache[name] = (now, state)
        return state

    def _collect_cluster_resource(
        self, cluster: PostgresCluster, state: PostgresClusterState
    ) -> ClusterResourceSnapshot:
        name = cluster.compose_project_name
        now = time.monotonic()
        cached = self._cluster_resource_cache.get(name)
        if cached is not None and now - cached[0] < _EXPENSIVE_TTL:
            return cached[1]
        if self.docker_provider is not None:
            crs = self.docker_provider.collect(
                compose_file=cluster.compose_file,
                compose_project_name=name,
                service="postgres",
                state=state,
            )
        else:
            # ponytail: per-cluster resource_snapshot with instance cache; the helper's
            # container_id module cache is bypassed by calling _compute_cluster_resource
            # directly so a fresh monitor instance recomputes. True cross-project
            # batching would need a pre-pass resolving all container_ids first.
            crs = _compute_cluster_resource(
                compose_file=cluster.compose_file,
                compose_project_name=name,
                service="postgres",
                runner=cluster._compose_runner,
                state=state,
            )
        self._cluster_resource_cache[name] = (now, crs)
        return crs

    # ------------------------------------------------------------- environment

    def _collect_environment(
        self, row: sqlite3.Row, project_id: str, catalog: BackupCatalog
    ) -> EnvironmentSnapshot:
        env_id = str(row["id"])
        db_mode = str(row["db_mode"])
        database = row["target_db_name"] if db_mode == "copy" else row["source_db_name"]
        database_str = str(database) if database is not None else None

        allocated_port = self._allocated_http_port(row)

        runtime = self._collect_runtime(row, catalog)

        worktree = Path(str(row["worktree_path"]))
        git = self._collect_git(worktree)
        short_sha = git.head_sha[:7] if git.head_sha else None

        storage = self._collect_storage(row, env_id, db_mode)

        return EnvironmentSnapshot(
            id=env_id,
            project_id=project_id,
            name=str(row["name"]),
            branch=str(row["branch"]),
            short_sha=short_sha,
            db_mode=db_mode,  # type: ignore[arg-type]
            database=database_str,
            lifecycle_state=EnvironmentState(str(row["state"])),
            allocated_http_port=allocated_port,
            runtime=runtime,
            git=git,
            storage=storage,
        )

    def _allocated_http_port(self, row: sqlite3.Row) -> int | None:
        cfg_path = str(row["generated_config_path"])
        try:
            from odoo_instance_sdk.models import StartConfig

            cfg = StartConfig.from_odoo_config(cfg_path)
        except Exception:
            return None
        return cfg.http_port

    def _collect_runtime(self, row: sqlite3.Row, catalog: BackupCatalog) -> RuntimeMetrics:
        env_id = str(row["id"])
        try:
            rt = catalog.get_environment_runtime(env_id)
        except BackupCatalogError:
            return _stopped_runtime()

        if rt is None:
            return _stopped_runtime()

        root_pid = int(rt["root_pid"])
        create_time = float(rt["create_time"])
        prev = self._cpu_points.get((root_pid, create_time))

        if self.process_provider is not None:
            result_pair = self.process_provider.collect(root_pid, create_time, prev_cpu_point=prev)
        else:
            from odoo_instance_sdk.internal.process_metrics import collect_process_tree

            result_pair = collect_process_tree(root_pid, create_time, prev_cpu_point=prev)

        if result_pair is None:
            return _stopped_runtime()

        result, new_point = result_pair
        self._cpu_points[(root_pid, create_time)] = new_point

        http_url = str(rt["http_url"])
        state = self._probe_readiness(http_url)

        started_at: datetime | None
        try:
            started_at = datetime.fromisoformat(str(rt["started_at"]))
        except (ValueError, TypeError):
            started_at = None

        return RuntimeMetrics(
            state=state,
            root_pid=root_pid,
            child_pids=result.child_pids,
            process_count=result.process_count,
            cpu_percent=result.cpu_percent,
            rss_bytes=result.rss_bytes,
            started_at=started_at,
            http_url=http_url,
            http_port=int(rt["http_port"]),
            database_name=str(rt["database_name"]),
            commit_sha=str(rt["commit_sha"]),
            branch=str(rt["checkout_branch"]),
        )

    def _probe_readiness(self, http_url: str) -> RuntimeState:
        try:
            resp = httpx.get(f"{http_url}/web/health?db_server_status=true", timeout=2.0)
        except Exception:
            return RuntimeState.NOT_READY
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("status") == "pass":
                return RuntimeState.READY
        return RuntimeState.NOT_READY

    def _collect_git(self, worktree: Path) -> GitActivity:
        key = worktree.resolve()
        now = time.monotonic()
        cached = self._git_cache.get(key)
        if cached is not None and now - cached[0] < _EXPENSIVE_TTL:
            return cached[1]
        try:
            if self.git_provider is not None:
                result = self.git_provider.collect(worktree)
            else:
                result = _compute_git_activity(worktree)
        except Exception:
            result = _orphan_git()
        self._git_cache[key] = (now, result)
        return result

    def _collect_storage(self, row: sqlite3.Row, env_id: str, db_mode: str) -> StorageFootprint:
        now = time.monotonic()
        cached = self._storage_cache.get(env_id)
        if cached is not None and now - cached[0] < _EXPENSIVE_TTL:
            return cached[1]

        worktree_path = Path(str(row["worktree_path"]))
        python_path = Path(str(row["python_environment_path"]))
        python_owned = bool(int(row["python_environment_owned"]))
        generated_config = Path(str(row["generated_config_path"]))
        dependency_lock = Path(str(row["dependency_lock_path"]))
        target_db = row["target_db_name"]
        target_db_str = str(target_db) if target_db is not None else None

        db_host: str | None
        db_port: int | None
        db_user: str | None
        db_password: str | None
        data_dir: Path | None
        try:
            from odoo_instance_sdk.models import StartConfig

            cfg = StartConfig.from_odoo_config(str(row["generated_config_path"]))
            db_host = cfg.db_host
            db_port = cfg.db_port
            db_user = cfg.db_user
            db_password = cfg.db_password
            data_dir = Path(cfg.data_dir) if cfg.data_dir is not None else None
        except Exception:
            db_host = None
            db_port = None
            db_user = None
            db_password = None
            data_dir = None

        try:
            footprint = _compute_storage_footprint(
                environment_id=env_id,
                worktree_path=worktree_path,
                python_environment_path=python_path,
                python_environment_owned=python_owned,
                db_mode=db_mode,
                generated_config_path=generated_config,
                dependency_lock_path=dependency_lock,
                target_db_name=target_db_str,
                db_host=db_host,
                db_port=db_port,
                db_user=db_user,
                db_password=db_password,
                data_dir=data_dir,
            )
        except Exception:
            footprint = _empty_storage()
        self._storage_cache[env_id] = (now, footprint)
        return footprint


__all__ = ["EnvironmentMonitor"]
