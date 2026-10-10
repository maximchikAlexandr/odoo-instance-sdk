from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import click
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli

if TYPE_CHECKING:
    import pytest


def test_top_level_click_surface_exposes_exactly_all_required_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert set(cli.list_commands(click.Context(cli))) == {
        "init",
        "env",
        "backup",
        "bug-report",
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
        "psql",
        "monitor",
        "ps",
        "test",
        "resource",
        "git",
        "stop",
        "update",
        "remote",
        "contract",
        "operation",
    }


def test_contract_export_is_project_free_and_has_no_domain_io(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail(*_: object, **__: object) -> None:
        raise AssertionError("contract export attempted domain I/O")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", fail)
    result = CliRunner().invoke(cli, ["contract", "export", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["contract_version"] == 1
    assert len(payload["operations"]) == 65


def test_contract_export_validates_composed_click_tree_before_serializing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import odoo_instance_sdk.commands.contract as contract_commands

    class InvalidRegistry:
        bindings = ()

        def validate_click_tree(self, _command: object) -> None:
            raise ValueError("unknown Click leaf")

    monkeypatch.setattr(contract_commands, "build_registry", lambda _bindings: InvalidRegistry())
    result = CliRunner().invoke(cli, ["contract", "export"])

    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)
    assert "unknown Click leaf" in str(result.exception)


def test_contract_export_serializes_a_valid_installed_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import odoo_instance_sdk.commands.contract as contract_commands
    from tests.fixtures.operation_provider.provider import operation

    monkeypatch.setattr(contract_commands, "discovered_bindings", lambda: (operation,))
    result = CliRunner().invoke(cli, ["contract", "export"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert any(row["id"] == "fixture.greeting" for row in payload["operations"])
