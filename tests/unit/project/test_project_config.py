from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError, ProjectManifestNotFoundError, StalePlanError
from odoo_instance_sdk.internal import project_manifest as project_manifest_module
from odoo_instance_sdk.internal.project_manifest import assert_no_secrets, write_manifest
from odoo_instance_sdk.project import ProjectConfig, RemoteSourceConfig
from odoo_instance_sdk.project import TestInstanceProjectConfig as ConfigTestInstance
from odoo_instance_sdk.project_init import (
    configure_remote_source,
    configure_remote_source_command,
    list_remote_sources,
    remove_remote_source,
)


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
    assert ".env" in (tmp_path / ".odcli" / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert loaded.repository_root == tmp_path.resolve()


def test_manifest_ignore_normalizes_managed_rules_after_user_negations(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / ".gitignore").write_text(
        "keep-me\n.env\n!odoo.conf\n!*.conf\nodoo.conf\n", encoding="utf-8"
    )
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))

    write_manifest(tmp_path, cfg)

    assert (manifest_dir / ".gitignore").read_text(encoding="utf-8") == (
        "keep-me\n!odoo.conf\n!*.conf\n.env\nodoo.conf\n"
    )


def test_manifest_ignore_refuses_outward_symlink_without_mutating_target(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    outside = tmp_path / "outside.gitignore"
    outside.write_text("keep-me\n", encoding="utf-8")
    (manifest_dir / ".gitignore").symlink_to(outside)
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))

    with pytest.raises(OSError):
        write_manifest(tmp_path, cfg)

    assert outside.read_text(encoding="utf-8") == "keep-me\n"
    assert not (manifest_dir / "project.toml").exists()


def test_manifest_ignore_refuses_symlinked_project_local_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".odcli").symlink_to(outside, target_is_directory=True)
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))

    with pytest.raises(OSError):
        write_manifest(tmp_path, cfg)

    assert not (outside / ".gitignore").exists()
    assert not (outside / "project.toml").exists()


def test_manifest_ignore_refuses_check_write_race_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    ignore = manifest_dir / ".gitignore"
    ignore.write_text("keep-me\n", encoding="utf-8")
    outside = tmp_path / "outside.gitignore"
    outside.write_text("outside\n", encoding="utf-8")
    real_read = project_manifest_module._read_regular_ignore

    def race(root_fd: int, display_path: Path) -> tuple[str, os.stat_result | None]:
        content, target_stat = real_read(root_fd, display_path)
        ignore.unlink()
        ignore.symlink_to(outside)
        return content, target_stat

    monkeypatch.setattr(project_manifest_module, "_read_regular_ignore", race)
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))

    with pytest.raises(OSError):
        write_manifest(tmp_path, cfg)

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert ignore.is_symlink()


def test_manifest_ignore_cleans_temp_after_injected_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_fchmod(_fd: int, _mode: int) -> None:
        raise OSError("injected write failure")

    monkeypatch.setattr(os, "fchmod", fail_fchmod)
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))

    with pytest.raises(OSError, match="injected write failure"):
        write_manifest(tmp_path, cfg)

    assert list((tmp_path / ".odcli").glob(".gitignore.*.tmp")) == []
    assert not (tmp_path / ".odcli" / ".gitignore").exists()


def test_manifest_ignore_cleans_temp_after_injected_stream_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fdopen = os.fdopen

    class FailingWriter:
        def __init__(self, fd: int) -> None:
            self._stream = real_fdopen(fd, "wb")

        def __enter__(self) -> FailingWriter:
            return self

        def __exit__(self, *_args: object) -> None:
            self._stream.close()

        def write(self, _content: bytes) -> int:
            raise OSError("injected stream write failure")

        def flush(self) -> None:
            self._stream.flush()

        def fileno(self) -> int:
            return self._stream.fileno()

    def fail_fdopen(fd: int, _mode: str) -> FailingWriter:
        return FailingWriter(fd)

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))

    with pytest.raises(OSError, match="injected stream write failure"):
        write_manifest(tmp_path, cfg)

    assert list((tmp_path / ".odcli").glob(".gitignore.*.tmp")) == []
    assert not (tmp_path / ".odcli" / ".gitignore").exists()


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
        database=None,
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


def test_test_instance_database_is_optional_and_roundtrips_without_it(tmp_path: Path) -> None:
    cfg = ProjectConfig._from_mapping(
        {},
        repository_root=tmp_path,
        test_instance_data={"base_url": "https://example.test", "git_branch": "main"},
    )
    assert cfg.test_instance is not None
    assert cfg.test_instance.database is None
    manifest = cfg.to_manifest()
    assert "database" not in manifest
    write_manifest(tmp_path, cfg)
    reloaded = ProjectConfig.load(tmp_path)
    assert reloaded.test_instance is not None
    assert reloaded.test_instance.database is None
    assert reloaded.to_manifest() == manifest


