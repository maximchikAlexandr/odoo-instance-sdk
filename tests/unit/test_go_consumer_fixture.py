from __future__ import annotations

import json
import tomllib
from pathlib import Path

from odoo_instance_sdk.operations import contract_bundle


def test_go_fixture_keeps_authoritative_alias_and_plugin_shapes() -> None:
    path = Path("tools/go-consumer/schema.json")
    schema = json.loads(path.read_text(encoding="utf-8"))
    bundle = contract_bundle()

    assert (
        schema["$defs"]["DepsMissingImport"]["properties"]["import"]
        == bundle["schemas"]["DepsMissingImport"]["$defs"]["DepsMissingImport"]["properties"][
            "import"
        ]
    )
    assert "import_name" not in schema["$defs"]["DepsMissingImport"]["properties"]
    assert schema["$defs"]["FixturePluginPayload"] == {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def test_provider_fixture_uses_one_entry_point_and_python_runtime_has_no_go_dependency() -> None:
    fixture = tomllib.loads(
        Path("tests/fixtures/operation_provider/pyproject.toml").read_text(encoding="utf-8")
    )
    root = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert fixture["project"]["entry-points"]["odoo_instance_sdk.operations"] == {
        "fixture": "odcli_fixture_provider.provider"
    }
    assert "go-jsonschema" not in " ".join(root["project"]["dependencies"])
    assert "go" not in root["project"]["scripts"]
