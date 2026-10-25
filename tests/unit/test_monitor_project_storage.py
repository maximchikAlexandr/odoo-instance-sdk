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
            SimpleNamespace(
                row={
                    "id": str(index),
                    "state": "ready",
                    "worktree_path": str(worktree),
                    "python_environment_owned": 0,
                    "generated_config_path": str(worktree / ".odcli" / "odoo.conf"),
                    "dependency_lock_path": str(worktree / ".odcli" / "requirements.lock"),
                    "db_mode": "shared",
                    "target_db_name": None,
                    "source_db_name": None,
                }
            )
            for index, worktree in enumerate(worktrees)
        ),
        backups=(),
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
        cast("Any", monitor), cast("Any", plan), frozenset({"storage", "postgresql"})
    )
    second = _StorageMixin._collect_project_storage(
        cast("Any", monitor), cast("Any", plan), frozenset({"storage", "postgresql"})
    )

    assert first == ProjectStorageFootprint(total_bytes=123, complete=True, backup_bytes=0)
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
    assert ambiguous == ProjectStorageFootprint(total_bytes=None, complete=False, backup_bytes=0)


def test_project_storage_does_not_count_a_worktree_contained_in_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    worktree = root / "nested-worktree"
    worktree.mkdir(parents=True)
    measured: list[Path] = []

    def size(path: Path) -> int:
        measured.append(path)
        return 100

    monkeypatch.setattr(storage_module, "_directory_size", size)

    result = _StorageMixin._collect_project_storage(
        cast("Any", _monitor()),
        cast("Any", _plan(root, worktree)),
        frozenset({"storage", "postgresql"}),
    )

    assert result == ProjectStorageFootprint(total_bytes=100, complete=True, backup_bytes=0)
    assert measured == [root]


def test_project_storage_adds_contained_worktrees_and_available_backup_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "file").write_bytes(b"1234")
    backup = tmp_path / "backup.zip"
    backup.write_bytes(b"backup")
    monkeypatch.setattr(storage_module, "_directory_size", lambda path: 100 if path == root else 4)
    plan = _plan(root, worktree)
    plan.backups = (
        {"state": "available", "path": str(backup)},
        {"state": "available", "path": str(backup)},
    )

    result = _StorageMixin._collect_project_storage(
        cast("Any", _monitor()), cast("Any", plan), frozenset({"storage"})
    )

    assert result == ProjectStorageFootprint(total_bytes=110, complete=True, backup_bytes=6)


def test_project_storage_counts_shared_database_and_filestore_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.models import StartConfig

    root = tmp_path / "checkout"
    root.mkdir()
    worktrees = (tmp_path / "one", tmp_path / "two")
    for worktree in worktrees:
        worktree.mkdir()
    data_dir = root / "data"
    (data_dir / "filestore" / "shared_db").mkdir(parents=True)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text(
        '[project]\nmanaged_filestore = "data"\n', encoding="utf-8"
    )
    config = SimpleNamespace(
        db_host="localhost", db_port=5432, db_user="odoo", db_password=None, data_dir=data_dir
    )
    monkeypatch.setattr(
        StartConfig,
        "from_odoo_config",
        classmethod(lambda _cls, _path: config),
    )
    plan = _plan(root, *worktrees)
    plan.cluster = SimpleNamespace(endpoint_host="localhost", endpoint_port=5432, _user="odoo")
    plan.project_runtime = {"database_name": "shared_db"}
    for env in plan.environments:
        env.row.update(db_mode="copy", target_db_name="shared_db")
    results = {
        "monitor.project-test.storage.project": SimpleNamespace(returncode=0, stdout="100\troot"),
        "monitor.0.storage.worktree": SimpleNamespace(returncode=0, stdout="4\tone"),
        "monitor.1.storage.worktree": SimpleNamespace(returncode=0, stdout="5\ttwo"),
        "monitor.0.storage.postgres": SimpleNamespace(returncode=0, stdout="20"),
        "monitor.1.storage.postgres": SimpleNamespace(returncode=0, stdout="20"),
        "monitor.0.storage.filestore": SimpleNamespace(returncode=0, stdout="30\tfilestore"),
        "monitor.1.storage.filestore": SimpleNamespace(returncode=0, stdout="30\tfilestore"),
    }

    result = _StorageMixin._collect_project_storage(
        cast("Any", _monitor()),
        cast("Any", plan),
        frozenset({"storage", "postgresql"}),
        cast("Any", results),
    )

    assert result == ProjectStorageFootprint(total_bytes=129, complete=True, backup_bytes=0)

    failed_results = {
        **results,
        "monitor.0.storage.postgres": SimpleNamespace(returncode=1, stdout=""),
        "monitor.1.storage.postgres": SimpleNamespace(returncode=1, stdout=""),
    }
    partial = _StorageMixin._collect_project_storage(
        cast("Any", _monitor()),
        cast("Any", plan),
        frozenset({"storage", "postgresql"}),
        cast("Any", failed_results),
    )
    assert partial == ProjectStorageFootprint(total_bytes=109, complete=False, backup_bytes=0)


