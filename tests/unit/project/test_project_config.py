from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError, ProjectManifestNotFoundError
from odoo_instance_sdk.internal.project_manifest import assert_no_secrets, write_manifest
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project import TestInstanceProjectConfig as ConfigTestInstance


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
    assert cfg.repository_root == tmp_path.resolve()


def test_load_missing_manifest_raises_typed_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectManifestNotFoundError) as exc_info:
        ProjectConfig.load(tmp_path)
    assert "odcli init" in str(exc_info.value)


def test_roundtrip_write_read_secrets_free(tmp_path: Path) -> None:
    cfg = ProjectConfig(
        repository_root=tmp_path,
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
    assert loaded.to_manifest() == cfg.to_manifest()
    assert ".odcli/.env" in (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert loaded.repository_root == tmp_path.resolve()


def test_manifest_ignore_refuses_outward_symlink_without_mutating_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside.gitignore"
    outside.write_text("keep-me\n", encoding="utf-8")
    (tmp_path / ".gitignore").symlink_to(outside)
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))

    with pytest.raises(OSError):
        write_manifest(tmp_path, cfg)

    assert outside.read_text(encoding="utf-8") == "keep-me\n"
    assert not (tmp_path / ".odcli" / "project.toml").exists()


def test_manifest_ignore_refuses_check_write_race_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ignore = tmp_path / ".gitignore"
    ignore.write_text("keep-me\n", encoding="utf-8")
    outside = tmp_path / "outside.gitignore"
    outside.write_text("outside\n", encoding="utf-8")
    real_stat = os.stat
    calls = 0

    def race(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal calls
        if path == ".gitignore" and follow_symlinks is False:
            calls += 1
            if calls == 2:
                ignore.unlink()
                ignore.symlink_to(outside)
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "stat", race)
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))

    with pytest.raises(OSError):
        write_manifest(tmp_path, cfg)

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert ignore.is_symlink()


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


def test_project_config_is_frozen(tmp_path: Path) -> None:
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))
    with pytest.raises(AttributeError):
        cfg.odoo_bin = Path("/other")  # type: ignore[misc]


def test_bare_project_config_requires_explicit_repository_root() -> None:
    with pytest.raises(TypeError, match="repository_root"):
        ProjectConfig()  # type: ignore[call-arg]


def test_manual_config_can_explicitly_bind_repository_root(tmp_path: Path) -> None:
    cfg = ProjectConfig(repository_root=tmp_path)
    assert cfg.repository_root == tmp_path.resolve()
    assert "repository_root" not in cfg.to_manifest()


def test_binding_does_not_change_manifest_equality(tmp_path: Path) -> None:
    left = ProjectConfig(repository_root=tmp_path / "one", odoo_bin=Path("/opt/odoo"))
    right = ProjectConfig(repository_root=tmp_path / "two", odoo_bin=Path("/opt/odoo"))
    assert left != right
    assert left.to_manifest() == right.to_manifest()


def test_configured_preparation_settings_normalize_and_roundtrip(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        "[project]\n"
        'odoo_bin = "/opt/odoo/odoo-bin"\n'
        'default_base_ref = "develop"\n'
        "refresh_after_hours = 24.5\n\n"
        "[test_instance]\n"
        'base_url = "HTTPS://Example.test:443/"\n'
        'database = "remote_test"\n'
        'git_branch = "release/19"\n'
    )

    cfg = ProjectConfig.load(tmp_path)

    assert cfg.default_base_ref == "develop"
    assert cfg.refresh_after_hours == 24.5
    assert cfg.test_instance == ConfigTestInstance(
        base_url="https://example.test", database="remote_test", git_branch="release/19"
    )
    assert (
        ProjectConfig._from_mapping(
            {
                "odoo_bin": "/opt/odoo/odoo-bin",
                "default_base_ref": "develop",
                "refresh_after_hours": 24.5,
            },
            repository_root=tmp_path,
            test_instance_data={
                "base_url": "https://example.test",
                "database": "remote_test",
                "git_branch": "release/19",
            },
        ).to_manifest()
        == cfg.to_manifest()
    )


def test_readme_preparation_manifest_parses_and_roundtrips(tmp_path: Path) -> None:
    readme = Path(__file__).parents[3] / "README.md"
    section = readme.read_text(encoding="utf-8").split("### Prepare a project database", 1)[1]
    documented_manifest = section.split("```toml", 1)[1].split("```", 1)[0].strip() + "\n"
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(documented_manifest, encoding="utf-8")

    config = ProjectConfig.load(tmp_path)
    assert config.default_base_ref == "main"
    assert config.refresh_after_hours == 24
    assert config.test_instance == ConfigTestInstance(
        base_url="https://odoo-test.example",
        database="testdb",
        git_branch="main",
    )
    assert tomllib.loads(config.to_manifest()) == tomllib.loads(documented_manifest)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_refresh_after_hours_must_be_finite_and_positive(tmp_path: Path, value: float) -> None:
    with pytest.raises(ConfigError):
        ProjectConfig(repository_root=tmp_path, refresh_after_hours=value)


def test_test_instance_rejects_unknown_secret_without_echo(tmp_path: Path) -> None:
    sentinel = "remote-secret-sentinel"
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        "[project]\n\n"
        "[test_instance]\n"
        'base_url = "https://example.test"\n'
        'database = "remote_test"\n'
        f'master_password = "{sentinel}"\n'
    )

    with pytest.raises(ConfigError) as exc_info:
        ProjectConfig.load(tmp_path)
    assert sentinel not in str(exc_info.value)


@pytest.mark.parametrize(
    "value",
    [
        {"base_url": "", "database": "db"},
        {"base_url": "https://example.test", "database": ""},
        {"base_url": "https://example.test", "database": "db", "git_branch": ""},
    ],
)
def test_test_instance_rejects_empty_values(tmp_path: Path, value: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        ProjectConfig._from_mapping({}, repository_root=tmp_path, test_instance_data=value)


def test_legacy_manifest_omits_new_sections_and_is_byte_stable(tmp_path: Path) -> None:
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))
    expected = '[project]\nodoo_bin = "/opt/odoo/odoo-bin"\n'
    assert cfg.to_manifest() == expected
    write_manifest(tmp_path, cfg)
    assert (tmp_path / ".odcli" / "project.toml").read_text() == expected
    assert ProjectConfig.load(tmp_path).to_manifest() == expected
