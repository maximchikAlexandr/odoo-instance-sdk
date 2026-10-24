from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.internal.storage_footprint import _directory_size, _walk_size
from odoo_instance_sdk.models import ProjectStorageFootprint, SnapshotSection
from odoo_instance_sdk.resources.monitor.planning import _EXPENSIVE_TTL

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import ProcessResult
    from odoo_instance_sdk.resources.monitor.planning import _ProjectPlan


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


class _StorageMixin:
    if TYPE_CHECKING:
        _project_storage_cache: dict[str, tuple[float, ProjectStorageFootprint]]

    def _collect_project_storage(
        self,
        plan: _ProjectPlan,
        sections: frozenset[SnapshotSection],
        probe_results: dict[str, ProcessResult] | None = None,
    ) -> ProjectStorageFootprint:
        if "storage" not in sections:
            return ProjectStorageFootprint(total_bytes=None, complete=False)
        now = time.monotonic()
        cached = self._project_storage_cache.get(plan.project_id)
        if cached is not None and now - cached[0] < _EXPENSIVE_TTL:
            return cached[1]
        try:
            root = plan.repo_root.resolve()
            for environment in plan.environments:
                worktree = Path(str(environment.row["worktree_path"])).resolve()
                if _contains(root, worktree) or _contains(worktree, root):
                    footprint = ProjectStorageFootprint(total_bytes=None, complete=False)
                    self._project_storage_cache[plan.project_id] = (now, footprint)
                    return footprint
            if probe_results is None:
                total = _directory_size(root)
            else:
                result = probe_results.get(f"monitor.{plan.project_id}.storage.project")
                stdout = result.stdout if result is not None else ""
                try:
                    total = (
                        int(str(stdout).strip().split()[0])
                        if result is not None and result.returncode == 0
                        else None
                    )
                except (ValueError, IndexError):
                    total = None
                if total is None:
                    total = _walk_size(root)
        except OSError:
            total = None
        footprint = ProjectStorageFootprint(total_bytes=total, complete=total is not None)
        self._project_storage_cache[plan.project_id] = (now, footprint)
        return footprint