def test_project_storage_keeps_known_total_when_available_backup_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setattr(storage_module, "_directory_size", lambda _path: 100)
    plan = _plan(root)
    plan.backups = ({"state": "available", "path": str(tmp_path / "missing.zip")},)

    result = _StorageMixin._collect_project_storage(
        cast("Any", _monitor()), cast("Any", plan), frozenset({"storage"})
    )

    assert result == ProjectStorageFootprint(total_bytes=100, complete=False, backup_bytes=None)


def test_project_storage_unmeasured_checkout_and_missing_backup_have_no_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage_module, "_directory_size", lambda _path: None)
    plan = _plan(tmp_path)
    plan.backups = ({"state": "available", "path": str(tmp_path / "missing.zip")},)

    result = _StorageMixin._collect_project_storage(
        cast("Any", _monitor()), cast("Any", plan), frozenset({"storage"})
    )

    assert result == ProjectStorageFootprint(total_bytes=None, complete=False, backup_bytes=None)


def test_project_runtime_main_only_counts_database_and_contained_filestore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    root = tmp_path / "project"
    manifest_dir = root / ".odcli"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "project.toml").write_text(
        '[project]\ndefault_source_database = "main_db"\nmanaged_filestore = ".odcli/filestore"\n',
        encoding="utf-8",
    )
    filestore = manifest_dir / "filestore" / "filestore" / "main_db"
    filestore.mkdir(parents=True)
    (filestore / "blob").write_bytes(b"blob")
    cluster = SimpleNamespace(endpoint_host="localhost", endpoint_port=5432, _user="odoo")
    monkeypatch.setattr(
        PostgresCluster,
        "from_project",
        classmethod(lambda _cls, _repository: cluster),
    )

    runtime = {"owner_id": "project-test", "database_name": "main_db"}
    measured_databases: set[tuple[str, int, str]] = set()
    measured_filestores: set[Path] = set()
    steps = storage_module._project_runtime_storage_steps(
        cast("Any", runtime),
        root,
        measured_databases,
        measured_filestores,
        du="du",
        postgresql=True,
    )

    assert {step.step_id for step in steps} == {
        "monitor.project-test.storage.postgres",
        "monitor.project-test.storage.filestore",
    }
    assert filestore.resolve() in measured_filestores
    assert (
        storage_module._project_runtime_storage_steps(
            cast("Any", runtime),
            root,
            measured_databases,
            measured_filestores,
            du="du",
            postgresql=True,
        )
        == ()
    )

    environment_databases = {("localhost", 5432, "main_db")}
    environment_filestores = {filestore.resolve()}
    assert (
        storage_module._project_runtime_storage_steps(
            cast("Any", runtime),
            root,
            environment_databases,
            environment_filestores,
            du="du",
            postgresql=True,
        )
        == ()
    )

    plan = _plan(root)
    plan.project_runtime = runtime
    plan.cluster = cluster
    monkeypatch.setattr(storage_module, "_walk_size", lambda _path: None)
    result = _StorageMixin._collect_project_storage(
        cast("Any", _monitor()),
        cast("Any", plan),
        frozenset({"storage", "postgresql"}),
        cast(
            "Any",
            {
                "monitor.project-test.storage.postgres": SimpleNamespace(returncode=0, stdout="20"),
                "monitor.project-test.storage.filestore": SimpleNamespace(
                    returncode=0, stdout="30\tfilestore"
                ),
            },
        ),
    )

    assert result == ProjectStorageFootprint(total_bytes=50, complete=False, backup_bytes=0)
