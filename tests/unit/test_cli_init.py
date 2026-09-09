from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def test_init_catalogue_access_is_worker_local_and_not_production(
    tmp_path: Path, isolated_cli_catalogue: Path, production_catalogue_path: Path
) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert isolated_cli_catalogue.parent != production_catalogue_path.parent


def test_worker_local_catalogues_do_not_cross_contaminate_monitor_projects(
    tmp_path: Path,
) -> None:
    catalogues: dict[str, Path] = {}
    for worker, project_name in (("gw0", "project-a"), ("gw1", "project-b")):
        root = tmp_path / worker / project_name
        common = root / ".git"
        common.mkdir(parents=True)
        catalog_path = root.parent / "catalog.sqlite3"
        catalogues[worker] = catalog_path
        project_id = f"project_{repo_key(root, common)}"
        catalog = BackupCatalog(db_path=catalog_path)
        try:
            catalog._register_project(project_id, root, common)
        finally:
            catalog.close()

    snapshots = {
        worker: EnvironmentMonitor(catalog_path=path).snapshot()
        for worker, path in catalogues.items()
    }

    assert [project.id for project in snapshots["gw0"].projects] == [
        f"project_{repo_key(tmp_path / 'gw0' / 'project-a', tmp_path / 'gw0' / 'project-a' / '.git')}"
    ]
    assert [project.id for project in snapshots["gw1"].projects] == [
        f"project_{repo_key(tmp_path / 'gw1' / 'project-b', tmp_path / 'gw1' / 'project-b' / '.git')}"
    ]
    assert snapshots["gw0"].projects[0].id != snapshots["gw1"].projects[0].id


def test_no_input_missing_odoo_bin_fails(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--no-input", "--project", str(tmp_path)])
    assert result.exit_code == 1
    assert "--odoo-bin" in result.output


def test_no_input_full_specified_writes(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert (tmp_path / ".odcli" / "project.toml").is_file()
    assert (tmp_path / ".odcli" / ".gitignore").read_text() == ".env\nodoo.conf\n"
    assert not (tmp_path / ".gitignore").exists()


def test_init_preserves_existing_root_gitignore(tmp_path: Path) -> None:
    root_ignore = tmp_path / ".gitignore"
    root_ignore.write_text("project-specific-rule\n")
    before = root_ignore.stat()

    result = CliRunner().invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert root_ignore.read_text() == "project-specific-rule\n"
    assert root_ignore.stat().st_ino == before.st_ino
    assert root_ignore.stat().st_mtime_ns == before.st_mtime_ns
    assert (tmp_path / ".odcli" / ".gitignore").read_text() == ".env\nodoo.conf\n"


def test_dry_run_json_returns_manifest_no_write(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--dry-run",
            "--json",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["dry_run"] is True
    assert envelope["data"]["odoo_bin"] == "/opt/odoo/odoo-bin"
    assert not (tmp_path / ".odcli" / "project.toml").exists()


def test_idempotent_identical_is_noop(tmp_path: Path) -> None:
    runner = CliRunner()
    args = [
        "init",
        "--no-input",
        "--odoo-bin",
        "/opt/odoo/odoo-bin",
        "--python",
        "python3",
        "--project",
        str(tmp_path),
    ]
    first = runner.invoke(cli, args)
    assert first.exit_code == 0
    mtime_before = (tmp_path / ".odcli" / "project.toml").stat().st_mtime_ns
    second = runner.invoke(cli, args)
    assert second.exit_code == 0
    assert "no-op" in second.output.lower()
    mtime_after = (tmp_path / ".odcli" / "project.toml").stat().st_mtime_ns
    assert mtime_before == mtime_after


def test_non_identical_no_input_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--project",
            str(tmp_path),
        ],
    )
    result = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/other/odoo-bin",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "differs" in result.output


def test_non_identical_no_input_yes_replaces_manifest_atomically(tmp_path: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--project",
            str(tmp_path),
        ],
    )
    assert first.exit_code == 0, first.output
    manifest = tmp_path / ".odcli" / "project.toml"
    before_inode = manifest.stat().st_ino

    result = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--yes",
            "--odoo-bin",
            "/opt/other/odoo-bin",
            "--python",
            "python3",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "/opt/other/odoo-bin" in manifest.read_text()
    assert manifest.stat().st_ino != before_inode
    assert list((tmp_path / ".odcli").glob("project.toml*.tmp")) == []
    assert not (tmp_path / ".gitignore").exists()


