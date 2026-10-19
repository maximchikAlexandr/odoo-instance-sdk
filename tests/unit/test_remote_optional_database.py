from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.project import ProjectConfig, RemoteSourceConfig
from odoo_instance_sdk.resources.database import DatabaseResource


def _invoke_remote_add(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    database: str | None,
    dry_run: bool,
) -> tuple[Result, Path, bytes]:
    write_manifest(tmp_path, ProjectConfig(repository_root=tmp_path))
    manifest = tmp_path / ".odcli/project.toml"
    before = manifest.read_bytes()

    def no_listing(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        pytest.fail("remote add must not query the server")

    monkeypatch.setattr(DatabaseResource, "names", no_listing)
    args = [
        "--project",
        str(tmp_path),
        "remote",
        "add",
        "lab",
        "--url",
        "https://lab.example",
        "--branch",
        "dev",
        "--format",
        "json",
    ]
    if database is not None:
        args.extend(["--database", database])
    if dry_run:
        args.append("--dry-run")
    result = CliRunner().invoke(cli, args)
    return result, manifest, before


def test_remote_add_optional_database_is_offline_and_roundtrips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, manifest, _before = _invoke_remote_add(
        tmp_path, monkeypatch, database="chosen_db", dry_run=False
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True
    source = ProjectConfig.load(tmp_path).remote_instances[0]
    assert source == RemoteSourceConfig(
        name="lab", base_url="https://lab.example", git_branch="dev", database="chosen_db"
    )
    saved = tomllib.loads(manifest.read_text())["remote_instances"]["lab"]
    assert saved["database"] == "chosen_db"
    listed = CliRunner().invoke(
        cli, ["--project", str(tmp_path), "remote", "ls", "--format", "json"]
    )
    assert listed.exit_code == 0
    assert json.loads(listed.output)["result"]["remote_instances"][0]["database"] == "chosen_db"
    update = CliRunner().invoke(
        cli,
        [
            "--project",
            str(tmp_path),
            "remote",
            "update",
            "lab",
            "--branch",
            "release",
            "--format",
            "json",
        ],
    )
    assert update.exit_code == 0, update.output
    updated = ProjectConfig.load(tmp_path).remote_instances[0]
    assert updated.database == "chosen_db"
    assert updated.git_branch == "release"


def test_remote_add_optional_database_is_offline_and_roundtrips_without_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, manifest, _before = _invoke_remote_add(
        tmp_path, monkeypatch, database=None, dry_run=False
    )
    assert result.exit_code == 0, result.output
    source = ProjectConfig.load(tmp_path).remote_instances[0]
    assert source.database is None
    saved = tomllib.loads(manifest.read_text())["remote_instances"]["lab"]
    assert "database" not in saved


@pytest.mark.parametrize("database", [None, "chosen_db"])
def test_remote_add_optional_database_is_offline_and_roundtrips_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: str | None
) -> None:
    result, manifest, before = _invoke_remote_add(
        tmp_path, monkeypatch, database=database, dry_run=True
    )
    assert result.exit_code == 0, result.output
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("database", ["", " ", 42, False])
def test_remote_manifest_rejects_invalid_database(
    tmp_path: Path, database: str | int | bool
) -> None:
    with pytest.raises(ConfigError, match="database"):
        ProjectConfig._from_mapping(
            {},
            repository_root=tmp_path,
            remote_instances_data={
                "lab": {
                    "base_url": "https://lab.example",
                    "git_branch": "dev",
                    "database": database,
                }
            },
        )


@pytest.mark.parametrize("database", [None, "chosen_db"])
def test_ps_reads_project_with_optional_remote_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: str | None
) -> None:
    write_manifest(
        tmp_path,
        ProjectConfig(
            repository_root=tmp_path,
            remote_instances=(
                RemoteSourceConfig(
                    name="lab",
                    base_url="https://lab.example",
                    git_branch="dev",
                    database=database,
                ),
            ),
        ),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.monitor_context.git_worktree.rev_parse_toplevel",
        lambda path: path,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.monitor_context.git_worktree.rev_parse_git_common_dir",
        lambda path: path / ".git",
    )
    manifest = tmp_path / ".odcli/project.toml"
    before = manifest.read_bytes()

    result = CliRunner().invoke(cli, ["ps", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "ps"
    assert manifest.read_bytes() == before
