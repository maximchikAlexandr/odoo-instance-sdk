from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from odoo_instance_sdk.internal.process_metrics import CpuPoint, ProcessTreeResult
from odoo_instance_sdk.models import (
    ClusterResourceSnapshot,
    ClusterSnapshot,
    DatabaseFootprint,
    EnvironmentSnapshot,
    GitActivity,
    GitActivityState,
    GitDiff,
    PostgresClusterState,
    ProjectSummary,
    PythonEnvFootprint,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster

_EXPENSIVE_TTL = 15.0
_CLUSTER_STATUS_TTL = 5.0
_SCHEMA_VERSION = 4
_PROBE_TIMEOUT_SECONDS = 5.0


def _default_monitor_executor() -> ProcessExecutor:
    from odoo_instance_sdk.internal.proc import SubprocessExecutor

    return SubprocessExecutor()


if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import (
        ProcessExecutor,
        ProcessResult,
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
    project_runtime: sqlite3.Row | None = None


@dataclass(frozen=True, slots=True)
class _EnvironmentPlan:
    """A catalog row and the single runtime read for this snapshot pass."""

    row: sqlite3.Row
    runtime: sqlite3.Row | None


@dataclass(frozen=True, slots=True)
class SnapshotSelection:
    """The records selected from one already-collected monitor snapshot."""

    environment: EnvironmentSnapshot
    project: ProjectSummary
    cluster: ClusterSnapshot | None


def select_snapshot_environment(
    snapshot: Snapshot,
    selector: str | None = None,
    *,
    cwd: Path | None = None,
    worktree_paths: Mapping[str, str] | None = None,
) -> SnapshotSelection:
    """Select an environment, project, and cluster without recollecting metrics."""
    candidates = list(snapshot.environments)
    if selector is None:
        current = (cwd or Path.cwd()).resolve()
        paths = worktree_paths or {}
        candidates = [
            item
            for item in candidates
            if item.id in paths and _path_contains(current, Path(paths[item.id]))
        ]
        if not candidates:
            raise ValueError("No environment matches the current working directory")
    else:
        by_id = [item for item in candidates if item.id == selector]
        by_name = [item for item in candidates if item.name == selector]
        candidates = by_id or by_name
        if not candidates:
            raise ValueError(f"Environment not found: {selector}")
        if len(candidates) > 1:
            raise ValueError(
                f"Ambiguous environment selector {selector!r}: "
                + ", ".join(item.id for item in candidates)
            )
    if len(candidates) > 1:
        raise ValueError(
            "Ambiguous current environment: " + ", ".join(item.id for item in candidates)
        )
    environment = candidates[0]
    project = next((item for item in snapshot.projects if item.id == environment.project_id), None)
    if project is None:
        raise ValueError(f"Environment owner project not found: {environment.project_id}")
    return SnapshotSelection(environment=environment, project=project, cluster=project.cluster)


select_environment_snapshot = select_snapshot_environment
select_snapshot = select_snapshot_environment


def _path_contains(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.resolve())
    except ValueError:
        return False
    return True


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


def _orphan_git(default_branch: str = "main") -> GitActivity:
    return GitActivity(
        default_branch=default_branch,
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


def _recorded_git_activity(  # noqa: C901
    results: Mapping[str, ProcessResult], base_ref: str | None = "main"
) -> GitActivity:
    """Rebuild the complete Git collector result from captured probe output."""

    default_branch = base_ref or "unknown"

    def output(name: str) -> tuple[int, str]:
        result = results.get(name)
        if result is None:
            return 127, ""
        value = result.stdout if isinstance(result.stdout, str) else ""
        return result.returncode, value.strip()

    head_rc, head = output("head")
    branch_rc, branch = output("branch")
    if head_rc != 0 or not head or branch_rc != 0 or not branch:
        return _orphan_git(default_branch)
    upstream_rc, default_tip = output("upstream")
    if upstream_rc == 0 and default_tip:
        prefix = "upstream"
    else:
        local_rc, default_tip = output("local_main")
        if local_rc != 0 or not default_tip:
            local_rc, default_tip = output("local_exact")
            prefix = "local_exact"
        else:
            prefix = "local"
        if local_rc != 0 or not default_tip:
            return _orphan_git(default_branch)
    merge_rc, merge_base = output(f"{prefix}_merge_base")
    if merge_rc == 1:
        return GitActivity(
            default_branch=default_branch,
            head_sha=head,
            short_sha=head[:7],
            branch=branch,
            ahead=None,
            behind=None,
            diff=None,
            state=GitActivityState.ORPHAN,
        )
    if merge_rc != 0 or not merge_base:
        return _orphan_git(default_branch)
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
        return _orphan_git(default_branch)
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
        default_branch=default_branch,
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
        memory_bytes=None,
        started_at=None,
        http_url=None,
        http_port=None,
        database_name=None,
        commit_sha=None,
        branch=None,
    )
