from __future__ import annotations

import click
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli


def test_top_level_click_surface_exposes_exactly_all_required_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert set(cli.list_commands(click.Context(cli))) == {
        "init",
        "env",
        "db",
        "run",
        "logs",
        "shell",
        "doctor",
        "eval",
        "exec",
        "module",
        "translations",
        "deps",
        "vscode",
        "postgres",
        "monitor",
        "test",
    }
