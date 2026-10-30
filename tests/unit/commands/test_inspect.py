from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.models import OdooModelInfo


def _decode_toon(value: str) -> object:
    from toon import DecodeOptions, decode

    return decode(value, DecodeOptions(indent=2, strict=True))


def test_inspect_model_rich_json_and_toon_share_typed_facts() -> None:
    inspector = MagicMock()
    inspector.model_command.return_value = Command.create(
        ExecutionPlan(),
        lambda _context: OdooModelInfo(
            name="res.partner",
            exists=False,
            description=None,
            table=None,
            abstract=None,
            transient=None,
            auto=None,
            field_count=None,
        ),
    )
    instance = SimpleNamespace(inspector=inspector)
    with patch(
        "odoo_instance_sdk.commands.inspect.cli_context.ready_instance",
        return_value=SimpleNamespace(instance=instance),
    ):
        rich = CliRunner().invoke(cli, ["inspect", "model", "res.partner"], env={"COLUMNS": "160"})
        json_result = CliRunner().invoke(
            cli, ["inspect", "model", "res.partner", "--format", "json"]
        )
        toon_result = CliRunner().invoke(
            cli, ["inspect", "model", "res.partner", "--format", "toon"]
        )

    assert rich.exit_code == 0, rich.output
    assert "res.partner" in rich.stdout and "False" in rich.stdout
    assert json_result.exit_code == toon_result.exit_code == 0
    json_document = json.loads(json_result.stdout)
    toon_document = _decode_toon(toon_result.stdout)
    assert json_document == toon_document
    assert json_document["result"] == {
        "name": "res.partner",
        "exists": False,
        "description": None,
        "table": None,
        "abstract": None,
        "transient": None,
        "auto": None,
        "field_count": None,
    }
    assert inspector.model_command.call_count == 3
    inspector.field_command.assert_not_called()
    inspector.ref_command.assert_not_called()


def test_inspect_help_has_no_dry_run_or_command_local_environment() -> None:
    result = CliRunner().invoke(cli, ["inspect", "field", "--help"])

    assert result.exit_code == 0, result.output
    assert "--dry-run" not in result.output
    assert "--env" not in result.output
    assert "--format" in result.output
