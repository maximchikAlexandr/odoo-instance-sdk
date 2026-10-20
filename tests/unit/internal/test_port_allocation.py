from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import EnvironmentConflictError
from odoo_instance_sdk.internal.address import AddressState
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.project import ProjectConfig
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

    assert find_free_port("http", catalog) == 8000
    catalog.close()


def test_http_allocation_reads_existing_catalog_without_opening_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=catalog_path)
    project_a = tmp_path / "project-a"
    generated = project_a / "odoo.conf"
    _write_manifest(project_a, preferred_http_port=8070)
    _write_generated_config(generated, 8069)
    _register_env(catalog, project_a, generated)
    catalog.close()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda **_kwargs: catalog_path,
    )

    assert find_free_port("http", None) == 8000


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


@pytest.mark.parametrize(
    ("preferred", "free_ports", "expected", "checked"),
    [
        pytest.param(8168, {8168, 8000}, 8168, [8168], id="preferred-8168"),
        pytest.param(8168, {8000}, 8000, [8168, 8000], id="busy-preferred-lower-free"),
        pytest.param(7999, {7999, 8000}, 8000, [8000], id="preferred-below-range"),
        pytest.param(9001, {9001, 8000}, 8000, [8000], id="preferred-above-range"),
        pytest.param(None, {8069, 8000}, 8069, [8069], id="default-first"),
        pytest.param(None, {8000}, 8000, [8069, 8000], id="busy-default"),
        pytest.param(8000, {8000}, 8000, [8000], id="preferred-lower-bound"),
        pytest.param(9000, {9000}, 9000, [9000], id="preferred-upper-bound"),
        pytest.param(
            None,
            {9000},
            9000,
            [8069, *range(8000, 8069), *range(8070, 9001)],
            id="scan-upper-bound",
        ),
    ],
)
def test_http_candidate_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preferred: int | None,
    free_ports: set[int],
    expected: int,
    checked: list[int],
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project = ProjectConfig(repository_root=tmp_path, preferred_http_port=preferred)
    probes: list[int] = []

    def probe(host: str, port: int) -> AddressState:
        assert host == "127.0.0.2"
        probes.append(port)
        return AddressState.FREE if port in free_ports else AddressState.OCCUPIED

    monkeypatch.setattr("odoo_instance_sdk.internal.port_allocation.probe_address", probe)
    try:
        assert find_free_port("http", catalog, project=project, host="127.0.0.2") == expected
        assert probes == checked
    finally:
        catalog.close()


@pytest.mark.parametrize("preferred", [None, 8168, 7999, 9001])
def test_http_exhaustion_checks_entire_range_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preferred: int | None,
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    probes: list[int] = []

    def probe(_host: str, port: int) -> AddressState:
        probes.append(port)
        return AddressState.OCCUPIED

    monkeypatch.setattr("odoo_instance_sdk.internal.port_allocation.probe_address", probe)
    try:
        with pytest.raises(EnvironmentConflictError) as caught:
            find_free_port(
                "http",
                catalog,
                project=ProjectConfig(
                    repository_root=tmp_path,
                    preferred_http_port=preferred,
                ),
            )
        assert caught.value.code == "no_free_port"
        assert str(caught.value) == (
            "No available HTTP port in automatic allocation range 8000\u20139000; "
            "all candidates are allocated or unavailable. Pass --http-port explicitly."
        )
        assert len(probes) == 1001
        assert set(probes) == set(range(8000, 9001))
    finally:
        catalog.close()


def test_explicit_http_port_outside_auto_range_is_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    probes: list[int] = []

    def probe(_host: str, port: int) -> AddressState:
        probes.append(port)
        return AddressState.FREE

    monkeypatch.setattr("odoo_instance_sdk.internal.port_allocation.probe_address", probe)
    try:
        assert find_free_port("http", catalog, requested=19069) == 19069
        assert probes == [19069]
    finally:
        catalog.close()


@pytest.mark.parametrize("state", [AddressState.OCCUPIED, AddressState.UNKNOWN])
def test_explicit_http_port_outside_auto_range_is_exact_rejects_unavailable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: AddressState
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    probes: list[int] = []

    def probe(_host: str, port: int) -> AddressState:
        probes.append(port)
        return state

    monkeypatch.setattr("odoo_instance_sdk.internal.port_allocation.probe_address", probe)
    try:
        with pytest.raises(EnvironmentConflictError) as caught:
            find_free_port("http", catalog, requested=19069)
        assert caught.value.code == "port_in_use"
        assert probes == [19069]
    finally:
        catalog.close()


def test_http_preferred_allocated_in_catalog_falls_back(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    repo = tmp_path / "other-project"
    generated = repo / "odoo.conf"
    _write_manifest(repo, preferred_http_port=8168)
    _write_generated_config(generated, 8000)
    _register_env(catalog, repo, generated)
    try:
        project = ProjectConfig(repository_root=tmp_path, preferred_http_port=8168)
        assert find_free_port("http", catalog, project=project) == 8001
    finally:
        catalog.close()


def test_explicit_port_reserved_for_auxiliary_is_not_replaced() -> None:
    with pytest.raises(EnvironmentConflictError, match="8069"):
        find_free_port("http", None, requested=8069, reserved_ports=(8069,))
