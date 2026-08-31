from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sqlite3
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import httpx

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    MonitorError,
    PostgresClusterError,
    ProjectManifestNotFoundError,
)
from odoo_instance_sdk.internal.address import probe_address
from odoo_instance_sdk.internal.cluster_resources import (
    BatchClusterRequest,
    collect_cluster_resource_batch,
)
from odoo_instance_sdk.internal.db_name import validate_filestore_containment
from odoo_instance_sdk.internal.git_activity import (
    _resolve_identity,
    collect_git_activity_from_identity,
)
from odoo_instance_sdk.internal.git_worktree import worktree_list_porcelain
from odoo_instance_sdk.internal.paths import get_catalog_path
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
_SCHEMA_VERSION = 3
_PROBE_TIMEOUT_SECONDS = 5.0


def _default_monitor_executor() -> ProcessExecutor:
    from odoo_instance_sdk.internal.proc import SubprocessExecutor

    return SubprocessExecutor()


if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        ProcessResult,
        RunContext,
    )


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


@dataclass(frozen=True, slots=True)
class _ProjectPlan:
    """One catalog project and its manifest-derived PostgreSQL plan."""

    project_id: str
    repo_root: Path
    cluster: PostgresCluster | None
    state: PostgresClusterState | None
    environments: tuple[_EnvironmentPlan, ...]


@dataclass(frozen=True, slots=True)
class _EnvironmentPlan:
    """A catalog row and the single runtime read for this snapshot pass."""

    row: sqlite3.Row
    runtime: sqlite3.Row | None


@dataclass(frozen=True, slots=True)
class _SnapshotPlan:
    """The catalog-derived, immutable input to one collection pass.

    Planning owns catalog reads and cache liveness; collection never reaches
    back into SQLite.  This is deliberately a real boundary: it prevents a
    second runtime read halfway through rendering a snapshot.
    """

    projects: tuple[_ProjectPlan, ...]
    environment_ids: frozenset[str]
    worktrees: frozenset[Path]
    statuses: frozenset[str]
    cpu_points: frozenset[tuple[int, float]]


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


