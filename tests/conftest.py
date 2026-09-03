from __future__ import annotations

import importlib.util
import os
import subprocess
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


_DASHBOARD_MODULES = ("fastapi", "uvicorn")


def _dashboard_extra_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in _DASHBOARD_MODULES)


@pytest.fixture
def env_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[OdooClient]:
    """Provide the environment client even when a mixed path skips nested conftest discovery."""
    from odoo_instance_sdk import OdooClient, OdooClientConfig

    fake_uv = tmp_path / "fakebin" / "uv"
    fake_uv.parent.mkdir()
    fake_uv.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(fake_uv, 0o755)
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
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    try:
        yield client
    finally:
        if client._catalog is not None:
            client._catalog.close()


def _git_run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, shell=False, capture_output=True, text=True, check=True)


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
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import sys
            if len(sys.argv) > 1 and sys.argv[1] == "-c" and "sys.prefix" in sys.argv[2]:
                print("True")
                sys.exit(0)
            sys.exit(0)
        """
        )
    )
    os.chmod(pybin, 0o755)
    return pybin


@pytest.fixture
def source_config(git_repo: Path) -> Path:
    cfg = git_repo / "odoo.conf"
    cfg.write_text(
        textwrap.dedent(
            """\
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
        """
        )
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
    rel_config = source_config.relative_to(git_repo)
    manifest.write_text(
        textwrap.dedent(
            f"""\
            [project]
            odoo_bin = "{fake_odoo}"
            python = "{fake_python}"
            source_config = "{rel_config}"
            default_source_database = "comerta"
        """
        )
    )
    return git_repo


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    root = Path(__file__).resolve().parent
    dashboard_extra_available = _dashboard_extra_available()
    for item in items:
        rel = Path(item.path).relative_to(root).as_posix()
        if rel.startswith("packaging/"):
            item.add_marker(pytest.mark.packaging)
        elif rel.startswith("integration/"):
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)
        if not dashboard_extra_available and item.get_closest_marker("dashboard") is not None:
            item.add_marker(
                pytest.mark.skip(reason="dashboard extra unavailable; run `make dashboard`")
            )
