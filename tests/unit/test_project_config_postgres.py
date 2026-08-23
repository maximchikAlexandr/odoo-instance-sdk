from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig


def test_postgres_section_round_trip(tmp_path: Path) -> None:
    cfg = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        postgres=PostgresProjectConfig(
            mode="compose",
            image="pgvector/pgvector:pg16",
            port=5468,
            user="odoo",
        ),
    )
    manifest = cfg.to_manifest()
    assert "[postgres]" in manifest
    assert 'mode = "compose"' in manifest
    assert 'image = "pgvector/pgvector:pg16"' in manifest
    assert "port = 5468" in manifest
    assert 'user = "odoo"' in manifest
    assert "password" not in manifest.lower()


def test_postgres_default_external_omits_section(tmp_path: Path) -> None:
    cfg = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        postgres=PostgresProjectConfig(mode="external"),
    )
    assert "[postgres]" not in cfg.to_manifest()


def test_postgres_section_load(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        "[project]\n"
        'odoo_bin = "/opt/odoo/odoo-bin"\n'
        "[postgres]\n"
        'mode = "compose"\n'
        'image = "pgvector/pgvector:pg16"\n'
        "port = 5468\n"
        'user = "odoo"\n'
    )
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.postgres is not None
    assert cfg.postgres.mode == "compose"
    assert cfg.postgres.image == "pgvector/pgvector:pg16"
    assert cfg.postgres.port == 5468
    assert cfg.postgres.user == "odoo"


def test_legacy_manifest_without_postgres(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text('[project]\nodoo_bin = "/opt/odoo/odoo-bin"\n')
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.postgres is None


def test_is_default_detection() -> None:
    assert PostgresProjectConfig(mode="external").is_default() is True
    assert PostgresProjectConfig(mode="external", image="x").is_default() is False
    assert PostgresProjectConfig(mode="compose").is_default() is False


def test_manifest_no_secret_keys(tmp_path: Path) -> None:
    cfg = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        postgres=PostgresProjectConfig(mode="compose", image="pg", port=5468, user="odoo"),
    )
    # to_manifest must not emit any secret-like key
    manifest = cfg.to_manifest().lower()
    for secret in ("password", "secret", "token", "api_key"):
        assert secret not in manifest


def test_postgres_config_frozen() -> None:
    cfg = PostgresProjectConfig(mode="compose", image="pg", port=5468, user="odoo")
    with pytest.raises(Exception):  # msgspec frozen
        cfg.image = "other"  # type: ignore[misc]


def test_postgres_manifest_rejects_unknown_keys_and_coercions(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        '[project]\n[postgres]\nmode = "compose"\nport = "5468"\nextra = true\n'
    )
    with pytest.raises(ConfigError, match=r"invalid \[postgres\]"):
        ProjectConfig.load(tmp_path)