def _recorded_git_activity(results: Mapping[str, ProcessResult]) -> GitActivity:  # noqa: C901
    """Rebuild the complete Git collector result from captured probe output."""

    def output(name: str) -> tuple[int, str]:
        result = results.get(name)
        if result is None:
            return 127, ""
        value = result.stdout if isinstance(result.stdout, str) else ""
        return result.returncode, value.strip()

    head_rc, head = output("head")
    branch_rc, branch = output("branch")
    if head_rc != 0 or not head or branch_rc != 0 or not branch:
        return _orphan_git()
    upstream_rc, default_tip = output("upstream")
    if upstream_rc == 0 and default_tip:
        prefix = "upstream"
    else:
        local_rc, default_tip = output("local_main")
        if local_rc != 0 or not default_tip:
            return _orphan_git()
        prefix = "local"
    merge_rc, merge_base = output(f"{prefix}_merge_base")
    if merge_rc == 1:
        return GitActivity(
            default_branch="main",
            head_sha=head,
            short_sha=head[:7],
            branch=branch,
            ahead=None,
            behind=None,
            diff=None,
            state=GitActivityState.ORPHAN,
        )
    if merge_rc != 0 or not merge_base:
        return _orphan_git()
    ahead_rc, ahead_text = output(f"{prefix}_ahead")
    behind_rc, behind_text = output(f"{prefix}_behind")
    diff_rc, diff_text = output(f"{prefix}_diff")
    if (
        ahead_rc != 0
        or behind_rc != 0
        or diff_rc != 0
        or not ahead_text.isdigit()
        or not behind_text.isdigit()
    ):
        return _orphan_git()
    added = deleted = 0
    for line in diff_text.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or parts[0] == "-" or parts[1] == "-":
            continue
        try:
            added += int(parts[0])
            deleted += int(parts[1])
        except ValueError:
            continue
    ahead, behind = int(ahead_text), int(behind_text)
    if ahead == 0 and behind == 0:
        state = GitActivityState.CLEAN
    elif behind == 0:
        state = GitActivityState.AHEAD
    elif ahead == 0:
        state = GitActivityState.BEHIND
    else:
        state = GitActivityState.DIVERGED
    return GitActivity(
        default_branch="main",
        head_sha=head,
        short_sha=head[:7],
        branch=branch,
        ahead=ahead,
        behind=behind,
        diff=GitDiff(added=added, deleted=deleted),
        state=state,
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
    # ponytail: frozen dataclass — dict contents are mutable, the field binding is not.
    # CPU points never expire (keyed by pid+create_time; stale when process dies).
    _cpu_points: dict[tuple[int, float], CpuPoint] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _cluster_status_cache: dict[str, tuple[float, PostgresClusterState]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _git_cache: dict[tuple[Path, str, str | None], tuple[float, GitActivity]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _storage_cache: dict[str, tuple[float, StorageFootprint]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    # A Docker ID is only meaningful within the process boundary that resolved
    # it.  In production that boundary is the monitor-owned runner; injected
    # runners deliberately stay isolated even if a fake returns the same ID.
    # Only the persistent monitor-owned production runner is cacheable.  An
    # injected runner is an explicit test/integration boundary and may be
    # short-lived, so using ``id(runner)`` as a durable cache identity is not
    # safe (CPython can reuse it after the object is collected).
    _cluster_resource_cache: dict[str, tuple[float, ClusterResourceSnapshot]] = field(
        default_factory=dict, repr=False, hash=False, compare=False
    )
    _docker_runner: ComposeRunner = field(
        default_factory=SubprocessComposeRunner, repr=False, hash=False, compare=False
    )
    _executor: ProcessExecutor = field(
        default_factory=_default_monitor_executor, repr=False, hash=False, compare=False
    )

    def snapshot(self, project_id: str | None = None, *, include_removed: bool = False) -> Snapshot:
        """Build one immutable snapshot command and execute it."""
        return self.snapshot_command(project_id, include_removed=include_removed).run()

    def snapshot_command(
        self, project_id: str | None = None, *, include_removed: bool = False
    ) -> Command[Snapshot]:
        """Capture one finite monitor collection operation.

        The monitor's catalog/cache reads and bounded probes remain in the
        operation callback.  The action is deliberately consumed after the
        collection so the command ledger still records a complete finite run;
        ``watch`` constructs a fresh command for every tick.
        """
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            ProcessExecutionError,
            ProcessResult,
            prepared_command,
        )

        action = PreparedAction(
            step_id="monitor.snapshot",
            action="collect_snapshot",
            description="Collect one finite environment monitor snapshot",
            details={
                "project_id": project_id,
                "include_removed": include_removed,
            },
            read_only=True,
        )
        probe_steps, catalog_rows = self._capture_probe_steps(
            project_id=project_id, include_removed=include_removed
        )
        captured_steps: tuple[PreparedStep | PreparedAction, ...] = (*probe_steps, action)

        def execute(context: RunContext[Snapshot]) -> Snapshot:
            probe_results = cast("dict[str, ProcessResult]", context.results)
            try:
                for probe in probe_steps:
                    try:
                        probe_results[probe.step_id] = cast(
                            "ProcessResult", context.process(probe.step_id)
                        )
                    except ProcessExecutionError as error:
                        # Keep a typed failed observation in the same ledger.
                        # Collectors must not retry a failed captured probe via
                        # their legacy provider path.
                        probe_results[probe.step_id] = ProcessResult(
                            argv=error.argv,
                            returncode=127,
                            stdout="",
                            stderr=str(error),
                            duration=error.duration,
                            cwd=probe.cwd,
                            environment=probe.environment,
                        )
                if probe_steps:
                    return self._snapshot_impl(
                        project_id=project_id,
                        include_removed=include_removed,
                        probe_results=probe_results,
                        catalog_rows=catalog_rows,
                    )
                return self._snapshot_impl(
                    project_id=project_id,
                    include_removed=include_removed,
                )
            finally:
                # Optional branches (for example an upstream Git ref that is
                # not present) are accounted for without launching a second
                # process.  Required probes are consumed by the collectors
                # through the active RunContext.
                context.skip_remaining()
                context.action(action.step_id)

        plan = ExecutionPlan(
            steps=tuple(step.public_projection() for step in captured_steps)
        ).with_fingerprint()
        return Command.from_prepared(
            plan,
            prepared_command(
                execute,
                captured_steps,
                executor=self._executor,
            ),
        )

    def _capture_probe_steps(  # noqa: C901
        self, *, project_id: str | None, include_removed: bool
    ) -> tuple[tuple[PreparedStep, ...], list[tuple[sqlite3.Row, sqlite3.Row | None]]]:
        """Capture the finite process probe manifest for one snapshot command.

        Catalog discovery is deliberately best-effort here: it is construction
        of an inspectable manifest, not snapshot collection.  The callback
        remains the authoritative domain collector, while this manifest keeps
        Git, storage, Docker and PostgreSQL child-process inputs in the same
        command ledger as the resulting snapshot.
        """
        from odoo_instance_sdk.internal.proc import PreparedStep

        db_path = self.catalog_path if self.catalog_path is not None else get_catalog_path()
        try:
            catalog = BackupCatalog(db_path=db_path)
            try:
                rows = catalog.list_environments_with_runtimes(include_removed=include_removed)
            finally:
                catalog.close()
        except (BackupCatalogError, sqlite3.Error, OSError):
            return (), []

        steps: list[PreparedStep] = []
        projects: set[tuple[Path, str]] = set()
        repositories: set[tuple[Path, str]] = set()
        for row, _runtime in rows:
            worktree = Path(str(row["worktree_path"])).resolve()
            env_id = str(row["id"])
            repository = Path(str(row["repository_root"])).resolve()
            resolved_project = f"project_{repo_key(repository, Path(str(row['git_common_dir'])))}"
            if project_id is not None and resolved_project != project_id:
                continue
            if self.git_provider is None:
                git_commands = (
                    ("branch", ("rev-parse", "--abbrev-ref", "HEAD")),
                    ("upstream", ("rev-parse", "--verify", "main@{upstream}")),
                    ("local_main", ("rev-parse", "--verify", "refs/heads/main")),
                    (
                        "upstream_merge_base",
                        ("merge-base", "main@{upstream}", "HEAD"),
                    ),
                    ("upstream_ahead", ("rev-list", "--count", "main@{upstream}..HEAD")),
                    ("upstream_behind", ("rev-list", "--count", "HEAD..main@{upstream}")),
                    ("upstream_diff", ("diff", "--numstat", "main@{upstream}...HEAD")),
                    (
                        "local_merge_base",
                        ("merge-base", "refs/heads/main", "HEAD"),
                    ),
                    ("local_ahead", ("rev-list", "--count", "refs/heads/main..HEAD")),
                    ("local_behind", ("rev-list", "--count", "HEAD..refs/heads/main")),
                    ("local_diff", ("diff", "--numstat", "refs/heads/main...HEAD")),
                )
                steps.append(
                    PreparedStep(
                        step_id=f"monitor.{env_id}.git.head",
                        argv=("git", "-C", str(worktree), "rev-parse", "--verify", "HEAD"),
                        cwd=str(worktree),
                        timeout=_PROBE_TIMEOUT_SECONDS,
                        read_only=True,
                        text=True,
                    )
                )
            du = shutil.which("du") or "du"
            steps.append(
                PreparedStep(
                    step_id=f"monitor.{env_id}.storage.worktree",
                    argv=(du, "-sb", str(worktree)),
                    cwd=str(worktree.parent),
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    read_only=True,
                    text=True,
                )
            )
            database = (
                row["target_db_name"] if str(row["db_mode"]) == "copy" else row["source_db_name"]
            )
            if database:
                steps.append(
                    PreparedStep(
                        step_id=f"monitor.{env_id}.postgres.identity",
                        argv=(
                            "psql",
                            "-X",
                            "-w",
                            "-d",
                            str(database),
                            "-t",
                            "-A",
                            "-c",
                            "SELECT 1",
                        ),
                        timeout=_PROBE_TIMEOUT_SECONDS,
                        read_only=True,
                        text=True,
                    )
                )
            if bool(int(row["python_environment_owned"])):
                steps.append(
                    PreparedStep(
                        step_id=f"monitor.{env_id}.storage.python",
                        argv=(du, "-sb", str(row["python_environment_path"])),
                        cwd=str(Path(str(row["python_environment_path"])).parent),
                        timeout=_PROBE_TIMEOUT_SECONDS,
                        read_only=True,
                        text=True,
                    )
                )
            environment_root = Path(str(row["generated_config_path"])).parent
            for suffix, path in (
                ("cache", environment_root / "cache"),
                ("artifacts", environment_root / "artifacts"),
            ):
                steps.append(
                    PreparedStep(
                        step_id=f"monitor.{env_id}.storage.{suffix}",
                        argv=(du, "-sb", str(path)),
                        cwd=str(path.parent),
                        timeout=_PROBE_TIMEOUT_SECONDS,
                        read_only=True,
                        text=True,
                    )
                )
            if str(row["db_mode"]) == "copy" and row["target_db_name"]:
                database_name = str(row["target_db_name"]).replace("'", "''")
                steps.append(
                    PreparedStep(
                        step_id=f"monitor.{env_id}.storage.postgres",
                        argv=(
                            "psql",
                            "-X",
                            "-w",
                            "-d",
                            str(row["target_db_name"]),
                            "-t",
                            "-A",
                            "-c",
                            f"SELECT pg_database_size('{database_name}')",
                        ),
                        timeout=_PROBE_TIMEOUT_SECONDS,
                        read_only=True,
                        text=True,
                    )
                )
                try:
                    from odoo_instance_sdk.models import StartConfig

                    cfg = StartConfig.from_odoo_config(str(row["generated_config_path"]))
                    data_dir = Path(cfg.data_dir) if cfg.data_dir is not None else None
                    filestore = (
                        validate_filestore_containment(data_dir, str(row["target_db_name"]))
                        if data_dir is not None
                        else None
                    )
                except Exception:
                    filestore = None
                if filestore is not None:
                    steps.append(
                        PreparedStep(
                            step_id=f"monitor.{env_id}.storage.filestore",
                            argv=(du, "-sb", str(filestore)),
                            cwd=str(filestore.parent),
                            timeout=_PROBE_TIMEOUT_SECONDS,
                            read_only=True,
                            text=True,
                        )
                    )
            if self.git_provider is None:
                for suffix, args in git_commands:
                    steps.append(
                        PreparedStep(
                            step_id=f"monitor.{env_id}.git.{suffix}",
                            argv=("git", "-C", str(worktree), *args),
                            cwd=str(worktree),
                            timeout=_PROBE_TIMEOUT_SECONDS,
                            read_only=True,
                            text=True,
                        )
                    )
            projects.add((repository, resolved_project))
            if (repository / ".git").exists():
                repositories.add((repository, resolved_project))

        for repository, project in sorted(projects, key=lambda item: (str(item[0]), item[1])):
            if self.docker_provider is not None:
                continue
            from odoo_instance_sdk.internal.proc import SubprocessExecutor

            if isinstance(self._executor, SubprocessExecutor) and shutil.which("docker") is None:
                continue
            compose_file = repository / "docker-compose.yml"
            try:
                cluster = PostgresCluster.from_project(repository)
                if cluster.mode != "compose" or not isinstance(
                    cluster.compose_runner, SubprocessComposeRunner
                ):
                    continue
                if SubprocessComposeRunner.run.__module__ != (
                    "odoo_instance_sdk.internal.postgres_compose"
                ):
                    continue
                compose_file = cluster.compose_file
            except (ProjectManifestNotFoundError, PostgresClusterError, OSError, AssertionError):
                # The command still exposes the canonical production probe
                # when construction cannot inspect a manifest.  If a custom
                # runner was available, the branch above deliberately omitted
                # it so injected process seams retain their old behavior.
                pass
            steps.append(
                PreparedStep(
                    step_id=f"monitor.{project}.docker.resources",
                    argv=(
                        "docker",
                        "compose",
                        "-f",
                        str(compose_file),
                        "-p",
                        project,
                        "ps",
                        "--format",
                        "json",
                    ),
                    cwd=str(repository),
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    read_only=True,
                    text=True,
                )
            )
        for repository, project in sorted(repositories, key=lambda item: (str(item[0]), item[1])):
            steps.append(
                PreparedStep(
                    step_id=f"monitor.{project}.git.worktrees",
                    argv=(
                        "git",
                        "-C",
                        str(repository),
                        "worktree",
                        "list",
                        "--porcelain",
                        "-z",
                    ),
                    cwd=str(repository),
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    read_only=True,
                    text=True,
                )
            )
        return tuple(steps), rows

    def _snapshot_impl(
        self,
        project_id: str | None = None,
        *,
        include_removed: bool = False,
        probe_results: dict[str, ProcessResult] | None = None,
        catalog_rows: list[tuple[sqlite3.Row, sqlite3.Row | None]] | None = None,
    ) -> Snapshot:
        """Perform one coherent collection pass and return an immutable snapshot."""
        generated_at = datetime.now(UTC)
        db_path = self.catalog_path if self.catalog_path is not None else get_catalog_path()
        try:
            catalog = BackupCatalog(db_path=db_path)
        except (BackupCatalogError, sqlite3.Error) as exc:
            raise MonitorError("monitor catalog unavailable") from exc

        try:
            plan = self._plan_snapshot(
                catalog,
                project_id=project_id,
                include_removed=include_removed,
                probe_results=probe_results,
                catalog_rows=catalog_rows,
            )
        finally:
            catalog.close()
        resources, active_clusters = self._collect_cluster_resources(
            list(plan.projects), probe_results=probe_results
        )
        projects, environments = self._collect_snapshot_rows(
            plan.projects, resources, probe_results=probe_results
        )
        self._prune_caches(
            set(plan.environment_ids),
            set(plan.worktrees),
            active_clusters,
            set(plan.statuses),
            set(plan.cpu_points),
        )
        return Snapshot(
            schema_version=_SCHEMA_VERSION,
            generated_at=generated_at,
            projects=projects,
            environments=environments,
        )

    def _plan_snapshot(
        self,
        catalog: BackupCatalog,
        *,
        project_id: str | None = None,
        include_removed: bool = False,
        probe_results: dict[str, ProcessResult] | None = None,
        catalog_rows: list[tuple[sqlite3.Row, sqlite3.Row | None]] | None = None,
    ) -> _SnapshotPlan:
        """Read catalog runtime once and derive deterministic project plans."""
        if catalog_rows is None:
            try:
                rows = catalog.list_environments_with_runtimes(include_removed=include_removed)
            except (BackupCatalogError, sqlite3.Error) as exc:
                raise MonitorError("monitor catalog unavailable") from exc
        else:
            rows = catalog_rows
        groups: dict[str, list[_EnvironmentPlan]] = {}
        environment_ids: set[str] = set()
        worktrees: set[Path] = set()
        cpu_points: set[tuple[int, float]] = set()
        for row, runtime in rows:
            groups.setdefault(str(row["git_common_dir"]), []).append(_EnvironmentPlan(row, runtime))
            environment_ids.add(str(row["id"]))
            worktrees.add(Path(str(row["worktree_path"])).resolve())
            if runtime is not None:
                with contextlib.suppress(TypeError, ValueError):
                    cpu_points.add((int(runtime["root_pid"]), float(runtime["create_time"])))
        plans: list[_ProjectPlan] = []
        statuses: set[str] = set()
        for git_common, environments in groups.items():
            first = environments[0].row
            repo_root = Path(str(first["repository_root"]))
            resolved_project_id = f"project_{repo_key(repo_root, Path(git_common))}"
            # Filtering is intentionally before manifest/status/Docker work.
            # The catalog grouping and cache-pruning inputs remain cheap.
            if project_id is not None and resolved_project_id != project_id:
                continue
            if all(
                str(item.row["state"]) == EnvironmentState.REMOVED.value for item in environments
            ):
                cluster, state = None, None
            else:
                cluster, state = self._project_cluster(
                    repo_root,
                    statuses,
                    project_id=resolved_project_id,
                    probe_results=probe_results,
                )
            plans.append(
                _ProjectPlan(
                    resolved_project_id,
                    repo_root,
                    cluster,
                    state,
                    tuple(environments),
                )
            )
        return _SnapshotPlan(
            tuple(sorted(plans, key=lambda item: item.project_id)),
            frozenset(environment_ids),
            frozenset(worktrees),
            frozenset(statuses),
            frozenset(cpu_points),
        )

    def _project_cluster(
        self,
        repo_root: Path,
        statuses: set[str],
        *,
        project_id: str,
        probe_results: dict[str, ProcessResult] | None = None,
    ) -> tuple[PostgresCluster | None, PostgresClusterState | None]:
        try:
            if (
                probe_results is None
                or PostgresCluster.from_project.__module__ != "odoo_instance_sdk.resources.postgres"
            ):
                cluster = PostgresCluster.from_project(repo_root)
            else:
                from odoo_instance_sdk.project import ProjectConfig

                cluster = PostgresCluster._from_config(
                    ProjectConfig.load(repo_root),
                    repository_root=repo_root,
                    compose_runner=self._docker_runner,
                    project_id=project_id.removeprefix("project_"),
                )
            statuses.add(str(cluster.compose_file.resolve()))
            return cluster, self._cached_status(cluster, probe_results=probe_results)
        except (ProjectManifestNotFoundError, PostgresClusterError, OSError):
            return None, None

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
            projects.append(
                ProjectSummary(
                    id=plan.project_id,
                    name=plan.repo_root.name,
                    display_hint=plan.project_id.removeprefix("project_"),
                    environment_count=len(plan.environments),
                    cluster=self._cluster_snapshot(plan, resources.get(plan.project_id)),
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
        now = time.monotonic()
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
        now = time.monotonic()

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
            if getattr(runner, "requires_docker", True) and not docker_available():
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
        git_probes = None
        if probe_results is not None:
            git_probes = {
                key.rsplit(".git.", 1)[1]: value
                for key, value in probe_results.items()
                if key.startswith(f"monitor.{env_id}.git.")
            }
        git = self._collect_git(worktree, recorded=git_probes or None)
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
            db_mode=db_mode,  # type: ignore[arg-type]
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
                    for entry in worktree_list_porcelain(repository_root)
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
            return PortObservation(probe_address(cfg.http_interface, allocated_port).value)
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
        recorded: Mapping[str, ProcessResult] | None = None,
    ) -> GitActivity:
        if recorded is not None:
            return _recorded_git_activity(recorded)
        try:
            if self.git_provider is not None:
                key = worktree.resolve()
                cache_key: tuple[Path, str, str | None] = (key, "provider", None)
                cached = self._git_cache.get(cache_key)
                if cached is not None and time.monotonic() - cached[0] < _EXPENSIVE_TTL:
                    return cached[1]
                result = self.git_provider.collect(worktree)
                self._git_cache[cache_key] = (time.monotonic(), result)
            else:
                resolved = worktree.resolve()
                identity = _resolve_identity(resolved)
                cache_key = (resolved, identity[0], identity[3])
                # Identity probing is cheap.  Keep at most one expensive value
                # per worktree: a new HEAD or default tip must invalidate the old
                # result instead of growing the monitor for every commit.
                for stale_key in tuple(self._git_cache):
                    if stale_key[0] == resolved and stale_key != cache_key:
                        del self._git_cache[stale_key]
                cached = self._git_cache.get(cache_key)
                if cached is not None and time.monotonic() - cached[0] < _EXPENSIVE_TTL:
                    return cached[1]
                result = collect_git_activity_from_identity(resolved, identity)
                self._git_cache[cache_key] = (time.monotonic(), result)
        except Exception:
            result = _orphan_git()
        return result

    def _collect_storage(  # noqa: C901
        self,
        row: sqlite3.Row,
        env_id: str,
        db_mode: str,
        *,
        recorded: Mapping[str, ProcessResult] | None = None,
    ) -> StorageFootprint:
        if recorded is not None:

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
                return StorageFootprint(
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
            cached = self._storage_cache.get(env_id)
            if cached is not None and time.monotonic() - cached[0] < _EXPENSIVE_TTL:
                return cached[1]
            empty = _empty_storage()
            self._storage_cache[env_id] = (time.monotonic(), empty)
            return empty
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


__all__ = ["EnvironmentMonitor"]
