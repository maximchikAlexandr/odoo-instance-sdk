from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ProjectManifestNotFoundError
from odoo_instance_sdk.internal.project_manifest import assert_no_secrets, write_manifest
from odoo_instance_sdk.project import ProjectConfig


def test_load_existing_manifest(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        "[project]\n"
        'odoo_bin = "/opt/odoo/odoo-bin"\n'
        'python = "python3"\n'
        'source_config = "./odoo.conf"\n'
        'default_source_database = "comerta"\n'
        "preferred_http_port = 8069\n"
        'requirements = ["reqs.txt"]\n'
        'default_run_args = ["--dev=qweb"]\n'
        'runtime_cwd = "."\n'
    )
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.odoo_bin == Path("/opt/odoo/odoo-bin")
    assert cfg.python == "python3"
    assert cfg.source_config == Path("./odoo.conf")
    assert cfg.default_source_database == "comerta"
    assert cfg.preferred_http_port == 8069
    assert cfg.requirements == ("reqs.txt",)
    assert cfg.default_run_args == ("--dev=qweb",)
    assert cfg.runtime_cwd == Path(".")


def test_load_missing_manifest_raises_typed_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectManifestNotFoundError) as exc_info:
        ProjectConfig.load(tmp_path)
    assert "odcli init" in str(exc_info.value)


def test_roundtrip_write_read_secrets_free(tmp_path: Path) -> None:
    cfg = ProjectConfig(
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        source_config=Path("./odoo.conf"),
        default_source_database="comerta",
        preferred_http_port=8069,
        requirements=("reqs.txt",),
        default_run_args=("--dev=qweb",),
        runtime_cwd=Path("."),
    )
    write_manifest(tmp_path, cfg)
    loaded = ProjectConfig.load(tmp_path)
    assert loaded == cfg


def test_manifest_refuses_secrets() -> None:
    with pytest.raises(ValueError, match="secret"):
        assert_no_secrets("[project]\nadmin_passwd = hunter2\n")


def test_load_empty_manifest_defaults(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text("[project]\n")
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.odoo_bin is None
    assert cfg.python is None
    assert cfg.requirements == ()
    assert cfg.default_run_args == ()


def test_project_config_is_frozen() -> None:
    cfg = ProjectConfig(odoo_bin=Path("/opt/odoo/odoo-bin"))
    with pytest.raises(AttributeError):
        cfg.odoo_bin = Path("/other")  # type: ignore[misc]
