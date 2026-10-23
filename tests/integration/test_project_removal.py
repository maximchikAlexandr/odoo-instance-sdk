from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

import odoo_instance_sdk.resources.publication as publication_resource

from odoo_instance_sdk.exceptions import LockConflictError, ProjectRemovalError
from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal import publication_routes
from odoo_instance_sdk.internal.cluster_resources import build_container
from odoo_instance_sdk.internal.dbprep import bootstrap
from odoo_instance_sdk.internal.locks import exclusive_lock, postgres_cluster_lock_path
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    RunContext,
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.project_data_purge import (
    ComposeClusterPurgeAdapter,
    DatabasePurgeAdapter,
    DatabasePurgeTarget,
)
from odoo_instance_sdk.internal.project_filesystem_purge import (
    FilesystemPurgeAdapter,
    FilesystemPurgeTarget,
)
from odoo_instance_sdk.internal.project_removal import (
    InternalRemovalDecision,
    ProjectRegistration,
    ProjectRemovalProjection,
)
from odoo_instance_sdk.internal.project_removal_coordinator import (
    ProjectRemovalAdapter,
    _projection as coordinator_projection,
    capture_project_removal_command,
    expected_project_confirmation_for_command,
    set_project_confirmation,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.resource_inventory import (
    ActiveUse,
    OwnershipConfidence,
    VolumeResourceSource,
)
from odoo_instance_sdk.models import Backup, BackupFormat
from odoo_instance_sdk.models.database_inventory import (
    DatabaseInventoryItem,
    DatabaseInventoryResult,
)
from odoo_instance_sdk.models.project_removal import (
    ProjectRemovalResult,
    ProjectResourceDecision,
    ProjectSelector,
)
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.project_init import (
    _BootstrapFollowup,
    _compose_followup_steps,
    _execute_bootstrap_followup_phase,
    init_project,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.resources.project import ProjectResource
from odoo_instance_sdk.resources.publication import PublicationSettings
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient

pytestmark = pytest.mark.integration


def _catalog(tmp_path: Path) -> tuple[BackupCatalog, str, Path]:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    catalog._register_project(project_id, root, root / ".git")
    return catalog, project_id, root


def _assert_no_purge_completion_marker(catalog: BackupCatalog) -> None:
    columns = {str(row[1]) for row in catalog._conn.execute("PRAGMA table_info(projects)")}
    assert "purge_completed_at" not in columns


def _legacy_environment(catalog: BackupCatalog, environment_id: str, root: Path) -> None:
    catalog.create_environment(
        {
            "id": environment_id,
            "name": environment_id,
            "repository_root": str(root),
            "git_common_dir": str(root / ".git"),
            "branch": "main",
            "base_ref": "HEAD",
            "worktree_path": str(root / "worktree"),
            "generated_config_path": str(root / "odoo.conf"),
            "python_environment_path": str(root / ".venv"),
            "python_environment_owned": False,
            "dependency_lock_path": str(root / "lock"),
            "db_mode": "shared",
            "runtime_json": "{}",
            "state": "ready",
            "created_at": "2026-01-01T00:00:00",
        }
    )


def _projection(
    project_id: str,
    root: Path,
    resources: tuple[tuple[str, str], ...],
) -> ProjectRemovalProjection:
    decisions = tuple(
        InternalRemovalDecision(
            identity=identity,
            resource_type=resource_type,
            action="delete",
            reason="exclusive_sdk_ownership",
            target="<local>/resource",
            ownership=OwnershipConfidence.PROVEN,
            active_use=ActiveUse.INACTIVE,
        )
        for identity, resource_type in resources
    )
    return ProjectRemovalProjection(
        project_id=project_id,
        registration_state="registered",
        repository_root=root,
        expected_confirmation=f"{root.name}:{project_id}",
        decisions=decisions,
        fingerprint="project-removal-matrix",
    )


def test_public_forget_refuses_matching_legacy_environment(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    _legacy_environment(catalog, "legacy-alpha", root)
    client = SimpleNamespace(get_catalog=lambda: catalog)

    with pytest.raises(ProjectRemovalError, match="environment:legacy-alpha"):
        ProjectResource(_client=cast("OdooClient", client)).remove(
            ProjectSelector.from_project_id(project_id)
        )

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert catalog.get_environment("legacy-alpha") is not None
    assert root.exists()
    catalog.close()


def test_public_init_then_immediate_forget_revalidates_same_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: catalog_path
    )
    root = tmp_path / "initialized-project"
    root.mkdir()

    init_project(
        root,
        ProjectConfig(repository_root=root, python="3.12", default_source_database="owned_db"),
        postgres_allocated=False,
        allow_partial=True,
    )
    catalog = BackupCatalog(db_path=catalog_path)
    row = catalog.list_projects()[0]
    project_id = str(row["project_id"])
    database_inventory = DatabaseInventoryResult(
        cluster="127.0.0.1:5432",
        databases=(
            DatabaseInventoryItem(
                cluster="127.0.0.1:5432",
                cluster_id=None,
                name="owned_db",
                logical_size_bytes=1,
                active_sessions=0,
                is_default=True,
            ),
        ),
        tracked=True,
    )
    instance = SimpleNamespace(
        databases=SimpleNamespace(list_inventory=lambda *_args, **_kwargs: database_inventory),
        _postgres_cluster=None,
    )
    client = SimpleNamespace(
        get_catalog=lambda: catalog,
        instance=SimpleNamespace(from_project=lambda _project: instance),
    )

    result = ProjectResource(_client=cast("OdooClient", client)).remove(
        ProjectSelector.from_path(str(root))
    )

    assert result.registration_state == "forgotten"
    assert root.exists()
    assert (root / ".odcli" / "project.toml").exists()
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]

    init_project(
        root,
        ProjectConfig(repository_root=root, python="3.12", default_source_database="owned_db"),
        postgres_allocated=False,
        allow_partial=True,
    )
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_purge_reuses_captured_git_decision_before_forget(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    subprocess.run(("git", "init", str(root)), check=True, capture_output=True, text=True)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text("[project]\n", encoding="utf-8")
    untracked = root / "untracked-addon.py"
    untracked.write_text("preserve me\n", encoding="utf-8")
    instance = SimpleNamespace(
        databases=SimpleNamespace(
            list_inventory=lambda *_args, **_kwargs: DatabaseInventoryResult(
                cluster="127.0.0.1:5432", databases=(), tracked=True
            )
        ),
        _postgres_cluster=None,
    )
    client = SimpleNamespace(
        get_catalog=lambda: catalog,
        instance=SimpleNamespace(from_project=lambda _project: instance),
    )

    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )

    assert command.plan.fingerprint
    assert command.plan.steps[0].step_id == f"project-removal:git-status:{project_id}"
    result = command.run()

    assert result.registration_state == "forgotten"
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    assert root.exists()
    assert untracked.read_text(encoding="utf-8") == "preserve me\n"
    catalog.close()


def test_public_forget_refuses_reactivation_after_capture(tmp_path: Path) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    assert catalog.forget_project(project_id)
    client = SimpleNamespace(get_catalog=lambda: catalog)
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id)
    )
    assert catalog.reactivate_project(project_id, root, root / ".git")

    with pytest.raises(ProjectRemovalError, match="registration state changed"):
        command.run()

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert root.exists()
    catalog.close()


def test_public_forget_refuses_nonblocking_resource_decision_drift(tmp_path: Path) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    client = SimpleNamespace(get_catalog=lambda: catalog)
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id)
    )
    _legacy_environment(catalog, "removed-after-capture", root)
    catalog._conn.execute(
        "UPDATE environments SET state = 'removed' WHERE id = ?", ("removed-after-capture",)
    )
    catalog._conn.commit()

    with pytest.raises(ProjectRemovalError, match="plan is stale"):
        command.run()

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert catalog.get_environment("removed-after-capture") is not None
    catalog.close()


def test_public_forget_refuses_post_capture_compose_and_database_activation(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    legacy_id = repo_key(root, root / ".git")
    compose_project = f"odcli_pg_{legacy_id}"
    volume_name = f"pgdata_{legacy_id}"
    pending = catalog._ensure_postgres_cluster_pending(project_id, compose_project, volume_name)
    client = SimpleNamespace(get_catalog=lambda: catalog)
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id)
    )

    active = catalog._activate_postgres_cluster(
        pending.cluster_id, project_id, compose_project, volume_name
    )
    catalog._record_project_database_binding(
        project_id, active.cluster_id, "127.0.0.1", 5432, "owned_db", 19576
    )

    with pytest.raises(ProjectRemovalError, match="plan is stale"):
        command.run()

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert catalog._get_postgres_cluster_by_id(active.cluster_id).state == "active"  # type: ignore[union-attr]
    assert catalog._project_bound_database_records(
        project_id, active.cluster_id, "127.0.0.1", 5432
    ) == (("owned_db", 19576),)
    catalog.close()


