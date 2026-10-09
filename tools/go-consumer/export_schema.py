"""Project the authoritative Python contract bundle for Go generation."""

from __future__ import annotations

import json
from pathlib import Path

from odoo_instance_sdk.operations import builtin_bindings, contract_bundle, provider_bindings
from tests.fixtures.operation_provider import provider

_ROOT_TYPES = {
    "OperationRequest": "request",
    "OperationResult": "result",
    "FixtureError": "error",
    "DepsMissingImport": "alias",
    "WireOperationDocument": "wire",
    "FixturePluginPayload": "plugin",
}


def main() -> None:
    bindings = (*builtin_bindings(), *provider_bindings(provider))
    bundle = contract_bundle(bindings)
    definitions: dict[str, object] = {}
    for name in _ROOT_TYPES:
        schema = bundle["schemas"][name]
        definitions.update(schema.get("$defs", {}))

    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.com/odcli/contract-fixture.json",
        "title": "OperationContractFixture",
        "type": "object",
        "properties": {field: {"$ref": f"#/$defs/{name}"} for name, field in _ROOT_TYPES.items()},
        "required": list(_ROOT_TYPES.values()),
        "additionalProperties": False,
        "$defs": definitions,
    }
    output = Path(__file__).with_name("schema.json")
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
