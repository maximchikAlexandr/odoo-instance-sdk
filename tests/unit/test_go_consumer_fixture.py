from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, cast

from odoo_instance_sdk.operations import builtin_bindings, contract_bundle, provider_bindings
from tests.fixtures.operation_provider import provider


def test_go_fixture_keeps_authoritative_alias_and_plugin_shapes() -> None:
    path = Path("tools/go-consumer/schema.json")
    schema = json.loads(path.read_text(encoding="utf-8"))
    bundle = cast("Any", contract_bundle((*builtin_bindings(), *provider_bindings(provider))))

    assert (
        schema["$defs"]["DepsMissingImport"]
        == bundle["schemas"]["DepsMissingImport"]["$defs"]["DepsMissingImport"]
    )
    assert "import_name" not in schema["$defs"]["DepsMissingImport"]["properties"]
    assert (
        schema["$defs"]["FixtureResult"]
        == bundle["schemas"]["FixtureResult"]["$defs"]["FixtureResult"]
    )
    assert (
        schema["$defs"]["FixturePluginPayload"]
        == bundle["schemas"]["FixtureResult"]["$defs"]["FixturePluginPayload"]
    )


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
    gate = Path("tools/go-consumer/test.sh").read_text(encoding="utf-8")
    assert "go test ./..." in gate
