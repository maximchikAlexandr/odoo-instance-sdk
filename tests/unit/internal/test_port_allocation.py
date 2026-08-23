from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import EnvironmentConflictError
from odoo_instance_sdk.internal.address import AddressState
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


@pytest.fixture(autouse=True)
def _free_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.port_allocation.probe_address",
        lambda _host, _port: AddressState.FREE,
    )


def _write_manifest(
    repo: Path,
    *,
    postgres_port: int | None = None,
    preferred_http_port: int | None = None,
) -> None:
    lines = ["[project]", 'odoo_bin = "/usr/bin/odoo"']
    if preferred_http_port is not None:
        lines.append(f"preferred_http_port = {preferred_http_port}")
    if postgres_port is not None:
        lines.extend(
            [
                "",
                "[postgres]",
                'mode = "compose"',
                'image = "postgres:16"',
                f"port = {postgres_port}",
                'user = "odoo"',
            ]
        )
    manifest_dir = repo / ".odcli"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "project.toml").write_text("\n".join(lines) + "\n")


def _write_generated_config(path: Path, http_port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[options]\nhttp_interface = 127.0.0.1\nhttp_port = {http_port}\n")


def _register_env(catalog: BackupCatalog, repo: Path, generated_config: Path) -> None:
    catalog.create_environment(
        {
            "id": str(uuid.uuid4()),
            "name": repo.name,
            "repository_root": str(repo),
            "git_common_dir": str(repo / ".git"),
            "branch": "main",
            "base_ref": "HEAD",
            "worktree_path": str(repo / "worktree"),
            "generated_config_path": str(generated_config),
            "python_environment_path": str(repo / "venv"),
            "python_environment_owned": False,
            "dependency_lock_path": str(repo / "requirements.lock"),
            "db_mode": "shared",
            "source_db_name": "db",
            "target_db_name": None,
            "backup_id": None,
            "runtime_json": "{}",
            "state": "ready",
            "created_at": "2026-01-01T00:00:00",
            "last_used_at": None,
            "removed_at": None,
            "last_error": None,
        }
    )


def test_postgres_allocation_skips_other_project_manifest_port(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project_a = tmp_path / "project-a"
    generated = project_a / "odoo.conf"
    _write_manifest(project_a, postgres_port=5468)
    _write_generated_config(generated, 8069)
    _register_env(catalog, project_a, generated)

    assert find_free_port("postgres", catalog) == 5469
    catalog.close()


def test_http_allocation_skips_other_project_preferred_port(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project_a = tmp_path / "project-a"
    generated = project_a / "odoo.conf"
    _write_manifest(project_a, preferred_http_port=8070)
    _write_generated_config(generated, 8069)
    _register_env(catalog, project_a, generated)

    assert find_free_port("http", catalog) == 8071
    catalog.close()


def test_manual_postgres_manifest_edit_is_source_of_truth(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project_a = tmp_path / "project-a"
    generated = project_a / "odoo.conf"
    _write_manifest(project_a, postgres_port=5468)
    _write_generated_config(generated, 8069)
    _register_env(catalog, project_a, generated)
    _write_manifest(project_a, postgres_port=5500)

    assert find_free_port("postgres", catalog) == 5468
    catalog.close()


def test_exclude_project_skips_own_manifest_ports(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project_a = tmp_path / "project-a"
    generated = project_a / "odoo.conf"
    _write_manifest(project_a, postgres_port=5468)
    _write_generated_config(generated, 8069)
    _register_env(catalog, project_a, generated)

    assert find_free_port("postgres", catalog, exclude_project=project_a) == 5468
    catalog.close()


def test_generated_odoo_conf_is_http_source_of_truth(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project_a = tmp_path / "project-a"
    generated = project_a / "odoo.conf"
    _write_manifest(project_a)
    _write_generated_config(generated, 8077)
    _register_env(catalog, project_a, generated)

    assert find_free_port("http", catalog) == 8069
    catalog.close()


def test_requested_http_port_conflict_raises(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project_a = tmp_path / "project-a"
    generated = project_a / "odoo.conf"
    _write_manifest(project_a)
    _write_generated_config(generated, 8077)
    _register_env(catalog, project_a, generated)

    with pytest.raises(EnvironmentConflictError, match="already allocated"):
        find_free_port("http", catalog, requested=8077)
    catalog.close()
