from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    MonitorError,
    PostgresClusterError,
    ProjectManifestNotFoundError,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import (
    SNAPSHOT_SECTIONS,
    GitActivityState,
    SnapshotObservation,
    SnapshotRequest,
    SnapshotSection,
    SnapshotSectionObservation,
)
from odoo_instance_sdk.resources.environment import EnvironmentState
from odoo_instance_sdk.resources.monitor.planning import (
    _EnvironmentPlan,
    _ProjectPlan,
    _SnapshotPlan,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
    from odoo_instance_sdk.internal.proc import ProcessResult
    from odoo_instance_sdk.models import (
        ClusterResourceSnapshot,
        EnvironmentSnapshot,
        PostgresClusterState,
        ProjectSummary,
    )
    from odoo_instance_sdk.storage.backup_catalog import MonitorCatalogSnapshot


class _SnapshotPlanningMixin:
    if TYPE_CHECKING:
        _docker_runner: ComposeRunner

        def _cached_status(
            self,
            cluster: PostgresCluster,
            *,
            probe_results: dict[str, ProcessResult] | None = None,
        ) -> PostgresClusterState: ...

    def _plan_snapshot(  # noqa: C901
        self,
        catalog: BackupCatalog,
        *,
        project_id: str | None = None,
        include_removed: bool = False,
        probe_results: dict[str, ProcessResult] | None = None,
        catalog_rows: MonitorCatalogSnapshot | None = None,
        request: SnapshotRequest | None = None,
    ) -> _SnapshotPlan:
        """Read catalog runtime once and derive deterministic project plans."""
        if catalog_rows is None:
            try:
                snapshot_rows = catalog._monitor_snapshot_rows(include_removed=include_removed)
            except (BackupCatalogError, sqlite3.Error) as exc:
                raise MonitorError("monitor catalog unavailable") from exc
        else:
            snapshot_rows = catalog_rows
        selected = request or SnapshotRequest(
            project_ids=(project_id,) if project_id is not None else (),
            include_removed=include_removed,
        )
        sections = frozenset(selected.sections)
        rows = snapshot_rows.environments
        registered_projects = snapshot_rows.projects
        project_runtimes = snapshot_rows.project_runtimes
        groups: dict[str, list[_EnvironmentPlan]] = {}
        project_details: dict[str, Path] = {
            str(row["project_id"]): Path(str(row["repository_root"])).resolve()
            for row in registered_projects
        }
        project_runtime_by_id = {str(row["owner_id"]): row for row in project_runtimes}
        environment_ids: set[str] = set()
        worktrees: set[Path] = set()
        cpu_points: set[tuple[int, float]] = set()
        for row, runtime in rows:
            repository = Path(str(row["repository_root"])).resolve()
            git_common = Path(str(row["git_common_dir"])).resolve()
            resolved_project_id = str(
                row["project_id"] or f"project_{repo_key(repository, git_common)}"
            )
            if selected.project_ids and resolved_project_id not in selected.project_ids:
                continue
            if selected.environment_ids and str(row["id"]) not in selected.environment_ids:
                continue
            groups.setdefault(resolved_project_id, []).append(_EnvironmentPlan(row, runtime))
            project_details.setdefault(resolved_project_id, repository)
            environment_ids.add(str(row["id"]))
            worktrees.add(Path(str(row["worktree_path"])).resolve())
            if runtime is not None:
                with contextlib.suppress(TypeError, ValueError):
                    cpu_points.add((int(runtime["root_pid"]), float(runtime["create_time"])))
        for runtime in project_runtimes:
            with contextlib.suppress(TypeError, ValueError):
                cpu_points.add((int(runtime["root_pid"]), float(runtime["create_time"])))
        plans: list[_ProjectPlan] = []
        statuses: set[str] = set()
        for resolved_project_id, environments in groups.items():
            first = environments[0].row
            repo_root = Path(str(first["repository_root"]))
            # Filtering is intentionally before manifest/status/Docker work.
            # The catalog grouping and cache-pruning inputs remain cheap.
            if project_id is not None and resolved_project_id != project_id:
                continue
            if selected.project_ids and resolved_project_id not in selected.project_ids:
                continue
            if all(
                str(item.row["state"]) == EnvironmentState.REMOVED.value for item in environments
            ):
                cluster, state = None, None
            elif sections & {"postgresql", "docker"}:
                cluster, state = self._project_cluster(
                    repo_root,
                    statuses,
                    project_id=resolved_project_id,
                    probe_results=probe_results,
                )
            else:
                cluster, state = None, None
            plans.append(
                _ProjectPlan(
                    resolved_project_id,
                    repo_root,
                    cluster,
                    state,
                    tuple(environments),
                    project_runtime=project_runtime_by_id.get(resolved_project_id),
                )
            )
        for resolved_project_id, repo_root in project_details.items():
            if resolved_project_id in groups:
                continue
            if project_id is not None and resolved_project_id != project_id:
                continue
            if selected.project_ids and resolved_project_id not in selected.project_ids:
                continue
            if sections & {"postgresql", "docker"}:
                cluster, state = self._project_cluster(
                    repo_root,
                    statuses,
                    project_id=resolved_project_id,
                    probe_results=probe_results,
                )
            else:
                cluster, state = None, None
            plans.append(
                _ProjectPlan(
                    resolved_project_id,
                    repo_root,
                    cluster,
                    state,
                    (),
                    project_runtime=project_runtime_by_id.get(resolved_project_id),
                )
            )
        return _SnapshotPlan(
            tuple(sorted(plans, key=lambda item: item.project_id)),
            frozenset(environment_ids),
            frozenset(worktrees),
            frozenset(statuses),
            frozenset(cpu_points),
            sections,
        )

    @staticmethod
    def _snapshot_observation(
        observed_at: datetime,
        sections: frozenset[SnapshotSection],
        projects: tuple[ProjectSummary, ...],
        environments: tuple[EnvironmentSnapshot, ...],
        resources: dict[str, ClusterResourceSnapshot],
        section_outcomes: Mapping[SnapshotSection, str] | None = None,
    ) -> SnapshotObservation:
        """Summarize selected collectors without manufacturing unavailable values."""
        completed: set[SnapshotSection] = set(sections)
        reasons: dict[SnapshotSection, str] = {}
        for section, reason in (section_outcomes or {}).items():
            if section in sections:
                completed.discard(section)
                reasons[section] = reason[:160]
        if "docker" in sections:
            failures = {
                str(resource.unavailability_reason)
                for resource in resources.values()
                if resource.unavailability_reason is not None
            }
            if failures:
                completed.discard("docker")
                reasons["docker"] = "; ".join(sorted(failures))[:160]
        if (
            "git" in sections
            and environments
            and all(item.git.state is GitActivityState.ORPHAN for item in environments)
        ):
            completed.discard("git")
            reasons["git"] = "git observation unavailable"
        if (
            "storage" in sections
            and environments
            and any(not item.storage.complete for item in environments)
        ):
            completed.discard("storage")
            reasons["storage"] = "storage observation incomplete"
        unknown = tuple(sorted(set(sections) - completed))
        observations = tuple(
            SnapshotSectionObservation(
                section=section,
                observed_at=observed_at,
                complete=section in completed,
                reason=reasons.get(section),
            )
            for section in sorted(sections)
        )
        return SnapshotObservation(
            schema_version=3,
            observed_at=observed_at,
            requested_sections=tuple(
                section for section in SNAPSHOT_SECTIONS if section in sections
            ),
            completed_sections=tuple(
                section for section in SNAPSHOT_SECTIONS if section in completed
            ),
            unknown_sections=unknown,
            sections=observations,
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