def test_public_purge_does_not_treat_reappeared_owned_manifest_as_inert(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    assert catalog.forget_project(project_id)
    subprocess.run(("git", "init", str(root)), check=True, capture_output=True, text=True)
    (root / ".gitignore").write_text(".odcli/\n", encoding="utf-8")
    (root / "tracked.txt").write_text("registered source\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(root), "add", ".gitignore", "tracked.txt"), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "baseline",
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text("[project]\n", encoding="utf-8")
    database_inventory = DatabaseInventoryResult(
        cluster="127.0.0.1:5432", databases=(), tracked=True
    )
    instance = SimpleNamespace(
        databases=SimpleNamespace(list_inventory=lambda *_args, **_kwargs: database_inventory),
        _postgres_cluster=None,
    )
    client = SimpleNamespace(
        get_catalog=lambda: catalog,
        instance=SimpleNamespace(from_project=lambda _project: instance),
    )

    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    assert any(step.mutating for step in command.plan.steps)

    result = command.run()

    assert result.registration_state == "forgotten"
    assert any(item.action == "delete" for item in result.completed)
    assert not manifest.exists()
    _assert_no_purge_completion_marker(catalog)
    catalog.close()


def _prepare_registered_empty(_catalog: BackupCatalog, _project_id: str, _root: Path) -> None:
    return


def _prepare_current_environment_active(
    catalog: BackupCatalog, project_id: str, root: Path
) -> None:
    assert catalog.forget_project(project_id)
    _legacy_environment(catalog, "environment-after-purge", root)
    catalog._conn.execute(
        "UPDATE environments SET project_id = ?, state = 'ready' WHERE id = ?",
        (project_id, "environment-after-purge"),
    )
    catalog._conn.commit()


def _prepare_legacy_environment_active(catalog: BackupCatalog, project_id: str, root: Path) -> None:
    assert catalog.forget_project(project_id)
    _legacy_environment(catalog, "environment-after-purge", root)
    catalog._conn.execute(
        "UPDATE environments SET state = 'ready' WHERE id = ?",
        ("environment-after-purge",),
    )
    catalog._conn.commit()


def _prepare_current_worktree_reappeared(
    catalog: BackupCatalog, project_id: str, root: Path
) -> None:
    assert catalog.forget_project(project_id)
    _legacy_environment(catalog, "environment-after-purge", root)
    catalog._conn.execute(
        "UPDATE environments SET project_id = ?, state = 'removed' WHERE id = ?",
        (project_id, "environment-after-purge"),
    )
    catalog._conn.commit()
    (root / "worktree").mkdir()


def _prepare_legacy_python_symlink_reappeared(
    catalog: BackupCatalog, project_id: str, root: Path
) -> None:
    assert catalog.forget_project(project_id)
    _legacy_environment(catalog, "environment-after-purge", root)
    catalog._conn.execute(
        "UPDATE environments SET state = 'removed', python_environment_owned = 1 WHERE id = ?",
        ("environment-after-purge",),
    )
    catalog._conn.commit()
    (root / ".venv").symlink_to(root / "missing-python", target_is_directory=True)


def _assert_registered_empty(catalog: BackupCatalog, project_id: str, _root: Path) -> None:
    row = catalog.get_project(project_id)
    assert row is not None and row["registration_state"] == "registered"
    assert catalog.get_environment("environment-after-purge") is None


def _assert_current_environment_active(
    catalog: BackupCatalog, project_id: str, _root: Path
) -> None:
    _assert_forgotten_environment_state(catalog, project_id, "ready")


def _assert_legacy_environment_active(catalog: BackupCatalog, project_id: str, _root: Path) -> None:
    _assert_forgotten_environment_state(catalog, project_id, "ready")


def _assert_current_worktree_reappeared(
    catalog: BackupCatalog, project_id: str, root: Path
) -> None:
    _assert_forgotten_environment_state(catalog, project_id, "removed")
    assert (root / "worktree").is_dir()


def _assert_legacy_python_symlink_reappeared(
    catalog: BackupCatalog, project_id: str, root: Path
) -> None:
    _assert_forgotten_environment_state(catalog, project_id, "removed")
    assert (root / ".venv").is_symlink()


def _assert_forgotten_environment_state(
    catalog: BackupCatalog, project_id: str, expected_state: str
) -> None:
    row = catalog.get_project(project_id)
    assert row is not None and row["registration_state"] == "forgotten"
    environment = catalog.get_environment("environment-after-purge")
    assert environment is not None and str(environment["state"]) == expected_state


def _assert_public_purge_refuses_environment_state(
    tmp_path: Path,
    arrange: Callable[[BackupCatalog, str, Path], None],
    assert_preserved: Callable[[BackupCatalog, str, Path], None],
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    arrange(catalog, project_id, root)

    client = SimpleNamespace(get_catalog=lambda: catalog)
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )

    catalogue_changes_before_run = catalog._conn.total_changes
    assert command.plan.steps
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert_preserved(catalog, project_id, root)
    assert catalog._conn.total_changes == catalogue_changes_before_run
    _assert_no_purge_completion_marker(catalog)
    catalog.close()


def test_public_purge_keeps_registered_empty_project_non_inert(tmp_path: Path) -> None:
    _assert_public_purge_refuses_environment_state(
        tmp_path, _prepare_registered_empty, _assert_registered_empty
    )


def test_public_purge_keeps_current_active_environment_non_inert(tmp_path: Path) -> None:
    _assert_public_purge_refuses_environment_state(
        tmp_path, _prepare_current_environment_active, _assert_current_environment_active
    )


def test_public_purge_keeps_legacy_active_environment_non_inert(tmp_path: Path) -> None:
    _assert_public_purge_refuses_environment_state(
        tmp_path, _prepare_legacy_environment_active, _assert_legacy_environment_active
    )


def test_public_purge_keeps_reappeared_current_worktree_non_inert(tmp_path: Path) -> None:
    _assert_public_purge_refuses_environment_state(
        tmp_path, _prepare_current_worktree_reappeared, _assert_current_worktree_reappeared
    )


def test_public_purge_keeps_reappeared_legacy_owned_python_non_inert(tmp_path: Path) -> None:
    _assert_public_purge_refuses_environment_state(
        tmp_path,
        _prepare_legacy_python_symlink_reappeared,
        _assert_legacy_python_symlink_reappeared,
    )


def _assert_public_purge_refuses_forgotten_owned_fact(
    catalog: BackupCatalog,
    project_id: str,
    root: Path,
    preserved: Callable[[], None],
) -> None:
    client = SimpleNamespace(get_catalog=lambda: catalog)
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    catalogue_changes_before_run = catalog._conn.total_changes
    with pytest.raises(ProjectRemovalError) as error:
        command.run()
    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["completed_total"] == 0
    assert catalog._conn.total_changes == catalogue_changes_before_run
    row = catalog.get_project(project_id)
    assert row is not None and row["registration_state"] == "forgotten"
    preserved()


def test_public_purge_does_not_treat_surviving_database_binding_as_inert(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    assert catalog.forget_project(project_id)
    catalog._conn.execute(
        "INSERT INTO project_database_bindings "
        "(project_id, cluster_id, db_host, db_port, database_name, bound_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, "cluster-id", "127.0.0.1", 5432, "owned-db", "2026-01-01T00:00:00Z"),
    )
    catalog._conn.commit()

    _assert_public_purge_refuses_forgotten_owned_fact(
        catalog,
        project_id,
        root,
        lambda: _assert_database_binding_preserved(catalog, project_id),
    )
    catalog.close()


def _assert_database_binding_preserved(catalog: BackupCatalog, project_id: str) -> None:
    assert (
        catalog._conn.execute(
            "SELECT database_name FROM project_database_bindings WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        == "owned-db"
    )


def _prepare_removed_owned_python_environment(
    catalog: BackupCatalog, project_id: str, root: Path, path: Path
) -> None:
    assert catalog.forget_project(project_id)
    _legacy_environment(catalog, "environment-with-owned-python", root)
    catalog._conn.execute(
        "UPDATE environments SET project_id = ?, state = 'removed', "
        "python_environment_path = ?, python_environment_owned = 1 WHERE id = ?",
        (project_id, str(path), "environment-with-owned-python"),
    )
    catalog._conn.commit()


def _assert_owned_python_environment_preserved(
    catalog: BackupCatalog, project_id: str, path: Path
) -> None:
    project = catalog.get_project(project_id)
    assert project is not None and project["registration_state"] == "forgotten"
    row = catalog.get_environment("environment-with-owned-python")
    assert row is not None and row["state"] == "removed"
    assert Path(str(row["python_environment_path"])) == path
    assert bool(row["python_environment_owned"])
    assert path.exists() or path.is_symlink()


def test_public_purge_does_not_treat_owned_python_directory_as_inert(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    python_path = tmp_path / "unrelated-owned-python"
    python_path.mkdir()
    _prepare_removed_owned_python_environment(catalog, project_id, root, python_path)

    _assert_public_purge_refuses_forgotten_owned_fact(
        catalog,
        project_id,
        root,
        lambda: _assert_owned_python_environment_preserved(catalog, project_id, python_path),
    )
    catalog.close()


def test_public_purge_does_not_treat_owned_python_symlink_as_inert(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    python_path = tmp_path / "unrelated-owned-python"
    python_path.symlink_to(tmp_path / "missing-python", target_is_directory=True)
    _prepare_removed_owned_python_environment(catalog, project_id, root, python_path)

    _assert_public_purge_refuses_forgotten_owned_fact(
        catalog,
        project_id,
        root,
        lambda: _assert_owned_python_environment_preserved(catalog, project_id, python_path),
    )
    catalog.close()


def _isolated_publication_settings(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[PublicationSettings, Path]:
    config_root = root / "config"
    config_root.mkdir(mode=0o700, parents=True)
    routes_path = config_root / "routes.caddy"
    settings_path = config_root / "publication.toml"
    monkeypatch.setattr(
        publication_resource, "get_config_root", lambda **_kwargs: config_root
    )
    monkeypatch.setattr(
        publication_resource,
        "get_publication_config_path",
        lambda **_kwargs: settings_path,
    )
    monkeypatch.setattr(
        publication_routes,
        "get_publication_config_path",
        lambda **_kwargs: settings_path,
    )
    settings = PublicationSettings(
        domain_suffix="example.test",
        caddy_executable="caddy",
        caddy_control_endpoint="http://127.0.0.1:2019",
        owned_route_file=routes_path,
        basic_auth_username="odoo",
        basic_auth_password_hash=("$2a$10$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    )
    settings_path.write_text(
        "[publication]\n"
        'domain_suffix = "example.test"\n'
        'caddy_executable = "caddy"\n'
        'caddy_control_endpoint = "http://127.0.0.1:2019"\n'
        f"owned_route_file = {json.dumps(str(routes_path))}\n"
        'basic_auth_username = "odoo"\n'
        'basic_auth_password_hash = "$2a$10$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n',
        encoding="utf-8",
    )
    settings_path.chmod(0o600)
    return settings, routes_path


def _project_route(project_id: str) -> publication_routes.PublicationRoute:
    return publication_routes.PublicationRoute(
        owner_kind="project",
        owner_id=project_id,
        project_id=project_id,
        environment_id=None,
        local_endpoint="http://127.0.0.1:8069",
        external_url="https://project.example.test",
        route_identity=publication_routes.route_identity(
            "project", project_id, "https://project.example.test"
        ),
    )


def _write_owned_routes(
    settings: PublicationSettings,
    routes_path: Path,
    routes: tuple[publication_routes.PublicationRoute, ...],
) -> None:
    routes_path.write_bytes(publication_routes._render_routes(routes, settings))
    routes_path.chmod(0o600)


def test_public_purge_refuses_owned_route_present_before_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    assert catalog.forget_project(project_id)
    settings, routes_path = _isolated_publication_settings(
        tmp_path / "isolated-user-root", monkeypatch
    )
    route = _project_route(project_id)
    _write_owned_routes(settings, routes_path, (route,))
    previous_routes = routes_path.read_bytes()
    catalogue_changes_before_capture = catalog._conn.total_changes
    client = SimpleNamespace(get_catalog=lambda: catalog)

    with pytest.raises(ProjectRemovalError, match="publication reappeared"):
        ProjectResource(_client=cast("OdooClient", client)).remove_command(
            ProjectSelector.from_project_id(project_id),
            purge=True,
            confirmation=f"{root.name}:{project_id}",
        )

    assert catalog._conn.total_changes == catalogue_changes_before_capture
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    assert routes_path.read_bytes() == previous_routes
    catalog.close()


def test_public_purge_refuses_owned_route_reappearing_after_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    assert catalog.forget_project(project_id)
    settings, routes_path = _isolated_publication_settings(
        tmp_path / "isolated-user-root", monkeypatch
    )
    _write_owned_routes(settings, routes_path, ())
    client = SimpleNamespace(get_catalog=lambda: catalog)
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    route = _project_route(project_id)
    _write_owned_routes(settings, routes_path, (route,))
    previous_routes = routes_path.read_bytes()
    catalogue_changes_before_run = catalog._conn.total_changes

    with pytest.raises(ProjectRemovalError, match="state changed before retry"):
        command.run()

    assert catalog._conn.total_changes == catalogue_changes_before_run
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    assert routes_path.read_bytes() == previous_routes
    catalog.close()


def test_public_forget_preserves_dirty_source_decision_across_execution(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    subprocess.run(("git", "init", str(root)), check=True, capture_output=True, text=True)
    odcli = root / ".odcli"
    odcli.mkdir()
    (odcli / "project.toml").write_text(
        '[project]\ndefault_source_database = "binta_db"\n', encoding="utf-8"
    )
    (odcli / "odoo.conf").write_text("[options]\n", encoding="utf-8")
    (odcli / ".env").write_text("BINTA_DIAGNOSTIC=redacted\n", encoding="utf-8")
    (odcli / "filestore").mkdir()
    untracked = root / "untracked-addon.py"
    untracked.write_text("# preserved\n", encoding="utf-8")
    client = SimpleNamespace(get_catalog=lambda: catalog)

    result = ProjectResource(_client=cast("OdooClient", client)).remove(
        ProjectSelector.from_project_id(project_id)
    )

    assert result.registration_state == "forgotten"
    assert untracked.exists()
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


def _git_probe_project_with_fsmonitor_canary(
    tmp_path: Path,
    *,
    include_manifest: bool = False,
) -> tuple[BackupCatalog, str, Path, Path, Path]:
    catalog, project_id, root = _catalog(tmp_path)
    subprocess.run(("git", "init", str(root)), check=True, capture_output=True, text=True)
    tracked = root / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    paths_to_add = ["tracked.txt"]
    if include_manifest:
        gitignore = root / ".gitignore"
        gitignore.write_text(".odcli/\n", encoding="utf-8")
        manifest = root / ".odcli" / "project.toml"
        manifest.parent.mkdir()
        manifest.write_text("[project]\n", encoding="utf-8")
        paths_to_add.append(".gitignore")
    subprocess.run(("git", "-C", str(root), "add", *paths_to_add), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "baseline",
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    marker = tmp_path / "fsmonitor-marker"
    canary = tmp_path / "fsmonitor-canary.py"
    canary.write_text(
        "from pathlib import Path\n"
        f"Path({json.dumps(str(marker))}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    canary_command = (
        subprocess.list2cmdline((sys.executable, str(canary)))
        if os.name == "nt"
        else " ".join(shlex.quote(part) for part in (sys.executable, str(canary)))
    )
    subprocess.run(
        ("git", "-C", str(root), "config", "core.fsmonitor", canary_command),
        check=True,
    )
    return catalog, project_id, root, marker, root / ".git" / "index"


def test_public_forget_git_probe_does_not_run_configured_fsmonitor(
    tmp_path: Path,
) -> None:
    catalog, project_id, root, marker, _index = _git_probe_project_with_fsmonitor_canary(tmp_path)
    client = SimpleNamespace(get_catalog=lambda: catalog)

    result = ProjectResource(_client=cast("OdooClient", client)).remove(
        ProjectSelector.from_project_id(project_id)
    )

    assert result.registration_state == "forgotten"
    assert not marker.exists()
    assert root.exists()
    catalog.close()


def test_public_forget_git_probe_preserves_index_bytes(
    tmp_path: Path,
) -> None:
    catalog, project_id, root, _marker, index = _git_probe_project_with_fsmonitor_canary(tmp_path)
    index_before = index.read_bytes()
    client = SimpleNamespace(get_catalog=lambda: catalog)

    result = ProjectResource(_client=cast("OdooClient", client)).remove(
        ProjectSelector.from_project_id(project_id)
    )

    assert result.registration_state == "forgotten"
    assert index.read_bytes() == index_before
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


@pytest.mark.parametrize(
    "confirmation",
    [pytest.param(None, id="missing"), pytest.param("wrong-token", id="wrong")],
)
def test_public_purge_confirmation_does_not_run_fsmonitor_or_mutate_index(
    tmp_path: Path,
    confirmation: str | None,
) -> None:
    catalog, project_id, root, marker, index = _git_probe_project_with_fsmonitor_canary(
        tmp_path,
        include_manifest=True,
    )
    index_before = index.read_bytes()
    client = SimpleNamespace(
        get_catalog=lambda: catalog,
        instance=SimpleNamespace(from_project=lambda _project: None),
    )
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=confirmation,
    )

    with pytest.raises(ProjectRemovalError, match="confirmation"):
        command.run()

    assert not marker.exists()
    assert index.read_bytes() == index_before
    assert (root / ".odcli" / "project.toml").exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_purge_hardened_source_probe_precedes_terminal_forget(
    tmp_path: Path,
) -> None:
    catalog, project_id, root, marker, _index = _git_probe_project_with_fsmonitor_canary(
        tmp_path,
        include_manifest=True,
    )
    foreign = tmp_path / "foreign-resource"
    foreign.write_text("keep\n", encoding="utf-8")
    client = SimpleNamespace(
        get_catalog=lambda: catalog,
        instance=SimpleNamespace(from_project=lambda _project: None),
    )
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )

    result = command.run()

    assert result.completed[-1].action == "forget"
    assert result.registration_state == "forgotten"
    assert not root.exists()
    assert foreign.exists()
    assert not marker.exists()
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


class _AdapterRegistry:
    def __init__(self, adapters: Mapping[str, ProjectRemovalAdapter]) -> None:
        self.adapters = dict(adapters)

    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None:
        return self.adapters.get(decision.identity)


def _process_child(
    executor: RecordingExecutor,
    identity: str,
) -> Command[JsonValue]:
    step = PreparedStep(
        step_id=f"project-removal.database.guarded-drop:{identity}",
        argv=("psql", "--guarded-project-drop"),
        mutating=True,
    )

    def run(context: RunContext[JsonValue]) -> JsonValue:
        context.process(step.step_id)
        return None

    return Command.create(
        ExecutionPlan(steps=(step.public_projection(),)),
        run,
        (step,),
        executor=executor,
    )


def _assert_partial_purge_failure_details(
    catalog: BackupCatalog,
    project_id: str,
    completed_identity: str,
    failed_identity: str,
    error: pytest.ExceptionInfo[ProjectRemovalError],
) -> dict[str, object]:
    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["registration_state"] == "registered"
    assert details["completed_total"] == 1
    assert details["remaining_total"] == 2
    assert details["failed_identity"] == failed_identity
    assert details["completed"][0]["identity"] == completed_identity  # type: ignore[index]
    assert details["remaining"][0]["identity"] == failed_identity  # type: ignore[index]
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    _assert_no_purge_completion_marker(catalog)
    return details


def test_partial_purge_failure_captured_process_is_publicly_truthful_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A captured process can fail once and retry from the public ledger."""
    catalog, project_id, root = _catalog(tmp_path)
    completed_identity = "database:completed"
    failed_identity = "database:failed"
    executor = RecordingExecutor()
    failed = True

    def result_factory(step: object) -> ProcessResult:
        nonlocal failed
        prepared = cast("PreparedStep", step)
        if failed and prepared.step_id.endswith(failed_identity):
            failed = False
            raise RuntimeError("guarded process failed after capture")
        return ProcessResult(
            argv=prepared.argv,
            returncode=0,
            stdout="",
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    executor.result_factory = result_factory
    database_targets = {
        identity: DatabasePurgeTarget(
            identity=identity,
            project_id=project_id,
            project_root=root,
            database_name=identity.rsplit(":", 1)[-1],
        )
        for identity in (completed_identity, failed_identity)
    }
    adapters = {
        completed_identity: DatabasePurgeAdapter(
            cast("OdooClient", object()),
            database_targets,
            command_factory=lambda target: _process_child(executor, target.identity),
        ),
        failed_identity: DatabasePurgeAdapter(
            cast("OdooClient", object()),
            database_targets,
            command_factory=lambda target: _process_child(executor, target.identity),
        ),
    }
    projection = _projection(
        project_id,
        root,
        ((completed_identity, "database"), (failed_identity, "database")),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_coordinator._projection",
        lambda _catalog, _registration, **_kwargs: projection,
    )
    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_project_id(project_id),
        purge=True,
        adapters=_AdapterRegistry(adapters),
    )
    set_project_confirmation(command, expected_project_confirmation_for_command(command))

    with pytest.raises(ProjectRemovalError, match="project removal step failed") as error:
        command.run()
    _assert_partial_purge_failure_details(
        catalog, project_id, completed_identity, failed_identity, error
    )
    assert [step.step_id for step in executor.executed] == [
        f"project-removal.database.guarded-drop:{completed_identity}",
        f"project-removal.database.guarded-drop:{failed_identity}",
    ]

    result = command.run()
    assert result.registration_state == "forgotten"
    assert tuple(item.identity for item in result.completed[:2]) == (
        completed_identity,
        failed_identity,
    )
    assert result.completed[-1].action == "forget"
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    _assert_no_purge_completion_marker(catalog)
    catalog.close()


def test_partial_purge_failure_in_process_action_is_publicly_truthful_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guarded in-process action can fail once and retry from the ledger."""
    catalog, project_id, root = _catalog(tmp_path)
    completed_identity = "filesystem:completed"
    failed_identity = "filesystem:failed"
    completed_resource = root / ".odcli" / "project.toml"
    completed_resource.parent.mkdir(parents=True)
    completed_resource.write_text("owned config\n", encoding="utf-8")
    failed_resource = root / ".odcli" / "logs"
    failed_resource.mkdir()
    filesystem_targets = {
        completed_identity: FilesystemPurgeTarget(
            identity=completed_identity,
            project_id=project_id,
            path=completed_resource,
            allowed_root=root,
        ),
        failed_identity: FilesystemPurgeTarget(
            identity=failed_identity,
            project_id=project_id,
            path=failed_resource,
            allowed_root=root,
        ),
    }
    filesystem_adapter = FilesystemPurgeAdapter(tuple(filesystem_targets.values()))
    projection = _projection(
        project_id,
        root,
        ((completed_identity, "config"), (failed_identity, "config")),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_coordinator._projection",
        lambda _catalog, _registration, **_kwargs: projection,
    )
    adapters = {
        completed_identity: filesystem_adapter,
        failed_identity: filesystem_adapter,
    }
    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_project_id(project_id),
        purge=True,
        adapters=_AdapterRegistry(adapters),
    )
    set_project_confirmation(command, expected_project_confirmation_for_command(command))

    def fail_guard(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("guarded in-process action failed")

    with monkeypatch.context() as guarded_patch:
        guarded_patch.setattr(
            "odoo_instance_sdk.internal.project_filesystem_purge._assert_safe_directory",
            fail_guard,
        )
        with pytest.raises(ProjectRemovalError, match="project removal step failed") as error:
            command.run()
    _assert_partial_purge_failure_details(
        catalog, project_id, completed_identity, failed_identity, error
    )
    assert not completed_resource.exists()
    assert failed_resource.exists()

    result = command.run()
    assert result.registration_state == "forgotten"
    assert tuple(item.identity for item in result.completed[:2]) == (
        completed_identity,
        failed_identity,
    )
    assert result.completed[-1].action == "forget"
    assert not failed_resource.exists()
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    _assert_no_purge_completion_marker(catalog)
    catalog.close()


def test_projection_result_is_bounded_without_changing_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    projection = _projection(
        "project-full-id",
        root,
        (("database:one", "database"), ("backup:one", "backup")),
    )
    retained = InternalRemovalDecision(
        identity="volume:retained",
        resource_type="volume",
        action="retain",
        reason="observation_incomplete",
        target="<local>/volume",
    )
    projection = ProjectRemovalProjection(
        project_id=projection.project_id,
        registration_state=projection.registration_state,
        repository_root=projection.repository_root,
        expected_confirmation=projection.expected_confirmation,
        decisions=(*projection.decisions, retained),
        fingerprint=projection.fingerprint,
    )

    assert len(projection.public_decisions) == 3
    assert tuple(item.identity for item in projection.retained) == ("volume:retained",)
    assert len(projection.bounded_decisions(2)) == 2
    result = projection.result(registration_state="registered", limit=2)
    assert result.registration_state == "registered"
    assert tuple(item.identity for item in result.remaining) == ("database:one", "backup:one")
    assert result.retained == ()
    with pytest.raises(ValueError, match="decision limit"):
        projection.bounded_decisions(0)


def test_projection_fails_closed_for_unobserved_database_and_compose_volume(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest_dir = root / ".odcli"
    manifest_dir.mkdir()
    manifest = manifest_dir / "project.toml"
    manifest.write_text('[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8")
    legacy_project_id = repo_key(root, root / ".git")
    compose_project = f"odcli_pg_{legacy_project_id}"
    volume_name = f"pgdata_{legacy_project_id}"
    claim = catalog._ensure_postgres_cluster_pending(
        legacy_project_id, compose_project, volume_name
    )
    claim = catalog._activate_postgres_cluster(
        claim.cluster_id, legacy_project_id, compose_project, volume_name
    )
    foreign_root = tmp_path / "foreign"
    (foreign_root / ".git").mkdir(parents=True)
    foreign_project_id = f"project_{repo_key(foreign_root, foreign_root / '.git')}"
    catalog._register_project(foreign_project_id, foreign_root, foreign_root / ".git")
    foreign_legacy_id = repo_key(foreign_root, foreign_root / ".git")
    foreign_claim = catalog._ensure_postgres_cluster_pending(
        foreign_legacy_id,
        f"odcli_pg_{foreign_legacy_id}",
        f"pgdata_{foreign_legacy_id}",
    )
    catalog._activate_postgres_cluster(
        foreign_claim.cluster_id,
        foreign_legacy_id,
        f"odcli_pg_{foreign_legacy_id}",
        f"pgdata_{foreign_legacy_id}",
    )

    projection = coordinator_projection(
        catalog,
        ProjectRegistration(project_id, root, root / ".git"),
    )
    database = next(item for item in projection.decisions if item.resource_type == "database")
    volume = next(item for item in projection.decisions if item.resource_type == "volume")
    assert database.action == "retain"
    assert database.reason == "observation_incomplete"
    assert volume.action == "retain"
    assert volume.reason == "observation_incomplete"
    assert all(
        str(foreign_claim.cluster_id) not in decision.identity for decision in projection.decisions
    )
    assert any(
        relationship.startswith("database:")
        for relationship in next(
            item for item in projection.decisions if item.identity == f"project:{project_id}"
        ).relationships
    )
    catalog.close()


@pytest.mark.parametrize("claim_form", ["exact", "legacy"])
def test_catalog_claim_requires_captured_common_dir(tmp_path: Path, claim_form: str) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    legacy_project_id = repo_key(root, root / ".git")
    claim_project_id = project_id if claim_form == "exact" else legacy_project_id
    claim = catalog._ensure_postgres_cluster_pending(
        claim_project_id,
        f"odcli_pg_{claim_project_id}",
        f"pgdata_{claim_project_id}",
    )
    catalog._activate_postgres_cluster(
        claim.cluster_id,
        claim_project_id,
        f"odcli_pg_{claim_project_id}",
        f"pgdata_{claim_project_id}",
    )

    assert catalog._get_postgres_cluster_for_project(project_id, root, None) is None
    catalog.close()


def test_projection_recovers_owned_claim_after_source_root_is_removed(tmp_path: Path) -> None:
    root = tmp_path / "source"
    common = tmp_path / "shared-git"
    root.mkdir()
    common.mkdir()
    project_id = f"project_{repo_key(root, common)}"
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    catalog._register_project(project_id, root, common)
    claim = catalog._ensure_postgres_cluster_pending(project_id, "odcli_pg_owned", "pgdata_owned")
    catalog._activate_postgres_cluster(
        claim.cluster_id, project_id, "odcli_pg_owned", "pgdata_owned"
    )
    root.rmdir()
    projection = coordinator_projection(
        catalog,
        ProjectRegistration(project_id, root, common),
        database_inventory=DatabaseInventoryResult(
            cluster="127.0.0.1:5432",
            databases=(
                DatabaseInventoryItem(
                    cluster="127.0.0.1:5432",
                    cluster_id=str(claim.cluster_id),
                    name="owned_db",
                    logical_size_bytes=1,
                    active_sessions=0,
                    is_default=True,
                ),
            ),
            tracked=False,
        ),
        volume_observation=VolumeResourceSource(
            project_id=project_id,
            volume_name="pgdata_owned",
            cluster_id=str(claim.cluster_id),
            owned=True,
            available=True,
            usage_bytes=1,
            active=True,
            container_identity_verified=True,
        ),
        git_status_hardened=False,
    )

    assert projection.complete
    assert (
        next(item for item in projection.decisions if item.resource_type == "database").action
        == "delete"
    )
    assert (
        next(item for item in projection.decisions if item.resource_type == "volume").action
        == "delete"
    )
    catalog.close()


def test_public_purge_retains_registration_when_missing_source_recovery_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    claim = catalog._ensure_postgres_cluster_pending(
        project_id, f"odcli_pg_{project_id}", f"pgdata_{project_id}"
    )
    catalog._activate_postgres_cluster(
        claim.cluster_id, project_id, claim.compose_project, claim.volume_name
    )
    catalog._record_database_bootstrapped(
        "127.0.0.1",
        5432,
        "tmp",
        cluster_id=claim.cluster_id,
        data_directory=tmp_path / "data",
    )
    shutil.rmtree(root)
    monkeypatch.setattr(
        PostgresCluster,
        "_from_config",
        classmethod(lambda cls, *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable"))),
    )
    client = SimpleNamespace(
        get_catalog=lambda: catalog,
        instance=SimpleNamespace(from_project=lambda _project: None),
    )
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )

    with pytest.raises(ProjectRemovalError, match="unavailable or unproven"):
        command.run()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def _capture_missing_source_purge(  # noqa: C901
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    claim_project_id: str,
    database_names: tuple[str, ...],
    seed_catalog: Callable[[BackupCatalog, str, object, Path], None] = lambda *_args: None,
    publish_observed: Callable[[BackupCatalog, object | None, Path], object | None] | None = None,
    prepare_catalog: Callable[[BackupCatalog, str, object, Path], None] | None = None,
    real_children: bool = False,
    observed_oids: Mapping[str, int] | None = None,
    recovery_password: str | None = None,
) -> tuple[BackupCatalog, Command[ProjectRemovalResult], RecordingExecutor, str]:
    root = tmp_path / "source"
    common = root / ".git"
    common.mkdir(parents=True)
    project_id = f"project_{repo_key(root, common)}"
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    catalog._register_project(project_id, root, common)
    captured_followup = (
        publish_observed(catalog, None, root) if publish_observed is not None else None
    )
    claim = catalog._ensure_postgres_cluster_pending(
        claim_project_id,
        f"odcli_pg_{claim_project_id}",
        f"pgdata_{claim_project_id}",
    )
    catalog._activate_postgres_cluster(
        claim.cluster_id,
        claim_project_id,
        f"odcli_pg_{claim_project_id}",
        f"pgdata_{claim_project_id}",
    )
    if publish_observed is None:
        catalog._record_database_bootstrapped(
            "127.0.0.1",
            15432,
            "tmp",
            cluster_id=claim.cluster_id,
            data_directory=tmp_path / "data",
        )
    if prepare_catalog is not None:
        prepare_catalog(catalog, claim_project_id, claim, root)
    if publish_observed is None:
        seed_catalog(catalog, claim_project_id, claim, tmp_path / "data")
    else:
        if captured_followup is not None:
            context = MagicMock()
            context.process_prepared.side_effect = lambda step: ProcessResult(
                argv=step.argv,
                returncode=0,
                stdout="tmp\t101\nodoo\t102\npostgres\t1\n",
                stderr="",
                duration=0.0,
                cwd=step.cwd,
                environment=step.environment,
            )
            _execute_bootstrap_followup_phase(
                context, cast("_BootstrapFollowup", captured_followup)
            )
        else:
            publish_observed(catalog, claim, root)
    shutil.rmtree(root)
    assert not root.exists()
    assert not common.exists()
    assert catalog._get_postgres_cluster_for_project(project_id, root, common) is not None
    full_container_id = "b" * 64
    compose_project = f"odcli_pg_{claim_project_id}"
    volume_name = f"pgdata_{claim_project_id}"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_project_postgres_dir",
        lambda project_id: tmp_path / "postgres" / str(project_id),
    )
    fake_cluster = PostgresCluster(
        _repository_root=root,
        _project_id=claim_project_id,
        _mode="compose",
        _endpoint_host="127.0.0.1",
        _endpoint_port=15432,
        _image="pgvector/pgvector:pg16",
        _user="odoo",
        _compose_runner=SimpleNamespace(
            inspect_volume_identity=lambda *_args, **_kwargs: True,
        ),
    )
    if recovery_password is not None:
        fake_cluster.password_file.parent.mkdir(parents=True, exist_ok=True)
        fake_cluster.password_file.write_text(recovery_password, encoding="utf-8")
    monkeypatch.setattr(
        PostgresCluster,
        "resource_snapshot",
        lambda _cluster: SimpleNamespace(
            container=build_container(
                {
                    "Id": full_container_id,
                    "Name": f"/{compose_project}-postgres-1",
                    "Config": {"Labels": {}},
                    "Mounts": [],
                }
            ),
            metrics=SimpleNamespace(volume_usage_bytes=1),
            unavailability_reason=None,
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster._from_config",
        classmethod(lambda cls, *args, **kwargs: fake_cluster),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.inventory.build_database_inventory_command",
        lambda *_args, **_kwargs: SimpleNamespace(
            run=lambda: DatabaseInventoryResult(
                cluster="127.0.0.1:15432",
                databases=tuple(
                    DatabaseInventoryItem(
                        cluster="127.0.0.1:15432",
                        cluster_id=str(claim.cluster_id),
                        name=name,
                        database_oid=(observed_oids or {}).get(name, 101 if name == "tmp" else 102),
                        logical_size_bytes=1,
                        active_sessions=0,
                        is_default=False,
                    )
                    for name in database_names
                ),
                tracked=False,
            )
        ),
    )

    def inspect_volume(*args: object, **kwargs: object) -> bool:
        assert args[1] == volume_name
        assert args[2] == str(claim.cluster_id)
        assert kwargs["project_id"] == claim_project_id
        return True

    def inspect_container(*args: object, **kwargs: object) -> str:
        assert args[1] == f"{compose_project}-postgres-1"
        assert args[2] == str(claim.cluster_id)
        assert kwargs["project_id"] == claim_project_id
        assert kwargs["compose_project"] == compose_project
        assert kwargs["volume_name"] == volume_name
        return full_container_id

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.inspect_volume_identity", inspect_volume
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.inspect_container_identity", inspect_container
    )
    executor = RecordingExecutor()

    def capture_child(
        _adapter: DatabasePurgeAdapter | ComposeClusterPurgeAdapter,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> Command[ProjectResourceDecision] | None:
        del project_id
        return cast("Command[ProjectResourceDecision]", _process_child(executor, decision.identity))

    if real_children:

        def execute_process(
            _executor: SubprocessExecutor, step: PreparedStep, **_kwargs: object
        ) -> ProcessResult:
            step_id = step.step_id
            if recovery_password is not None and step_id.startswith("database.drop"):
                assert dict(step.environment_snapshot).get("PGPASSWORD") == recovery_password
            if "verify-volume" in step_id or "verify-container" in step_id:
                return ProcessResult(
                    argv=step.argv,
                    returncode=1,
                    stdout="",
                    stderr=f"Error: No such {'volume' if 'volume' in step_id else 'container'}",
                    duration=0.0,
                    cwd=step.cwd,
                    environment=step.environment,
                )
            if "inspect-volume" in step_id:
                stdout = json.dumps(
                    {
                        "Name": volume_name,
                        "Labels": {
                            "com.docker.compose.project": compose_project,
                            "io.odoo-instance-sdk.cluster-id": str(claim.cluster_id),
                            "io.odoo-instance-sdk.project-id": claim_project_id,
                        },
                    }
                )
            elif "inspect-container" in step_id:
                stdout = json.dumps(
                    {
                        "Id": full_container_id,
                        "Name": f"/{compose_project}-postgres-1",
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": compose_project,
                                "io.odoo-instance-sdk.cluster-id": str(claim.cluster_id),
                                "io.odoo-instance-sdk.project-id": claim_project_id,
                            }
                        },
                        "Mounts": [{"Type": "volume", "Name": volume_name}],
                    }
                )
            elif step_id.endswith("database.drop.verify"):
                stdout = "t\n"
            elif "database.drop" in step_id and (
                "inspect" in step_id or "revalidate-drop" in step_id
            ):
                database_oid = next(
                    (
                        oid
                        for name, oid in (observed_oids or {}).items()
                        if f":{name}:" in step_id or step_id.endswith(f":{name}")
                    ),
                    101 if ":tmp:" in step_id else 102,
                )
                stdout = json.dumps(
                    {
                        "exists": True,
                        "database_oid": database_oid,
                        "is_template": False,
                        "sessions": [],
                    }
                )
            else:
                stdout = ""
            return ProcessResult(
                argv=step.argv,
                returncode=0,
                stdout=stdout,
                stderr="",
                duration=0.0,
                cwd=step.cwd,
                environment=step.environment,
            )

        monkeypatch.setattr(SubprocessExecutor, "execute", execute_process)
    else:
        monkeypatch.setattr(DatabasePurgeAdapter, "capture", capture_child)
        monkeypatch.setattr(ComposeClusterPurgeAdapter, "capture", capture_child)
    client = cast("OdooClient", SimpleNamespace(get_catalog=lambda: catalog))

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    return catalog, command, executor, project_id


def _seed_exact_restore(
    catalog: BackupCatalog,
    project_id: str,
    claim: object,
    data_directory: Path,
    database_oid: int = 102,
) -> None:
    backup_id = str(uuid.uuid4())
    backup_path = data_directory.parent / "owned.zip"
    backup_path.write_bytes(b"owned")
    catalog.start_download(
        backup_id,
        "http://127.0.0.1:8069",
        "owned_db",
        "zip",
        True,
        backup_path,
        project_id=project_id,
    )
    catalog.success_download(backup_id, backup_path.name, backup_path.stat().st_size, "a" * 64)
    catalog.record_restore(
        "127.0.0.1",
        15432,
        "owned_db",
        backup_id,
        cluster_id=getattr(claim, "cluster_id"),
        data_directory=data_directory,
        database_oid=database_oid,
    )
    catalog._record_project_database_binding(
        project_id,
        getattr(claim, "cluster_id"),
        "127.0.0.1",
        15432,
        "owned_db",
        database_oid=database_oid,
    )


def _publish_observed_init_binding(
    catalog: BackupCatalog, claim: object | None, root: Path
) -> object | None:
    data_directory = root / ".odcli" / "filestore"
    data_directory.mkdir(parents=True)
    source_config = root / "odoo.conf"
    source_database = f"binta_{root.name}"
    source_config.write_text(f"[options]\ndb_name = {source_database}\n", encoding="utf-8")
    config = ProjectConfig(
        repository_root=root,
        default_source_database=source_database,
        source_config=source_config,
        postgres=PostgresProjectConfig(
            mode="compose", image="pgvector/pgvector:pg16", port=15432, user="odoo"
        ),
    )
    _compose, followup = _compose_followup_steps(root, config, dry_run=True, defer_actions=False)
    if claim is None:
        return followup
    context = MagicMock()
    context.process_prepared.side_effect = lambda step: ProcessResult(
        argv=step.argv,
        returncode=0,
        stdout="tmp\t101\nodoo\t102\npostgres\t1\n",
        stderr="",
        duration=0.0,
        cwd=step.cwd,
        environment=step.environment,
    )
    _execute_bootstrap_followup_phase(context, followup)
    return None


def test_public_purge_recovers_missing_source_with_exact_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog, command, executor, project_id = _capture_missing_source_purge(
        tmp_path,
        monkeypatch,
        project_id,
        ("owned_db",),
        _seed_exact_restore,
        recovery_password="test-recovery-password",
    )
    result = command.run()
    assert result.registration_state == "forgotten"
    assert len(executor.executed) == 2
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


def test_public_restore_publishes_oid_for_source_less_purge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Link the public restore publication to the later source-less purge."""
    from odoo_instance_sdk import OdooClient, OdooClientConfig
    from odoo_instance_sdk.config import InstanceConfig
    from tests.fixtures.transport import (
        make_http_client,
        make_response,
        patch_open_odoo_http_client,
    )

    root = tmp_path / "source"
    legacy_project_id = repo_key(root, root / ".git")
    catalog_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: catalog_path
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )

    def restore_with_public_command(
        catalog: BackupCatalog, claim_project_id: str, claim: object, source_root: Path
    ) -> None:
        backup_path = source_root.parent / "owned.zip"
        backup_path.write_bytes(b"owned")
        backup_id = uuid.uuid4()
        catalog.start_download(
            str(backup_id),
            "http://127.0.0.1:8069",
            "source_db",
            BackupFormat.ZIP.value,
            True,
            backup_path,
            project_id=f"project_{repo_key(source_root, source_root / '.git')}",
        )
        catalog.success_download(
            str(backup_id), backup_path.name, backup_path.stat().st_size, "a" * 64
        )
        backup = Backup(
            id=backup_id,
            source_base_url="http://127.0.0.1:8069",
            database_name="source_db",
            format=BackupFormat.ZIP,
            filestore_requested=True,
            path=str(backup_path),
            filename=backup_path.name,
            size_bytes=backup_path.stat().st_size,
            sha256="a" * 64,
            downloaded_at=datetime.now(UTC),
        )
        cluster = PostgresCluster(
            _repository_root=source_root,
            _project_id=claim_project_id,
            _mode="compose",
            _endpoint_host="127.0.0.1",
            _endpoint_port=15432,
            _image="pgvector/pgvector:pg16",
            _user="odoo",
            _compose_runner=SimpleNamespace(),
        )
        client = OdooClient(config=OdooClientConfig(executable="odoo"))
        client._catalog = catalog
        instance = client.instance("http://127.0.0.1:8069", master_password="admin")
        object.__setattr__(
            instance,
            "config",
            InstanceConfig(
                base_url="http://127.0.0.1:8069",
                master_password="admin",
                db_host="127.0.0.1",
                db_port=15432,
                db_user="odoo",
            ),
        )
        object.__setattr__(instance, "_postgres_cluster", cluster)
        executor = RecordingExecutor(
            result_factory=lambda step: ProcessResult(
                argv=(prepared := cast("PreparedStep", step)).argv,
                returncode=0,
                stdout=("19557\n" if prepared.step_id == "database.restore.exists-after" else ""),
                stderr="",
                duration=0.0,
                cwd=prepared.cwd,
                environment=prepared.environment,
            )
        )
        http = make_http_client(post=make_response())
        with patch_open_odoo_http_client(http):
            instance.databases.restore_command(backup, "owned_db", executor=executor).run()
        assert any(step.step_id == "database.restore.exists-after" for step in executor.executed)
        http.post.assert_called_once()

    catalog, command, _executor, project_id = _capture_missing_source_purge(
        tmp_path,
        monkeypatch,
        legacy_project_id,
        ("owned_db",),
        prepare_catalog=restore_with_public_command,
        real_children=True,
        observed_oids={"owned_db": 19557},
        recovery_password="test-recovery-password",
    )
    claim = catalog._get_postgres_cluster(legacy_project_id)
    assert claim is not None
    assert catalog._project_restored_database_records(
        project_id, claim.cluster_id, "127.0.0.1", 15432
    ) == (("owned_db", 19557),)
    assert catalog._project_bound_database_records(
        legacy_project_id, claim.cluster_id, "127.0.0.1", 15432
    ) == (("owned_db", 19557),)
    assert any("database" in step.step_id for step in command.plan.steps)

    result = command.run()

    assert result.registration_state == "forgotten"
    assert tuple(item.resource_type for item in result.completed) == (
        "database",
        "volume",
        "project",
    )
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


def test_public_purge_refuses_same_name_replaced_restored_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    project_id = f"project_{repo_key(root, root / '.git')}"

    def seed_replaced_restore(
        catalog: BackupCatalog, seeded_project_id: str, claim: object, data_directory: Path
    ) -> None:
        _seed_exact_restore(catalog, seeded_project_id, claim, data_directory, database_oid=101)

    catalog, command, executor, project_id = _capture_missing_source_purge(
        tmp_path,
        monkeypatch,
        project_id,
        ("owned_db",),
        seed_replaced_restore,
        observed_oids={"owned_db": 202},
    )
    with pytest.raises(ProjectRemovalError, match="unavailable or unproven"):
        command.run()
    assert executor.executed == []
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_purge_refuses_missing_source_restore_without_oid_before_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    legacy_project_id = repo_key(root, root / ".git")

    def seed_restore_without_oid(
        catalog: BackupCatalog, _project_id: str, claim: object, data_directory: Path
    ) -> None:
        backup_id = str(uuid.uuid4())
        backup_path = data_directory.parent / "owned.zip"
        backup_path.write_bytes(b"owned")
        catalog.start_download(
            backup_id,
            "http://127.0.0.1:8069",
            "owned_db",
            "zip",
            True,
            backup_path,
            project_id=f"project_{legacy_project_id}",
        )
        catalog.success_download(backup_id, backup_path.name, backup_path.stat().st_size, "a" * 64)
        catalog.record_restore(
            "127.0.0.1",
            15432,
            "owned_db",
            backup_id,
            cluster_id=getattr(claim, "cluster_id"),
            data_directory=data_directory,
        )

    catalog, command, executor, project_id = _capture_missing_source_purge(
        tmp_path,
        monkeypatch,
        legacy_project_id,
        ("owned_db",),
        seed_restore_without_oid,
        observed_oids={"owned_db": 19557},
    )
    assert all(step.action == "retain" for step in command.plan.steps if hasattr(step, "action"))
    with pytest.raises(ProjectRemovalError, match="unavailable or unproven"):
        command.run()
    assert executor.executed == []
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_purge_recovers_missing_source_with_legacy_init_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    legacy_project_id = repo_key(root, root / ".git")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda: tmp_path / "catalog.sqlite3",
    )
    monkeypatch.setattr(
        bootstrap,
        "run_bootstrap_tmp",
        lambda _context, _plan: bootstrap.BootstrapOutcome.CREATED,
    )
    catalog, command, _executor, project_id = _capture_missing_source_purge(
        tmp_path,
        monkeypatch,
        legacy_project_id,
        ("tmp", "odoo"),
        publish_observed=_publish_observed_init_binding,
        real_children=True,
        recovery_password="test-recovery-password",
    )
    result = command.run()
    assert result.registration_state == "forgotten"
    assert tuple(item.resource_type for item in result.completed) == (
        "database",
        "database",
        "volume",
        "project",
    )
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


def _assert_legacy_restore_completion(
    command: Command[ProjectRemovalResult],
    _executor: RecordingExecutor,
    _catalog: BackupCatalog,
    _project_id: str,
) -> None:
    assert any("database" in step.step_id for step in command.plan.steps)
    result = command.run()
    assert result.registration_state == "forgotten"
    assert tuple(item.resource_type for item in result.completed) == (
        "database",
        "volume",
        "project",
    )


def _assert_legacy_restore_replacement_refusal(
    command: Command[ProjectRemovalResult],
    executor: RecordingExecutor,
    catalog: BackupCatalog,
    project_id: str,
) -> None:
    with pytest.raises(ProjectRemovalError, match="unavailable or unproven"):
        command.run()
    assert executor.executed == []
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]


@pytest.mark.parametrize(
    ("recorded_oid", "observed_oid", "assert_result"),
    (
        (102, 102, _assert_legacy_restore_completion),
        (101, 202, _assert_legacy_restore_replacement_refusal),
    ),
    ids=("stable-restore", "same-name-replacement"),
)
def test_public_purge_handles_missing_source_with_legacy_restore_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorded_oid: int,
    observed_oid: int,
    assert_result: Callable[
        [Command[ProjectRemovalResult], RecordingExecutor, BackupCatalog, str], None
    ],
) -> None:
    root = tmp_path / "source"
    legacy_project_id = repo_key(root, root / ".git")
    registered_project_id = f"project_{legacy_project_id}"

    def seed_legacy_restore(
        catalog: BackupCatalog, _claim_project_id: str, claim: object, data_directory: Path
    ) -> None:
        backup_id = str(uuid.uuid4())
        backup_path = data_directory.parent / "owned.zip"
        backup_path.write_bytes(b"owned")
        catalog.start_download(
            backup_id,
            "http://127.0.0.1:8069",
            "owned_db",
            "zip",
            True,
            backup_path,
            project_id=registered_project_id,
        )
        catalog.success_download(backup_id, backup_path.name, backup_path.stat().st_size, "a" * 64)
        catalog.record_restore(
            "127.0.0.1",
            15432,
            "owned_db",
            backup_id,
            cluster_id=getattr(claim, "cluster_id"),
            data_directory=data_directory,
            database_oid=recorded_oid,
        )

    catalog, command, executor, project_id = _capture_missing_source_purge(
        tmp_path,
        monkeypatch,
        legacy_project_id,
        ("owned_db",),
        seed_legacy_restore,
        real_children=True,
        observed_oids={"owned_db": observed_oid},
        recovery_password="test-recovery-password",
    )
    assert project_id == registered_project_id
    assert_result(command, executor, catalog, project_id)
    catalog.close()


def test_public_purge_refuses_missing_source_with_foreign_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    legacy_project_id = repo_key(root, root / ".git")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda: tmp_path / "catalog.sqlite3",
    )
    monkeypatch.setattr(
        bootstrap,
        "run_bootstrap_tmp",
        lambda _context, _plan: bootstrap.BootstrapOutcome.CREATED,
    )
    catalog, command, executor, project_id = _capture_missing_source_purge(
        tmp_path,
        monkeypatch,
        legacy_project_id,
        ("tmp", "odoo", "foreign_db"),
        publish_observed=_publish_observed_init_binding,
    )
    with pytest.raises(ProjectRemovalError, match="unavailable or unproven"):
        command.run()
    assert not executor.executed
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


@pytest.mark.parametrize("claim_form", ["exact", "legacy"])
@pytest.mark.parametrize("common_failure", ["captured-missing", "root-missing"])
def test_coordinator_retains_resources_without_git_common_proof(
    tmp_path: Path, claim_form: str, common_failure: str
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest_dir = root / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        '[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8"
    )
    legacy_project_id = repo_key(root, root / ".git")
    claim_project_id = project_id if claim_form == "exact" else legacy_project_id
    claim = catalog._ensure_postgres_cluster_pending(
        claim_project_id,
        f"odcli_pg_{claim_project_id}",
        f"pgdata_{claim_project_id}",
    )
    catalog._activate_postgres_cluster(
        claim.cluster_id,
        claim_project_id,
        f"odcli_pg_{claim_project_id}",
        f"pgdata_{claim_project_id}",
    )
    captured_common = None if common_failure == "captured-missing" else root / ".git"
    if common_failure == "root-missing":
        (root / ".git").rmdir()

    projection = coordinator_projection(
        catalog,
        ProjectRegistration(project_id, root, captured_common),
    )

    database = next(item for item in projection.decisions if item.resource_type == "database")
    assert database.action == "retain"
    assert all(
        not (item.resource_type in {"database", "volume"} and item.action == "delete")
        for item in projection.decisions
    )
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


@pytest.mark.parametrize("claim_form", ["exact", "legacy"])
@pytest.mark.parametrize("common_failure", ["missing", "malformed", "drift"])
def test_public_purge_refuses_invalid_git_common_for_postgres_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    claim_form: str,
    common_failure: str,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest_dir = root / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        '[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8"
    )
    legacy_project_id = repo_key(root, root / ".git")
    claim_project_id = project_id if claim_form == "exact" else legacy_project_id
    claim = catalog._ensure_postgres_cluster_pending(
        claim_project_id,
        f"odcli_pg_{claim_project_id}",
        f"pgdata_{claim_project_id}",
    )
    catalog._activate_postgres_cluster(
        claim.cluster_id,
        claim_project_id,
        f"odcli_pg_{claim_project_id}",
        f"pgdata_{claim_project_id}",
    )
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(
                from_project=lambda _project: SimpleNamespace(
                    databases=SimpleNamespace(
                        list_inventory=lambda *_args, **_kwargs: DatabaseInventoryResult(
                            cluster="127.0.0.1:5432",
                            databases=(
                                DatabaseInventoryItem(
                                    cluster="127.0.0.1:5432",
                                    cluster_id=None,
                                    name="owned_db",
                                    logical_size_bytes=1,
                                    active_sessions=0,
                                    is_default=True,
                                ),
                            ),
                            tracked=True,
                        )
                    ),
                    _postgres_cluster=SimpleNamespace(
                        owned=True,
                        compose_runner=SimpleNamespace(),
                        resource_snapshot=lambda: SimpleNamespace(
                            container=None,
                            metrics=SimpleNamespace(volume_usage_bytes=1),
                            unavailability_reason="stopped",
                        ),
                    ),
                )
            ),
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.inspect_volume_identity",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.inspect_container_identity",
        lambda *_args, **_kwargs: True,
    )
    database_executor = RecordingExecutor()

    def capture_child(
        _adapter: DatabasePurgeAdapter | ComposeClusterPurgeAdapter,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> Command[ProjectResourceDecision] | None:
        del project_id
        return cast(
            "Command[ProjectResourceDecision]",
            _process_child(database_executor, decision.identity),
        )

    if common_failure == "drift":
        monkeypatch.setattr(DatabasePurgeAdapter, "capture", capture_child)
        monkeypatch.setattr(ComposeClusterPurgeAdapter, "capture", capture_child)

    def build_command() -> Command[ProjectRemovalResult]:
        return ProjectResource(_client=client).remove_command(
            ProjectSelector.from_project_id(project_id),
            purge=True,
            confirmation=f"{root.name}:{project_id}",
        )

    if common_failure == "drift":
        command = build_command()
        alternate_common = tmp_path / "alternate-common"
        alternate_common.mkdir()
        (root / ".git").rmdir()
        (root / ".git").write_text(f"gitdir: {alternate_common}\n", encoding="utf-8")
    elif common_failure == "missing":
        (root / ".git").rmdir()
        command = build_command()
    else:
        (root / ".git").rmdir()
        (root / ".git").write_text("not-a-git-marker\n", encoding="utf-8")
        command = build_command()
    with pytest.raises(ProjectRemovalError, match="observation"):
        command.run()

    assert root.exists()
    assert database_executor.executed == []
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert catalog._get_postgres_cluster(claim_project_id) is not None
    catalog.close()


def test_projection_retains_dirty_primary_source_at_capture(tmp_path: Path) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    projection = coordinator_projection(
        catalog,
        ProjectRegistration(project_id, root, root / ".git"),
    )
    source = next(item for item in projection.decisions if item.resource_type == "source")
    assert source.action == "retain"
    assert source.reason == "source_git_provenance_unmatched"
    catalog.close()


def _observed_database_and_compose_removal_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verification_error: str | None,
    *,
    absence_observed: threading.Event | None = None,
    continue_after_absence: threading.Event | None = None,
    container_absent: bool = False,
) -> tuple[
    BackupCatalog,
    str,
    Path,
    Command[ProjectRemovalResult],
    str,
    tuple[str, ...],
    RecordingExecutor,
    RecordingExecutor,
    str,
    dict[str, BackupCatalog],
    OdooClient,
]:
    catalog, project_id, root = _catalog(tmp_path)
    subprocess.run(("git", "init", str(root)), check=True, capture_output=True)
    manifest_dir = root / ".odcli"
    manifest_dir.mkdir()
    manifest = manifest_dir / "project.toml"
    manifest.write_text('[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8")
    (root / ".gitignore").write_text(".odcli/\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(root), "add", ".gitignore"), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "baseline",
        ),
        check=True,
        capture_output=True,
    )
    legacy_project_id = repo_key(root, root / ".git")
    compose_project = f"odcli_pg_{legacy_project_id}"
    volume_name = f"pgdata_{legacy_project_id}"
    claim = catalog._ensure_postgres_cluster_pending(
        legacy_project_id, compose_project, volume_name
    )
    claim = catalog._activate_postgres_cluster(
        claim.cluster_id, legacy_project_id, compose_project, volume_name
    )
    full_container_id = "a" * 64
    inspect_entry = cast(
        "dict[str, JsonValue]",
        {
            "Name": f"/{compose_project}-postgres-1",
            "Id": full_container_id,
            "Config": {
                "Labels": {
                    "com.docker.compose.project": compose_project,
                    "io.odoo-instance-sdk.cluster-id": str(claim.cluster_id),
                    "io.odoo-instance-sdk.project-id": legacy_project_id,
                }
            },
            "Mounts": [{"Type": "volume", "Name": volume_name}],
        },
    )
    database_inventory = DatabaseInventoryResult(
        cluster="127.0.0.1:5432",
        databases=(
            DatabaseInventoryItem(
                cluster="127.0.0.1:5432",
                cluster_id=None,
                name="owned_db",
                logical_size_bytes=128,
                active_sessions=0,
                is_default=True,
            ),
        ),
        tracked=True,
    )
    cluster = SimpleNamespace(
        owned=True,
        compose_runner=SimpleNamespace(
            run=lambda *_args, **_kwargs: subprocess.CompletedProcess(
                args=["docker"],
                returncode=0,
                stdout=json.dumps(inspect_entry),
                stderr="",
            )
        ),
        resource_snapshot=lambda: SimpleNamespace(
            container=build_container(inspect_entry),
            metrics=SimpleNamespace(volume_usage_bytes=1024),
            unavailability_reason=None,
        ),
    )
    instance = SimpleNamespace(
        databases=SimpleNamespace(list_inventory=lambda *_args, **_kwargs: database_inventory),
        _postgres_cluster=cluster,
    )
    catalog_holder = {"catalog": catalog}
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog_holder["catalog"],
            instance=SimpleNamespace(from_project=lambda _project: instance),
        ),
    )
    database_executor = RecordingExecutor()
    compose_executor = RecordingExecutor()
    real_database_capture = DatabasePurgeAdapter.capture
    real_compose_capture = ComposeClusterPurgeAdapter.capture

    def capture_database(
        _adapter: DatabasePurgeAdapter,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> Command[ProjectResourceDecision] | None:
        _adapter._command_factory = lambda target: _process_child(
            database_executor, target.identity
        )
        return real_database_capture(_adapter, decision, project_id=project_id)

    def compose_result_factory(step: PreparedProcess) -> ProcessResult:
        step = cast("PreparedStep", step)
        if step.step_id.endswith("inspect-volume"):
            stdout = json.dumps(
                {
                    "Name": volume_name,
                    "Labels": {
                        "com.docker.compose.project": compose_project,
                        "io.odoo-instance-sdk.cluster-id": str(claim.cluster_id),
                        "io.odoo-instance-sdk.project-id": legacy_project_id,
                    },
                }
            )
        elif step.step_id.endswith("inspect-container"):
            if container_absent:
                return ProcessResult(
                    argv=step.argv,
                    returncode=1,
                    stdout="",
                    stderr="No such container",
                    duration=0.0,
                    cwd=step.cwd,
                    environment=step.environment,
                )
            stdout = json.dumps(
                {
                    "Name": f"/{compose_project}-postgres-1",
                    "Id": full_container_id,
                    "Config": {
                        "Labels": {
                            "com.docker.compose.project": compose_project,
                            "io.odoo-instance-sdk.cluster-id": str(claim.cluster_id),
                            "io.odoo-instance-sdk.project-id": legacy_project_id,
                        }
                    },
                    "Mounts": [{"Type": "volume", "Name": volume_name}],
                }
            )
        elif step.step_id.endswith("verify-volume") or step.step_id.endswith("verify-container"):
            stdout = ""
            if (
                step.step_id.endswith("verify-container")
                and verification_error is None
                and absence_observed is not None
            ):
                absence_observed.set()
                if continue_after_absence is not None and not continue_after_absence.wait(
                    timeout=10
                ):
                    raise TimeoutError("purge test was not released after absence proof")
            return ProcessResult(
                argv=step.argv,
                returncode=1,
                stdout=stdout,
                stderr=verification_error or "No such object",
                duration=0.0,
                cwd=step.cwd,
                environment=step.environment,
            )
        else:
            stdout = ""
        return ProcessResult(
            argv=step.argv,
            returncode=0,
            stdout=stdout,
            stderr="",
            duration=0.0,
            cwd=step.cwd,
            environment=step.environment,
        )

    compose_executor.result_factory = compose_result_factory

    def capture_compose(
        _adapter: ComposeClusterPurgeAdapter,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> Command[ProjectResourceDecision] | None:
        _adapter._executor = compose_executor
        return real_compose_capture(_adapter, decision, project_id=project_id)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.inspect_volume_identity",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(DatabasePurgeAdapter, "capture", capture_database)
    monkeypatch.setattr(ComposeClusterPurgeAdapter, "capture", capture_compose)

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    captured_fingerprint = command.plan.fingerprint
    assert captured_fingerprint
    plan_step_ids = tuple(step.step_id for step in command.plan.steps)
    assert any("database" in step_id and "guarded-drop" in step_id for step_id in plan_step_ids)
    assert any(step_id.endswith(":destroy") for step_id in plan_step_ids)
    return (
        catalog,
        project_id,
        root,
        command,
        captured_fingerprint,
        plan_step_ids,
        database_executor,
        compose_executor,
        legacy_project_id,
        catalog_holder,
        client,
    )


def test_public_remove_command_executes_observed_database_and_compose_before_forget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        catalog,
        project_id,
        root,
        command,
        captured_fingerprint,
        plan_step_ids,
        database_executor,
        compose_executor,
        legacy_project_id,
        catalog_holder,
        client,
    ) = _observed_database_and_compose_removal_case(tmp_path, monkeypatch, verification_error=None)
    assert captured_fingerprint
    assert any("database" in step_id and "guarded-drop" in step_id for step_id in plan_step_ids)
    assert any(step_id.endswith(":destroy") for step_id in plan_step_ids)
    result = command.run()

    assert command.plan.fingerprint == captured_fingerprint
    assert [step.step_id for step in database_executor.executed] == [
        "project-removal.database.guarded-drop:database:owned_db"
    ]
    assert [step.step_id.rsplit(":", 1)[-1] for step in compose_executor.executed] == [
        "inspect-volume",
        "inspect-container",
        "stop-container",
        "remove-container",
        "remove-volume",
        "verify-volume",
        "verify-container",
    ]
    assert tuple(
        item.resource_type
        for item in result.completed
        if item.resource_type in {"database", "volume"}
    ) == ("database", "volume")
    assert result.completed[-1].action == "forget"
    assert not root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    assert catalog._get_postgres_cluster(legacy_project_id) is None
    catalog_path = catalog.db_path
    catalog.close()

    catalog = BackupCatalog(db_path=catalog_path)
    catalog_holder["catalog"] = catalog
    persisted = catalog.get_project_for_lifecycle(project_id)
    assert persisted is not None
    assert persisted["registration_state"] == "forgotten"
    assert catalog._get_postgres_cluster(legacy_project_id) is None
    repeat_command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    assert all("volume:" not in step.step_id for step in repeat_command.plan.steps)
    assert all("compose:" not in step.step_id for step in repeat_command.plan.steps)
    changes_before_repeat = catalog._conn.total_changes
    repeated = repeat_command.run()
    assert repeated.registration_state == "forgotten"
    assert catalog._conn.total_changes == changes_before_repeat
    persisted_after_repeat = catalog.get_project_for_lifecycle(project_id)
    assert persisted_after_repeat is not None
    assert persisted_after_repeat["registration_state"] == "forgotten"
    catalog.close()


def test_public_remove_command_refuses_unavailable_compose_postcondition_before_forget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        catalog,
        project_id,
        root,
        command,
        _captured_fingerprint,
        _plan_step_ids,
        database_executor,
        _compose_executor,
        legacy_project_id,
        _catalog_holder,
        _client,
    ) = _observed_database_and_compose_removal_case(
        tmp_path, monkeypatch, verification_error="Cannot connect to the Docker daemon"
    )
    changes_before_failure = catalog._conn.total_changes
    with pytest.raises(ProjectRemovalError, match="project removal step failed"):
        command.run()
    active_claim = catalog._get_postgres_cluster(legacy_project_id)
    assert active_claim is not None
    assert active_claim.state == "active"
    assert active_claim.compose_project == f"odcli_pg_{legacy_project_id}"
    assert active_claim.volume_name == f"pgdata_{legacy_project_id}"
    assert catalog._conn.total_changes == changes_before_failure
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert [step.step_id for step in database_executor.executed] == [
        "project-removal.database.guarded-drop:database:owned_db"
    ]
    assert root.exists()
    catalog.close()


def test_compose_purge_retains_claim_when_cluster_lock_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        catalog,
        project_id,
        root,
        command,
        _fingerprint,
        _step_ids,
        _database_executor,
        compose_executor,
        cluster_project_id,
        _catalog_holder,
        _client,
    ) = _observed_database_and_compose_removal_case(tmp_path, monkeypatch, verification_error=None)
    with (
        exclusive_lock(postgres_cluster_lock_path(cluster_project_id)),
        pytest.raises(ProjectRemovalError, match="project removal step failed"),
    ):
        command.run()

    retained_claim = catalog._get_postgres_cluster(cluster_project_id)
    assert retained_claim is not None
    assert retained_claim.state == "active"
    assert compose_executor.executed == []
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert root.exists()
    catalog.close()


def test_public_remove_command_retires_claim_when_owned_container_is_already_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        catalog,
        project_id,
        root,
        command,
        _fingerprint,
        _step_ids,
        database_executor,
        compose_executor,
        cluster_project_id,
        _catalog_holder,
        _client,
    ) = _observed_database_and_compose_removal_case(
        tmp_path, monkeypatch, verification_error=None, container_absent=True
    )
    result = command.run()

    assert result.registration_state == "forgotten"
    assert [step.step_id.rsplit(":", 1)[-1] for step in compose_executor.executed] == [
        "inspect-volume",
        "inspect-container",
        "remove-volume",
        "verify-volume",
        "verify-container",
    ]
    assert [step.step_id for step in database_executor.executed] == [
        "project-removal.database.guarded-drop:database:owned_db"
    ]
    assert catalog._get_postgres_cluster(cluster_project_id) is None
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    assert not root.exists()
    catalog.close()


def test_compose_purge_holds_cluster_lock_from_absence_proof_through_claim_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    absence_observed = threading.Event()
    continue_after_absence = threading.Event()
    (
        catalog,
        project_id,
        root,
        command,
        _fingerprint,
        _step_ids,
        _database_executor,
        _compose_executor,
        cluster_project_id,
        _catalog_holder,
        _client,
    ) = _observed_database_and_compose_removal_case(
        tmp_path,
        monkeypatch,
        verification_error=None,
        absence_observed=absence_observed,
        continue_after_absence=continue_after_absence,
    )
    reconcile_attempted = threading.Event()
    reconcile_acquired = threading.Event()
    reconcile_denied = threading.Event()

    def reconcile_cluster() -> None:
        assert absence_observed.wait(timeout=10)
        reconcile_attempted.set()
        try:
            with exclusive_lock(postgres_cluster_lock_path(cluster_project_id)):
                reconcile_acquired.set()
        except LockConflictError:
            reconcile_denied.set()
        finally:
            continue_after_absence.set()

    reconcile_thread = threading.Thread(target=reconcile_cluster)
    reconcile_thread.start()
    result = command.run()
    reconcile_thread.join(timeout=5)
    assert not reconcile_thread.is_alive()
    assert result.registration_state == "forgotten"
    assert reconcile_attempted.is_set()
    assert reconcile_denied.is_set()
    assert not reconcile_acquired.is_set()
    assert catalog._get_postgres_cluster(cluster_project_id) is None
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    assert not root.exists()
    catalog.close()


@pytest.mark.parametrize("container_probe", ["mismatch", "malformed", "unavailable"])
def test_public_remove_command_blocks_forget_for_mismatched_snapshot_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, container_probe: str
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest_dir = root / ".odcli"
    manifest_dir.mkdir()
    manifest = manifest_dir / "project.toml"
    manifest.write_text('[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8")
    legacy_project_id = repo_key(root, root / ".git")
    compose_project = f"odcli_pg_{legacy_project_id}"
    volume_name = f"pgdata_{legacy_project_id}"
    claim = catalog._ensure_postgres_cluster_pending(
        legacy_project_id, compose_project, volume_name
    )
    catalog._activate_postgres_cluster(
        claim.cluster_id, legacy_project_id, compose_project, volume_name
    )

    database_inventory = DatabaseInventoryResult(
        cluster="127.0.0.1:5432",
        databases=(
            DatabaseInventoryItem(
                cluster="127.0.0.1:5432",
                cluster_id=None,
                name="owned_db",
                logical_size_bytes=1,
                active_sessions=0,
                is_default=True,
            ),
        ),
        tracked=True,
    )

    expected_container_id = "b" * 64
    foreign_container_id = "c" * 64

    cluster = SimpleNamespace(
        owned=True,
        compose_runner=SimpleNamespace(),
        resource_snapshot=lambda: SimpleNamespace(
            container=build_container({"Id": foreign_container_id}),
            metrics=SimpleNamespace(volume_usage_bytes=1),
            unavailability_reason=None,
        ),
    )
    instance = SimpleNamespace(
        databases=SimpleNamespace(list_inventory=lambda *_args, **_kwargs: database_inventory),
        _postgres_cluster=cluster,
    )
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(from_project=lambda _project: instance),
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.inspect_volume_identity",
        lambda *_args, **_kwargs: True,
    )
    if container_probe == "mismatch":
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.postgres_compose.inspect_container_identity",
            lambda *_args, **_kwargs: expected_container_id,
        )
    elif container_probe == "malformed":
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.postgres_compose.inspect_container_identity",
            lambda *_args, **_kwargs: "not-a-docker-id",
        )
    else:

        def unavailable_container(*_args: object, **_kwargs: object) -> str | None:
            raise OSError("container observation unavailable")

        monkeypatch.setattr(
            "odoo_instance_sdk.internal.postgres_compose.inspect_container_identity",
            unavailable_container,
        )
    database_executor = RecordingExecutor()

    def capture_database(
        _adapter: DatabasePurgeAdapter,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> Command[ProjectResourceDecision] | None:
        del project_id
        return cast(
            "Command[ProjectResourceDecision]",
            _process_child(database_executor, decision.identity),
        )

    monkeypatch.setattr(DatabasePurgeAdapter, "capture", capture_database)

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert database_executor.executed == []
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert manifest.exists()
    assert root.exists()
    catalog.close()


def test_public_remove_command_blocks_forget_when_required_observation_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text('[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8")
    legacy_project_id = repo_key(root, root / ".git")
    compose_project = f"odcli_pg_{legacy_project_id}"
    volume_name = f"pgdata_{legacy_project_id}"
    claim = catalog._ensure_postgres_cluster_pending(
        legacy_project_id, compose_project, volume_name
    )
    claim = catalog._activate_postgres_cluster(
        claim.cluster_id, legacy_project_id, compose_project, volume_name
    )

    def unavailable_inventory(*_args: object, **_kwargs: object) -> DatabaseInventoryResult:
        raise OSError("database observation unavailable")

    cluster = SimpleNamespace(
        owned=True,
        compose_runner=SimpleNamespace(),
        resource_snapshot=lambda: (_ for _ in ()).throw(OSError("Compose observation unavailable")),
    )
    instance = SimpleNamespace(
        databases=SimpleNamespace(list_inventory=unavailable_inventory),
        _postgres_cluster=cluster,
    )
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(from_project=lambda _project: instance),
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.inspect_volume_identity",
        lambda *_args, **_kwargs: False,
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    retained_claim = catalog._get_postgres_cluster(legacy_project_id)
    assert retained_claim == claim
    assert retained_claim is not None
    assert retained_claim.state == "active"
    assert retained_claim.activated_at is not None
    assert manifest.exists()
    assert root.exists()
    catalog.close()


def test_public_remove_command_blocks_malformed_manifest_before_mutation(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text("[project\n", encoding="utf-8")
    before = manifest.read_bytes()
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(),
        ),
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert manifest.read_bytes() == before
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_remove_command_retains_registration_when_instance_probe_fails(
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.exceptions import ConfigError

    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text('[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8")
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(
                from_project=lambda _project: (_ for _ in ()).throw(ConfigError("probe failed"))
            ),
        ),
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    with pytest.raises(ProjectRemovalError, match="observation"):
        command.run()

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert manifest.exists()
    catalog.close()


def test_public_remove_command_retains_registration_when_database_probe_fails(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text('[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8")
    instance = SimpleNamespace(
        databases=SimpleNamespace(
            list_inventory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("database probe failed")
            )
        )
    )
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(from_project=lambda _project: instance),
        ),
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    with pytest.raises(ProjectRemovalError, match="observation"):
        command.run()

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert manifest.exists()
    catalog.close()


def test_public_remove_command_blocks_unreadable_manifest_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text("[project]\n", encoding="utf-8")
    before = manifest.read_bytes()

    from odoo_instance_sdk.project import ProjectConfig

    def unreadable(_path: Path) -> ProjectConfig:
        raise OSError("manifest permission denied")

    monkeypatch.setattr(ProjectConfig, "load", unreadable)
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(),
        ),
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert manifest.read_bytes() == before
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_remove_command_refuses_manifest_drift_before_mutation(
    tmp_path: Path,
) -> None:
    """A manifest becoming malformed after capture cannot reach a child action."""
    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text("[project]\n", encoding="utf-8")
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(from_project=lambda _project: None),
        ),
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    manifest.write_text("[project\n", encoding="utf-8")
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert manifest.read_text(encoding="utf-8") == "[project\n"
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_remove_command_refuses_manifest_drift_when_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest becoming unreadable after capture cannot reach a child action."""
    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text("[project]\n", encoding="utf-8")
    before = manifest.read_bytes()
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(from_project=lambda _project: None),
        ),
    )
    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )

    from odoo_instance_sdk.project import ProjectConfig

    def unreadable(_path: Path) -> ProjectConfig:
        raise OSError("manifest permission denied")

    monkeypatch.setattr(ProjectConfig, "load", unreadable)
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert manifest.read_bytes() == before
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_remove_command_refuses_registered_root_symlink(
    tmp_path: Path,
) -> None:
    """A dangling registered root is present lexically and cannot be forgotten."""
    catalog, project_id, root = _catalog(tmp_path)
    (root / ".git").rmdir()
    root.rmdir()
    missing_target = tmp_path / "missing-target"
    root.symlink_to(missing_target, target_is_directory=True)
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(),
        ),
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    assert expected_project_confirmation_for_command(command) == f"{root.name}:{project_id}"
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert root.is_symlink()
    assert not root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()
