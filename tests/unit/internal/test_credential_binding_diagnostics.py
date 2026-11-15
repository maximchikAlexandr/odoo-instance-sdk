from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.internal import paths
from odoo_instance_sdk.internal.generated_config import project_generated_config_path
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.internal.paths import get_project_postgres_dir
from odoo_instance_sdk.internal.pg.inventory import _credentials
from odoo_instance_sdk.internal.project_init import switch_project_default
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster
from tests.integration.real_odoo.credential_binding_diagnostics import (
    capture_binding,
    compare_bindings,
)
from tests.integration.real_odoo.focused_support import focused_cli_environment


def _bind_user_root(monkeypatch: pytest.MonkeyPatch) -> None:
    def user_root(*, ensure_exists: bool = True) -> Path:
        root = Path(os.environ["ODCLI_USER_ROOT"])
        if ensure_exists:
            root.mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(paths, "_user_root", user_root)
    monkeypatch.setattr(
        paths,
        "get_data_root",
        lambda *, ensure_exists=True: user_root(ensure_exists=ensure_exists),
    )


def _project_config(root: Path, database: str) -> ProjectConfig:
    return ProjectConfig(
        repository_root=root,
        default_source_database=database,
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5432),
    )


def _write_generated_config(root: Path, password: str) -> Path:
    path = project_generated_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[options]\n"
        "db_name = previous\n"
        "db_host = 127.0.0.1\n"
        "db_port = 5432\n"
        "db_user = odoo\n"
        f"db_password = {password}\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        f"data_dir = {root / '.odcli' / 'filestore'}\n",
        encoding="utf-8",
    )
    return path


def _instance_for(project: Path, cluster: PostgresCluster) -> SimpleNamespace:
    generated = parse_odoo_config(project_generated_config_path(project))
    config = SimpleNamespace(
        db_host=generated.get("db_host"),
        db_port=int(generated["db_port"]),
        db_user=generated.get("db_user"),
        db_password=generated.get("db_password"),
    )
    return SimpleNamespace(config=config, _postgres_cluster=cluster)


def test_changed_home_reproduction_detects_owned_cluster_credential_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_user_root(monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    initial = _project_config(project, "previous")
    write_manifest(project, initial)
    generated_path = _write_generated_config(project, "cluster-password-a")

    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    monkeypatch.setenv("HOME", str(home_a))
    monkeypatch.setenv("ODCLI_USER_ROOT", str(home_a / ".odcli"))
    cluster_a = PostgresCluster.from_project(project)
    password_a = cluster_a.password_file
    password_a.parent.mkdir(parents=True, exist_ok=True)
    password_a.write_text("cluster-password-a\n", encoding="utf-8")
    before_instance = _instance_for(project, cluster_a)
    before = capture_binding(before_instance, _credentials(cast("OdooInstance", before_instance)))

    monkeypatch.setenv("HOME", str(home_b))
    monkeypatch.setenv("ODCLI_USER_ROOT", str(home_b / ".odcli"))
    cluster_b = PostgresCluster.from_project(project)
    password_b = cluster_b.password_file
    password_b.parent.mkdir(parents=True, exist_ok=True)
    password_b.write_text("cluster-password-b\n", encoding="utf-8")
    switch_project_default(project, _project_config(project, "target"))
    after_instance = _instance_for(project, cluster_b)
    after = capture_binding(after_instance, _credentials(cast("OdooInstance", after_instance)))

    trace = compare_bindings(before, after)
    assert trace == {
        "cluster_identity_unchanged": True,
        "endpoint_identity_unchanged": True,
        "user_identity_unchanged": True,
        "credential_source_before": "instance_config",
        "credential_source_after": "instance_config",
        "password_present_before": True,
        "password_present_after": True,
        "selected_password_matches_baseline": False,
        "generated_password_matches_selected_before": True,
        "generated_password_matches_selected_after": True,
        "generated_password_matches_owned_file_before": True,
        "generated_password_matches_owned_file_after": True,
        "owned_password_file_unchanged": False,
        "password_file_binding_unchanged": False,
        "home_binding_unchanged": False,
        "user_root_binding_unchanged": False,
        "user_root_matches_home_before": True,
        "user_root_matches_home_after": True,
    }
    assert parse_odoo_config(generated_path)["db_password"] == "cluster-password-b"
    assert "cluster-password-a" not in repr(trace)
    assert "cluster-password-b" not in repr(trace)


def test_isolated_catalog_does_not_replace_owned_resource_home(tmp_path: Path) -> None:
    owned_home = tmp_path / "owned-home"
    isolated_catalog = tmp_path / "leaf" / "home" / ".odcli" / "catalog.sqlite3"
    environment = focused_cli_environment({"HOME": str(owned_home)}, isolated_catalog)

    assert environment["HOME"] == str(owned_home)
    assert environment["ODCLI_E2E_CATALOG"] == str(isolated_catalog)


def test_catalog_and_postgres_resource_roots_remain_separate(tmp_path: Path) -> None:
    owned_home = tmp_path / "owned-home"
    isolated_catalog = tmp_path / "leaf" / "home" / ".odcli" / "catalog.sqlite3"
    environment = focused_cli_environment({"HOME": str(owned_home)}, isolated_catalog)

    with pytest.MonkeyPatch.context() as patch:
        _bind_user_root(patch)
        patch.setenv("HOME", environment["HOME"])
        patch.setenv("ODCLI_USER_ROOT", environment["ODCLI_USER_ROOT"])
        cluster_dir = get_project_postgres_dir("project-id")
        assert cluster_dir.is_relative_to(owned_home / ".odcli")
        assert not cluster_dir.is_relative_to(isolated_catalog.parent)


def test_stable_owned_resource_root_preserves_binding_through_default_publication(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    owned_home = tmp_path / "owned-home"
    catalog_path = tmp_path / "leaf" / "catalog.sqlite3"
    environment = focused_cli_environment({"HOME": str(owned_home)}, catalog_path)

    with pytest.MonkeyPatch.context() as patch:
        _bind_user_root(patch)
        patch.setenv("HOME", environment["HOME"])
        patch.setenv("ODCLI_USER_ROOT", environment["ODCLI_USER_ROOT"])

        write_manifest(project, _project_config(project, "previous"))
        _write_generated_config(project, "stable-cluster-password")
        cluster = PostgresCluster.from_project(project)
        cluster.password_file.parent.mkdir(parents=True, exist_ok=True)
        cluster.password_file.write_text("stable-cluster-password\n", encoding="utf-8")
        before_instance = _instance_for(project, cluster)
        before = capture_binding(
            before_instance, _credentials(cast("OdooInstance", before_instance))
        )

        switch_project_default(project, _project_config(project, "target"))

        after_instance = _instance_for(project, PostgresCluster.from_project(project))
        after = capture_binding(after_instance, _credentials(cast("OdooInstance", after_instance)))

    trace = compare_bindings(before, after)
    assert trace["cluster_identity_unchanged"] is True
    assert trace["endpoint_identity_unchanged"] is True
    assert trace["user_identity_unchanged"] is True
    assert trace["selected_password_matches_baseline"] is True
    assert trace["generated_password_matches_owned_file_before"] is True
    assert trace["generated_password_matches_owned_file_after"] is True
    assert trace["owned_password_file_unchanged"] is True
    assert trace["password_file_binding_unchanged"] is True
    assert trace["home_binding_unchanged"] is True
    assert trace["user_root_binding_unchanged"] is True
    assert trace["user_root_matches_home_before"] is True
    assert trace["user_root_matches_home_after"] is True
