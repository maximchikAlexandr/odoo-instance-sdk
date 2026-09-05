from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.output import action_command
from odoo_instance_sdk.internal.pg.drop import DatabaseDropResult


def _command() -> object:
    return action_command(
        "database.drop",
        lambda: DatabaseDropResult(database="feature_db", cluster="127.0.0.1:5432"),
    )


@pytest.mark.unit
@pytest.mark.parametrize("machine_options", [("--json",), ("--format", "toon")])
def test_machine_drop_requires_yes_before_sdk_or_transport(
    machine_options: tuple[str, ...],
) -> None:
    args = ["db", "drop", "feature_db", *machine_options]
    with patch("odoo_instance_sdk.commands.pg._database_instance") as resolve:
        result = CliRunner().invoke(cli, args)

    assert result.exit_code == 1, result.output
    assert result.output.count("schema_version") == 1
    assert "confirmation_required" in result.output
    resolve.assert_not_called()


@pytest.mark.unit
def test_machine_drop_yes_executes_one_document_without_confirmation(
    project_manifest: Path,
) -> None:
    instance = SimpleNamespace(_postgres_cluster=SimpleNamespace(endpoint="127.0.0.1:5432"))
    command = _command()
    with (
        patch("odoo_instance_sdk.commands.pg._database_instance", return_value=(None, instance)),
        patch(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command",
            return_value=command,
        ) as build,
    ):
        result = CliRunner().invoke(
            cli,
            [
                "--project",
                str(project_manifest),
                "db",
                "drop",
                "feature_db",
                "--yes",
                "--json",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.output.count('"schema_version"') == 1
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["result"]["database"] == "feature_db"
    build.assert_called_once()


@pytest.mark.unit
def test_machine_drop_dry_run_does_not_require_yes_or_execute(project_manifest: Path) -> None:
    instance = SimpleNamespace(_postgres_cluster=SimpleNamespace(endpoint="127.0.0.1:5432"))
    command = _command()
    with (
        patch("odoo_instance_sdk.commands.pg._database_instance", return_value=(None, instance)),
        patch(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command",
            return_value=command,
        ),
    ):
        result = CliRunner().invoke(
            cli,
            [
                "--project",
                str(project_manifest),
                "db",
                "drop",
                "feature_db",
                "--dry-run",
                "--json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["result"]["steps"][0]["step_id"] == "database.drop"


@pytest.mark.unit
def test_drop_help_exposes_safety_controls() -> None:
    result = CliRunner().invoke(cli, ["db", "drop", "--help"])

    assert result.exit_code == 0, result.output
    for option in ("--force-default", "--force-connections", "--yes", "--dry-run"):
        assert option in result.output
