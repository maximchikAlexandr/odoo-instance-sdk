from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from odoo_instance_sdk.models import ProjectStorageFootprint
from odoo_instance_sdk.resources.monitor.collection_parts import storage as storage_module
from odoo_instance_sdk.resources.monitor.collection_parts.storage import _StorageMixin

if TYPE_CHECKING:
    import pytest


def _monitor() -> Any:
    return SimpleNamespace(_project_storage_cache={})


def _plan(root: Path, *worktrees: Path) -> SimpleNamespace:
    return SimpleNamespace(
        project_id="project-test",
        repo_root=root,
        environments=tuple(
            SimpleNamespace(row={"worktree_path": str(worktree)}) for worktree in worktrees
        ),
    )


def test_project_storage_measures_registered_root_once_with_ttl_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    measured: list[Path] = []

    def size(path: Path) -> int:
        measured.append(path)
        return 123

    monkeypatch.setattr(storage_module, "_directory_size", size)
    monitor = _monitor()
    plan = _plan(tmp_path)

    first = _StorageMixin._collect_project_storage(
        cast("Any", monitor), cast("Any", plan), frozenset({"storage"})
    )
    second = _StorageMixin._collect_project_storage(
        cast("Any", monitor), cast("Any", plan), frozenset({"storage"})
    )

    assert first == ProjectStorageFootprint(total_bytes=123, complete=True)
    assert second is first
    assert measured == [tmp_path]


def test_project_storage_is_unavailable_without_selection_or_when_roots_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        storage_module,
        "_directory_size",
        lambda _path: (_ for _ in ()).throw(AssertionError("storage must not be measured")),
    )
    root = tmp_path / "checkout"
    nested = root / "legacy-worktree"
    monitor = _monitor()

    unselected = _StorageMixin._collect_project_storage(
        cast("Any", monitor), cast("Any", _plan(root)), frozenset()
    )
    ambiguous = _StorageMixin._collect_project_storage(
        cast("Any", monitor), cast("Any", _plan(root, nested)), frozenset({"storage"})
    )

    assert unselected == ProjectStorageFootprint(total_bytes=None, complete=False)
    assert ambiguous == ProjectStorageFootprint(total_bytes=None, complete=False)