def test_test_instance_database_optional_preserves_explicit_value(tmp_path: Path) -> None:
    cfg = ProjectConfig._from_mapping(
        {},
        repository_root=tmp_path,
        test_instance_data={
            "base_url": "https://example.test",
            "database": "mydb",
            "git_branch": "main",
        },
    )
    assert cfg.test_instance is not None
    assert cfg.test_instance.database == "mydb"
    manifest = cfg.to_manifest()
    assert 'database = "mydb"' in manifest


def test_legacy_manifest_omits_new_sections_and_is_byte_stable(tmp_path: Path) -> None:
    cfg = ProjectConfig(repository_root=tmp_path, odoo_bin=Path("/opt/odoo/odoo-bin"))
    expected = '[project]\nodoo_bin = "/opt/odoo/odoo-bin"\n'
    assert cfg.to_manifest() == expected
    write_manifest(tmp_path, cfg)
    assert (tmp_path / ".odcli" / "project.toml").read_text() == expected
    assert ProjectConfig.load(tmp_path).to_manifest() == expected


def test_named_remote_sources_roundtrip_deterministically(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        "[project]\n\n"
        "[remote_instances.zeta]\n"
        'base_url = "HTTPS://Zeta.example:443/"\n'
        'database = "zeta"\n'
        'git_branch = "main"\n\n'
        "[remote_instances.alpha]\n"
        'base_url = "https://alpha.example/"\n'
        'database = "alpha"\n'
        'git_branch = "release/19"\n'
    )

    config = ProjectConfig.load(tmp_path)

    assert [source.name for source in config.remote_instances] == ["alpha", "zeta"]
    assert config.remote_instances[0].base_url == "https://alpha.example"
    assert config.to_manifest().index("[remote_instances.alpha]") < config.to_manifest().index(
        "[remote_instances.zeta]"
    )
    assert (
        ProjectConfig._from_mapping(
            {},
            repository_root=tmp_path,
            remote_instances_data={
                "ALPHA": {
                    "base_url": "https://alpha.example/",
                    "database": "alpha",
                    "git_branch": "release/19",
                }
            },
        )
        .remote_instances[0]
        .name
        == "alpha"
    )


def test_named_remote_sources_reject_unknown_and_duplicate_fields(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid"):
        ProjectConfig._from_mapping(
            {},
            repository_root=tmp_path,
            remote_instances_data={
                "alpha": {
                    "base_url": "https://alpha.example",
                    "database": "alpha",
                    "git_branch": "main",
                    "password": "secret",
                }
            },
        )
    with pytest.raises(ConfigError, match="duplicate remote source"):
        ProjectConfig(
            repository_root=tmp_path,
            remote_instances=(
                RemoteSourceConfig(
                    name="alpha", base_url="https://a.example", database="a", git_branch="main"
                ),
                RemoteSourceConfig(
                    name="ALPHA", base_url="https://b.example", database="b", git_branch="main"
                ),
            ),
        )


def test_public_remote_source_operations_are_atomic_and_idempotent(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        ProjectConfig(
            repository_root=tmp_path,
            odoo_bin=Path("/opt/odoo/odoo-bin"),
            source_config=Path("odoo.conf"),
        ),
    )
    source = RemoteSourceConfig(
        name="staging",
        base_url="https://staging.example",
        database="staging",
        git_branch="main",
    )
    assert configure_remote_source(tmp_path, source) == (source,)
    assert configure_remote_source(tmp_path, source) == (source,)
    assert list_remote_sources(tmp_path) == (source,)

    replacement = RemoteSourceConfig(
        name="staging",
        base_url="https://staging.example",
        database="staging_next",
        git_branch="develop",
    )
    with pytest.raises(ValueError, match="replace=True"):
        configure_remote_source(tmp_path, replacement)
    assert configure_remote_source(tmp_path, replacement, replace=True) == (replacement,)
    assert list_remote_sources(tmp_path) == (replacement,)

    manifest = tmp_path / ".odcli" / "project.toml"
    planned = configure_remote_source_command(
        tmp_path,
        RemoteSourceConfig(
            name="production",
            base_url="https://production.example",
            database="production",
            git_branch="main",
        ),
    )
    before_preview = manifest.read_text(encoding="utf-8")
    assert planned.plan.steps[0].mutating is True
    assert manifest.read_text(encoding="utf-8") == before_preview

    drifted = configure_remote_source_command(tmp_path, source, replace=True)
    manifest.write_text(before_preview + "\n", encoding="utf-8")
    with pytest.raises(StalePlanError, match="manifest changed"):
        drifted.run()

    assert remove_remote_source(tmp_path, "STAGING") == ()
    assert remove_remote_source(tmp_path, "staging") == ()
