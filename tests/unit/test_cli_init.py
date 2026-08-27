from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli


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