def test_yes_dry_run_does_not_write_existing_manifest_or_ignore(tmp_path: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--project",
            str(tmp_path),
        ],
    )
    assert first.exit_code == 0, first.output
    manifest = tmp_path / ".odcli" / "project.toml"
    ignore = tmp_path / ".odcli" / ".gitignore"
    manifest_before = manifest.read_bytes()
    ignore_before = ignore.read_bytes()
    manifest_stat_before = manifest.stat()
    ignore_stat_before = ignore.stat()

    result = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--yes",
            "--dry-run",
            "--odoo-bin",
            "/opt/other/odoo-bin",
            "--python",
            "python3",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert manifest.read_bytes() == manifest_before
    assert ignore.read_bytes() == ignore_before
    assert manifest.stat().st_ino == manifest_stat_before.st_ino
    assert manifest.stat().st_mtime_ns == manifest_stat_before.st_mtime_ns
    assert ignore.stat().st_ino == ignore_stat_before.st_ino
    assert ignore.stat().st_mtime_ns == ignore_stat_before.st_mtime_ns
    assert not (tmp_path / ".gitignore").exists()


def test_wizard_prompts_for_missing_odoo_bin(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--project", str(tmp_path)],
        input="/opt/odoo/odoo-bin\n",
    )
    assert result.exit_code == 0
    content = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "/opt/odoo/odoo-bin" in content


def test_wizard_all_specified_no_prompts(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--config",
            "./odoo.conf",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "Path to odoo-bin" not in result.output


def test_non_identical_tty_prompt_overwrite(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--project",
            str(tmp_path),
        ],
    )
    result = runner.invoke(
        cli,
        [
            "init",
            "--odoo-bin",
            "/opt/other/odoo-bin",
            "--project",
            str(tmp_path),
        ],
        input="y\n",
    )
    assert result.exit_code == 0
    content = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "/opt/other/odoo-bin" in content


def test_from_vscode_import(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "comerta-launch.json"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--from-vscode",
            str(fixture),
            "--launch-name",
            "Odoo comerta",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    content = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "CMRT-361_1" in content
    assert "8068" in content
    assert "--dev=qweb,xml" in content
    assert "comerta_base" not in content


@pytest.mark.parametrize("source", ["direct", "vscode"])
def test_dry_run_manifest_sanitizes_cli_and_vscode_controls(source: str, tmp_path: Path) -> None:
    payload = "\x00\x1b[2J\x9b31m\x7f"
    runner = CliRunner()
    if source == "direct":
        args = [
            "init",
            "--no-input",
            "--dry-run",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            f"python-{payload}",
            "--database",
            f"db-{payload}",
            "--project",
            str(tmp_path),
        ]
    else:
        launch = tmp_path / "launch.json"
        launch.write_text(
            json.dumps(
                {
                    "configurations": [
                        {
                            "name": "Odoo malicious",
                            "type": "debugpy",
                            "request": "launch",
                            "program": "${workspaceFolder}/odoo-bin",
                            "python": f"python-{payload}",
                            "args": [f"--dev={payload}"],
                        }
                    ]
                }
            )
        )
        args = [
            "init",
            "--no-input",
            "--dry-run",
            "--from-vscode",
            str(launch),
            "--project",
            str(tmp_path),
        ]

    result = runner.invoke(cli, args)

    assert result.exit_code == 0, result.output
    assert "[project]" in result.output
    assert "\x00" not in result.output
    assert "\x1b" not in result.output
    assert "\x7f" not in result.output
    assert "\x9b" not in result.output
    assert r"\x00" in result.output
    assert r"\x1b[2J" in result.output
    assert r"\x9b31m" in result.output
    assert r"\x7f" in result.output
