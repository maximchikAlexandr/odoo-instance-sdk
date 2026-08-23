from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance


@pytest.fixture
def config() -> OdooClientConfig:
    return OdooClientConfig(executable="/usr/bin/odoo")


@pytest.fixture
def client(config: OdooClientConfig) -> OdooClient:
    return OdooClient(config=config)


@pytest.fixture
def instance(client: OdooClient) -> OdooInstance:
    return client.instance("http://localhost:8069", master_password="admin")


@pytest.fixture
def instance_no_pwd(client: OdooClient) -> OdooInstance:
    return client.instance("http://127.0.0.1:8069")


@pytest.fixture
def instance_remote(client: OdooClient) -> OdooInstance:
    return client.instance("http://example.com:8069", master_password="admin")


@pytest.fixture
def backup_fixtures(tmp_path: Path) -> dict[str, Path]:
    from tests.fixtures.backups import write_fixtures as write_backup_fixtures

    return write_backup_fixtures(tmp_path / "backups")


@pytest.fixture
def pg_restore_fixtures(tmp_path: Path) -> dict[str, Path]:
    from tests.fixtures.pg_restore import write_fixtures as write_pg_restore_fixtures

    return write_pg_restore_fixtures(tmp_path / "pg_restore")


def _git_run(args: list[str], *, cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        shell=False,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git_run(["git", "init", "-b", "main"], cwd=repo)
    _git_run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _git_run(["git", "config", "user.name", "Test"], cwd=repo)
    (repo / "README.md").write_text("# Test\n")
    _git_run(["git", "add", "."], cwd=repo)
    _git_run(["git", "commit", "-m", "initial"], cwd=repo)
    return repo


@pytest.fixture
def fake_python(tmp_path: Path) -> Path:
    bindir = tmp_path / "fakebin"
    bindir.mkdir(parents=True, exist_ok=True)
    pybin = bindir / "fakepython"
    pybin.write_text(
        textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        if len(sys.argv) > 1 and sys.argv[1] == "-c" and "sys.prefix" in sys.argv[2]:
            print("True")
            sys.exit(0)
        sys.exit(0)
    """)
    )
    os.chmod(pybin, 0o755)
    return pybin


@pytest.fixture
def fake_uv(tmp_path: Path) -> Path:
    bindir = tmp_path / "fakebin"
    bindir.mkdir(parents=True, exist_ok=True)
    uvbin = bindir / "uv"
    uvbin.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(uvbin, 0o755)
    return uvbin


@pytest.fixture
def env_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_uv: Path) -> OdooClient:
    monkeypatch.setenv("PATH", str(fake_uv.parent) + os.pathsep + os.environ.get("PATH", ""))
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    cache_root = tmp_path / "cache"
    data_root.mkdir()
    state_root.mkdir()
    cache_root.mkdir()

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_data_root", lambda **_kwargs: data_root
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_environments_root",
        lambda **_kwargs: data_root / "environments",
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_state_root", lambda: state_root)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_locks_dir", lambda: state_root / "locks"
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: cache_root)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda: data_root / "catalog.sqlite3"
    )
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0").removeprefix("gw")
    port_start = 8069 + (int(worker) if worker.isdigit() else 0) * 31
    monkeypatch.setattr("odoo_instance_sdk.internal.port_allocation._HTTP_RANGE_START", port_start)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.port_allocation._HTTP_RANGE_END", port_start + 30
    )

    return OdooClient(config=OdooClientConfig(executable="odoo"))


@pytest.fixture
def source_config(git_repo: Path) -> Path:
    cfg = git_repo / "odoo.conf"
    cfg.write_text(
        textwrap.dedent("""\
        [options]
        db_name = comerta
        http_interface = 127.0.0.1
        http_port = 8069
        admin_passwd = admin
        db_host = localhost
        db_port = 5432
        db_user = odoo
        db_password = secret
        data_dir = /tmp/odoo_data
    """)
    )
    return cfg


@pytest.fixture
def project_manifest(git_repo: Path, fake_python: Path, source_config: Path) -> Path:
    manifest_dir = git_repo / ".odcli"
    manifest_dir.mkdir(exist_ok=True)
    manifest = manifest_dir / "project.toml"
    fake_odoo = fake_python.parent / "odoo-bin"
    fake_odoo.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(fake_odoo, 0o755)
    rel_config = (
        source_config.relative_to(git_repo)
        if source_config.is_relative_to(git_repo)
        else source_config
    )
    manifest.write_text(
        textwrap.dedent(f"""\
        [project]
        odoo_bin = "{fake_odoo}"
        python = "{fake_python}"
        source_config = "{rel_config}"
        default_source_database = "comerta"
    """)
    )
    return git_repo
