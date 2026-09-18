from __future__ import annotations

# ruff: noqa: F821
import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

from msgspec.structs import replace

import odoo_instance_sdk.resources.monitor as _monitor_shim
from odoo_instance_sdk.internal.cluster_resources import (
    BatchClusterRequest,
    collect_cluster_resource_batch,
)
from odoo_instance_sdk.internal.git_activity import (
    _validated_base_ref,
)
from odoo_instance_sdk.internal.postgres_compose import (
    SubprocessComposeRunner,
)
from odoo_instance_sdk.internal.storage_footprint import (
    DatabaseStorageInput,
    collect_storage_footprint,
)
from odoo_instance_sdk.models import (
    ClusterEndpoint,
    ClusterResourceSnapshot,
    ClusterSnapshot,
    DatabaseFootprint,
    EnvironmentArtifacts,
    EnvironmentSnapshot,
    GitActivity,
    PgAdminEligibility,
    PgAdminEligibilityState,
    PortObservation,
    PostgresClusterState,
    ProjectSummary,
    PythonEnvFootprint,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)
from odoo_instance_sdk.resources.environment import EnvironmentState
from odoo_instance_sdk.resources.monitor.planning import (
    _CLUSTER_STATUS_TTL,
    _EXPENSIVE_TTL,
    _empty_storage,
    _orphan_git,
    _ProjectPlan,
    _recorded_git_activity,
    _stopped_runtime,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster


class _SnapshotMixin:
    def _collect_snapshot_rows(
        self,
        plans: tuple[_ProjectPlan, ...],
        resources: dict[str, ClusterResourceSnapshot],
        *,
        probe_results: dict[str, ProcessResult] | None = None,
    ) -> tuple[tuple[ProjectSummary, ...], tuple[EnvironmentSnapshot, ...]]:
        projects: list[ProjectSummary] = []
        environments: list[EnvironmentSnapshot] = []
        for plan in plans:
            project_runtime = None
            if plan.project_runtime is not None:
                try:
                    project_runtime = self._collect_runtime(plan.project_runtime)
                except Exception:
                    project_runtime = _stopped_runtime()
            display_hint = plan.project_id.removeprefix("project_")
            projects.append(
                ProjectSummary(
                    id=plan.project_id,
                    name=plan.repo_root.name,
                    display_hint=display_hint,
                    repository_root=display_hint,
                    environment_count=len(plan.environments),
                    cluster=self._cluster_snapshot(plan, resources.get(plan.project_id)),
                    runtime=project_runtime,
                )
            )
            environments.extend(
                self._collect_environment(item.row, plan, item.runtime, probe_results=probe_results)
                for item in sorted(plan.environments, key=lambda item: str(item.row["id"]))
            )
        return tuple(projects), tuple(sorted(environments, key=lambda item: item.id))

    def _prune_caches(
        self,
        environment_ids: set[str],
        worktrees: set[Path],
        clusters: set[str],
        statuses: set[str],
        cpu_points: set[tuple[int, float]],
    ) -> None:
        """Bound monitor memory to the catalog entries seen in the current pass."""
        for key in set(self._storage_cache) - environment_ids:
            del self._storage_cache[key]
        for cache_key in tuple(self._git_cache):
            if cache_key[0] not in worktrees:
                del self._git_cache[cache_key]
        # Stateless default collection probes identity every time; injected test
        # providers have no identity API, so only those use this bounded cache.
        for key in set(self._cluster_status_cache) - statuses:
            del self._cluster_status_cache[key]
        for resource_key in tuple(self._cluster_resource_cache):
            if resource_key not in clusters:
                del self._cluster_resource_cache[resource_key]
        for cpu_key in set(self._cpu_points) - cpu_points:
            del self._cpu_points[cpu_key]

    async def watch(
        self,
        interval: float = 2.0,
        project_id: str | None = None,
        *,
        include_removed: bool = False,
    ) -> AsyncIterator[Snapshot]:
        """Thin async generator over ``snapshot()`` + ``asyncio.sleep``.

        ``interval`` must be ``>= 0.1`` else ``ValueError``. Consumer cancellation
        (``CancelledError``/``break``/``aclose``) stops the generator cleanly; no
        background threads or processes are left behind.
        """
        if interval < 0.1:
            raise ValueError(f"interval must be >= 0.1, got {interval}")
        while True:
            yield self.snapshot(project_id=project_id, include_removed=include_removed)
            await asyncio.sleep(interval)

    # ------------------------------------------------------------------ cluster

    def _cluster_snapshot(
        self, plan: _ProjectPlan, resource: ClusterResourceSnapshot | None
    ) -> ClusterSnapshot | None:
        cluster = plan.cluster
        state = plan.state
        if cluster is None or state is None:
            return None
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

        crs = resource or ClusterResourceSnapshot(
            container=None, metrics=None, unavailability_reason="missing", sampled_at=None
        )
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

    def _cached_status(
        self,
        cluster: PostgresCluster,
        *,
        probe_results: dict[str, ProcessResult] | None = None,
    ) -> PostgresClusterState:
        # A compose project name is user-configurable and can collide.  The
        # manifest root is the ownership boundary for status caching.
        name = str(cluster.compose_file.resolve())
        now = _monitor_shim.time.monotonic()
        cached = self._cluster_status_cache.get(name)
        if cached is not None and now - cached[0] < _CLUSTER_STATUS_TTL:
            return cached[1]
        if probe_results is not None:
            diagnostic = getattr(cluster, "to_diagnostic_dict", None)
            identity = diagnostic().get("project_id") if diagnostic is not None else None
            project_id = f"project_{identity}" if identity else None
            recorded = (
                None
                if project_id is None
                else probe_results.get(f"monitor.{project_id}.docker.resources")
            )
            if recorded is not None and recorded.returncode == 0 and recorded.stdout:
                try:
                    rows = json.loads(str(recorded.stdout))
                except (TypeError, ValueError):
                    rows = []
                if isinstance(rows, list):
                    state = PostgresClusterState.HEALTHY if rows else PostgresClusterState.STOPPED
                    self._cluster_status_cache[name] = (now, state)
                    return state
            if recorded is not None:
                state = PostgresClusterState.STOPPED
                self._cluster_status_cache[name] = (now, state)
                return state
        state = cluster.status()
        self._cluster_status_cache[name] = (now, state)
        return state

    def _collect_cluster_resources(  # noqa: C901
        self,
        plans: list[_ProjectPlan],
        *,
        probe_results: dict[str, ProcessResult] | None = None,
    ) -> tuple[dict[str, ClusterResourceSnapshot], set[str]]:
        """Collect compose resources in one inspect/stats pass for the catalog.

        Each manifest was already parsed once by ``from_project`` above.  We do
        still resolve each compose service separately because compose files can
        use different project names; after that all uncached Docker identities
        share exactly one inspect and one stats invocation.  Cache ownership is
        the immutable Docker ID, so a recreated container cannot inherit old
        metrics merely because its compose project name stayed the same.
        """
        result: dict[str, ClusterResourceSnapshot] = {}
        active_ids: set[str] = set()
        pending: list[BatchClusterRequest] = []
        now = _monitor_shim.time.monotonic()

        if probe_results is not None:
            for plan in plans:
                recorded = probe_results.get(f"monitor.{plan.project_id}.docker.resources")
                if recorded is not None:
                    result[plan.project_id] = ClusterResourceSnapshot(
                        container=None,
                        metrics=None,
                        unavailability_reason=(
                            "stats_failed" if recorded.returncode == 0 else "inspect_failed"
                        ),
                        sampled_at=datetime.now(UTC),
                    )

        for plan in plans:
            cluster = plan.cluster
            state = plan.state
            if plan.project_id in result:
                continue
            if cluster is None or state is None or cluster.mode == "external":
                continue
            if state is PostgresClusterState.STOPPED:
                result[plan.project_id] = ClusterResourceSnapshot(
                    container=None, metrics=None, unavailability_reason="stopped", sampled_at=None
                )
                continue
            if self.docker_provider is not None:
                resource = self.docker_provider.collect(
                    compose_file=cluster.compose_file,
                    compose_project_name=cluster.compose_project_name,
                    service="postgres",
                    state=state,
                )
                result[plan.project_id] = resource
                continue
            # Production clusters each construct a stateless subprocess runner.
            # Use one monitor-owned runner so they form one Docker batch.  A
            # supplied runner is an explicit boundary and remains isolated.
            runner = (
                self._docker_runner
                if isinstance(cluster.compose_runner, SubprocessComposeRunner)
                else cluster.compose_runner
            )
            if getattr(runner, "requires_docker", True) and not _monitor_shim.docker_available():
                result[plan.project_id] = ClusterResourceSnapshot(
                    container=None,
                    metrics=None,
                    unavailability_reason="docker_unavailable",
                    sampled_at=None,
                )
                continue
            pending.append(
                BatchClusterRequest(
                    project_id=plan.project_id,
                    compose_file=cluster.compose_file,
                    compose_project_name=cluster.compose_project_name,
                    service="postgres",
                    runner=runner,
                    state=state,
                    cacheable=runner is self._docker_runner,
                )
            )

        if pending:
            cache = {
                container_id: resource
                for container_id, (cached_at, resource) in self._cluster_resource_cache.items()
                if now - cached_at < _EXPENSIVE_TTL
            }
            batch = collect_cluster_resource_batch(tuple(pending), cached=cache)
            active_ids.update(
                container_id
                for request in pending
                if request.cacheable
                for project_id, container_id in batch.container_ids.items()
                if project_id == request.project_id
            )
            result.update(batch.resources)
            # Failure results are deliberately not cached: a next poll retries.
            cacheable_projects = {request.project_id for request in pending if request.cacheable}
            for project_id, container_id in batch.container_ids.items():
                resource = batch.resources[project_id]
                if project_id in cacheable_projects and resource.unavailability_reason not in {
                    "inspect_failed",
                    "stats_failed",
                }:
                    self._cluster_resource_cache[container_id] = (now, resource)
        return result, active_ids

    # ------------------------------------------------------------- environment

    def _collect_environment(
        self,
        row: sqlite3.Row,
        plan: _ProjectPlan,
        runtime_record: sqlite3.Row | None,
        *,
        probe_results: dict[str, ProcessResult] | None = None,
    ) -> EnvironmentSnapshot:
        env_id = str(row["id"])
        db_mode = str(row["db_mode"])
        database = row["target_db_name"] if db_mode == "copy" else row["source_db_name"]
        database_str = str(database) if database is not None else None

        allocated_port = self._allocated_http_port(row)

        lifecycle_state = EnvironmentState(str(row["state"]))
        # Removed rows retain catalog identity but never perform live probes.
        # Process collection is an environment boundary for active rows: one
        # unavailable PID or psutil failure must not erase healthy siblings.
        if lifecycle_state is EnvironmentState.REMOVED:
            runtime = _stopped_runtime()
        else:
            try:
                runtime = self._collect_runtime(runtime_record)
            except Exception:
                runtime = _stopped_runtime()

        worktree = Path(str(row["worktree_path"]))
        base_ref = _validated_base_ref(row["base_ref"])
        git_probes = None
        if probe_results is not None:
            git_probes = {
                key.rsplit(".git.", 1)[1]: value
                for key, value in probe_results.items()
                if key.startswith(f"monitor.{env_id}.git.")
            }
        git = self._collect_git(worktree, base_ref=base_ref, recorded=git_probes or None)
        short_sha = git.head_sha[:7] if git.head_sha else None

        storage_probes = (
            None
            if probe_results is None
            else {
                key.rsplit(f"monitor.{env_id}.storage.", 1)[1]: value
                for key, value in probe_results.items()
                if key.startswith(f"monitor.{env_id}.storage.")
            }
        )
        storage = self._collect_storage(
            row,
            env_id,
            db_mode,
            recorded=storage_probes,
        )
        worktree_probe = (
            None
            if probe_results is None
            else probe_results.get(f"monitor.{plan.project_id}.git.worktrees")
        )
        artifacts = self._collect_artifacts(row, recorded=worktree_probe)
        observed_port = self._observe_port(row, lifecycle_state, runtime, allocated_port)
        pgadmin = self._pgadmin_eligibility(lifecycle_state, database_str, plan.cluster, plan.state)

        return EnvironmentSnapshot(
            id=env_id,
            project_id=plan.project_id,
            name=str(row["name"]),
            branch=str(row["branch"]),
            short_sha=short_sha,
            db_mode=db_mode,
            database=database_str,
            lifecycle_state=lifecycle_state,
            allocated_http_port=allocated_port,
            observed_port=observed_port,
            artifacts=artifacts,
            runtime=runtime,
            git=git,
            storage=storage,
            pgadmin=pgadmin,
        )

    @staticmethod
    def _pgadmin_eligibility(
        lifecycle_state: EnvironmentState,
        database: str | None,
        cluster: PostgresCluster | None,
        cluster_state: PostgresClusterState | None,
    ) -> PgAdminEligibility:
        if lifecycle_state is not EnvironmentState.READY:
            state = PgAdminEligibilityState.ENVIRONMENT_NOT_READY
        elif database is None:
            state = PgAdminEligibilityState.DATABASE_UNRESOLVED
        elif cluster is None or cluster.mode != "compose":
            state = PgAdminEligibilityState.CLUSTER_NOT_OWNED
        elif cluster_state is not PostgresClusterState.HEALTHY:
            state = PgAdminEligibilityState.CLUSTER_UNHEALTHY
        else:
            state = PgAdminEligibilityState.ELIGIBLE
        return PgAdminEligibility(state=state)

    def _collect_artifacts(  # noqa: C901
        self, row: sqlite3.Row, *, recorded: ProcessResult | None = None
    ) -> EnvironmentArtifacts:
        """Reconcile independent catalog/filesystem artifacts defensively."""
        worktree = Path(str(row["worktree_path"]))
        repository_root = Path(str(row["repository_root"]))
        generated_config = Path(str(row["generated_config_path"]))
        dependency_lock = Path(str(row["dependency_lock_path"]))
        python_path = Path(str(row["python_environment_path"]))
        python_owned = bool(int(row["python_environment_owned"]))

        def is_file(path: Path) -> bool:
            try:
                return path.is_file()
            except OSError:
                return False

        def is_dir(path: Path) -> bool:
            try:
                return path.is_dir()
            except OSError:
                return False

        if recorded is not None:
            registered = False
            if recorded.returncode == 0 and isinstance(recorded.stdout, str):
                for entry in recorded.stdout.replace("\x00", "\n").splitlines():
                    if entry.startswith("worktree "):
                        try:
                            if Path(entry[len("worktree ") :]).resolve() == worktree.resolve():
                                registered = True
                                break
                        except OSError:
                            pass
        else:
            try:
                registered = any(
                    Path(entry.worktree).resolve() == worktree.resolve()
                    for entry in _monitor_shim.worktree_list_porcelain(repository_root)
                )
            except Exception:
                registered = False

        if python_owned:
            python_exists = is_file(python_path / "bin" / "python")
            try:
                python_contained = python_path.resolve().is_relative_to(worktree.parent.resolve())
            except OSError:
                python_contained = False
        else:
            python_exists = is_file(python_path)
            python_contained = True

        backup_id = row["backup_id"]
        if backup_id is None:
            backup_exists: bool | None = None
        else:
            backup_state = row["backup_state"]
            backup_path = row["backup_path"]
            backup_exists = (
                backup_state == "available"
                and backup_path is not None
                and is_file(Path(str(backup_path)))
            )
        return EnvironmentArtifacts(
            worktree_exists=is_dir(worktree),
            worktree_registered=registered,
            config_exists=is_file(generated_config),
            python_exists=python_exists,
            python_contained=python_contained,
            dependency_lock_exists=is_file(dependency_lock),
            backup_exists=backup_exists,
        )

    def _observe_port(
        self,
        row: sqlite3.Row,
        lifecycle_state: EnvironmentState,
        runtime: RuntimeMetrics,
        allocated_port: int | None,
    ) -> PortObservation | None:
        """Probe only a live ready environment's allocated HTTP endpoint."""
        if (
            lifecycle_state is not EnvironmentState.READY
            or runtime.state not in (RuntimeState.READY, RuntimeState.NOT_READY)
            or allocated_port is None
        ):
            return None
        try:
            from odoo_instance_sdk.models import StartConfig

            cfg = StartConfig.from_odoo_config(str(row["generated_config_path"]))
            return PortObservation(
                _monitor_shim.probe_address(cfg.http_interface, allocated_port).value
            )
        except Exception:
            return PortObservation.UNKNOWN

    def _allocated_http_port(self, row: sqlite3.Row) -> int | None:
        cfg_path = str(row["generated_config_path"])
        try:
            from odoo_instance_sdk.models import StartConfig

            cfg = StartConfig.from_odoo_config(cfg_path)
        except Exception:
            return None
        return cfg.http_port

    def _collect_runtime(self, rt: sqlite3.Row | None) -> RuntimeMetrics:
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
            memory_bytes=result.memory_bytes,
            started_at=started_at,
            http_url=http_url,
            http_port=int(rt["http_port"]),
            database_name=str(rt["database_name"]),
            commit_sha=str(rt["commit_sha"]),
            branch=str(rt["checkout_branch"]),
        )

    def _probe_readiness(self, http_url: str) -> RuntimeState:
        try:
            resp = _monitor_shim.httpx.get(
                f"{http_url}/web/health?db_server_status=true", timeout=2.0
            )
        except Exception:
            return RuntimeState.NOT_READY
        if resp.status_code == 200:
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                return RuntimeState.NOT_READY
            if isinstance(data, dict) and data.get("status") == "pass":
                return RuntimeState.READY
        return RuntimeState.NOT_READY

    def _collect_git(
        self,
        worktree: Path,
        *,
        base_ref: str | None = "main",
        recorded: Mapping[str, ProcessResult] | None = None,
    ) -> GitActivity:
        if recorded is not None:
            return _recorded_git_activity(recorded, base_ref=base_ref)
        validated_base_ref = _validated_base_ref(base_ref)
        if validated_base_ref is None:
            return _orphan_git("unknown")
        try:
            if self.git_provider is not None:
                key = worktree.resolve()
                cache_key: tuple[Path, str, str | None, str] = (
                    key,
                    "provider",
                    None,
                    validated_base_ref,
                )
                cached = self._git_cache.get(cache_key)
                if (
                    cached is not None
                    and _monitor_shim.time.monotonic() - cached[0] < _EXPENSIVE_TTL
                ):
                    return cached[1]
                result = self.git_provider.collect(worktree)
                if result.default_branch != validated_base_ref:
                    result = replace(result, default_branch=validated_base_ref)
                self._git_cache[cache_key] = (_monitor_shim.time.monotonic(), result)
            else:
                resolved = worktree.resolve()
                identity = (
                    _monitor_shim._resolve_identity(resolved)
                    if validated_base_ref == "main"
                    else _monitor_shim._resolve_identity(resolved, validated_base_ref)
                )
                cache_key = (resolved, identity[0], identity[3], validated_base_ref)
                # Identity probing is cheap.  Keep at most one expensive value
                # per worktree: a new HEAD or default tip must invalidate the old
                # result instead of growing the monitor for every commit.
                for stale_key in tuple(self._git_cache):
                    if stale_key[0] == resolved and stale_key != cache_key:
                        del self._git_cache[stale_key]
                cached = self._git_cache.get(cache_key)
                if (
                    cached is not None
                    and _monitor_shim.time.monotonic() - cached[0] < _EXPENSIVE_TTL
                ):
                    return cached[1]
                result = (
                    _monitor_shim.collect_git_activity_from_identity(resolved, identity)
                    if validated_base_ref == "main"
                    else _monitor_shim.collect_git_activity_from_identity(
                        resolved, identity, base_ref=validated_base_ref
                    )
                )
                self._git_cache[cache_key] = (_monitor_shim.time.monotonic(), result)
        except Exception:
            result = _orphan_git(validated_base_ref or "unknown")
        return result

    def _collect_storage(  # noqa: C901
        self,
        row: sqlite3.Row,
        env_id: str,
        db_mode: str,
        *,
        recorded: Mapping[str, ProcessResult] | None = None,
    ) -> StorageFootprint:
        now = _monitor_shim.time.monotonic()
        if recorded is not None:
            cached = self._storage_cache.get(env_id)
            if cached is not None and now - cached[0] < _EXPENSIVE_TTL:
                return cached[1]

            def measured(name: str) -> int | None:
                result = recorded.get(name)
                if result is None or result.returncode != 0 or not result.stdout:
                    return None
                try:
                    return int(str(result.stdout).strip().split()[0])
                except (TypeError, ValueError, IndexError):
                    return None

            worktree_bytes = measured("worktree")
            python_owned = bool(int(row["python_environment_owned"]))
            python_bytes = measured("python") if python_owned else None
            postgres_bytes = measured("postgres") if db_mode == "copy" else None
            filestore_bytes = measured("filestore") if db_mode == "copy" else None
            generated_config = Path(str(row["generated_config_path"]))
            dependency_lock = Path(str(row["dependency_lock_path"]))

            def file_size(path: Path) -> int | None:
                try:
                    return path.stat().st_size if path.is_file() else 0
                except OSError:
                    return None

            def directory_size(path: Path, name: str) -> int | None:
                if path.is_dir():
                    measured_size = measured(name)
                    if measured_size is not None:
                        return measured_size
                return None if path.exists() else 0

            other_sizes = [
                file_size(generated_config),
                file_size(dependency_lock),
                file_size(generated_config.parent / "odoo.log"),
                directory_size(generated_config.parent / "cache", "cache"),
                directory_size(generated_config.parent / "artifacts", "artifacts"),
            ]
            other_bytes = (
                None
                if any(value is None for value in other_sizes)
                else sum(value for value in other_sizes if value is not None)
            )
            if worktree_bytes is not None:
                database = DatabaseFootprint(
                    owned=db_mode == "copy",
                    postgres_bytes=postgres_bytes,
                    filestore_bytes=filestore_bytes,
                    total_bytes=(postgres_bytes or 0) + (filestore_bytes or 0)
                    if postgres_bytes is not None or filestore_bytes is not None
                    else None,
                )
                complete = worktree_bytes is not None and other_bytes is not None
                if python_owned:
                    complete = complete and python_bytes is not None
                if db_mode == "copy":
                    complete = (
                        complete and postgres_bytes is not None and filestore_bytes is not None
                    )
                footprint = StorageFootprint(
                    total_bytes=worktree_bytes
                    + (python_bytes or 0)
                    + (postgres_bytes or 0)
                    + (filestore_bytes or 0)
                    + (other_bytes or 0),
                    complete=complete,
                    worktree_bytes=worktree_bytes,
                    python_environment=PythonEnvFootprint(owned=python_owned, bytes=python_bytes),
                    database=database,
                    other_files_bytes=other_bytes,
                )
                self._storage_cache[env_id] = (now, footprint)
                return footprint
            empty = _empty_storage()
            self._storage_cache[env_id] = (now, empty)
            return empty
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
            footprint = collect_storage_footprint(
                worktree_path=worktree_path,
                python_environment_path=python_path,
                python_environment_owned=python_owned,
                generated_config_path=generated_config,
                dependency_lock_path=dependency_lock,
                environment_root=generated_config.parent,
                database=DatabaseStorageInput(
                    mode=db_mode,
                    target_name=target_db_str,
                    host=db_host,
                    port=db_port,
                    user=db_user,
                    password=db_password,
                    data_dir=data_dir,
                ),
            )
        except Exception:
            footprint = _empty_storage()
        self._storage_cache[env_id] = (now, footprint)
        return footprint
