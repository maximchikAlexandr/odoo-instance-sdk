"""Project the authoritative Python contract bundle for Go generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

_ROOT_TYPES = {
    "OperationRequest": "request",
    "FixtureResult": "result",
    "FixtureError": "error",
    "DepsMissingImport": "alias",
    "WireOperationDocument": "wire",
    "FixturePluginPayload": "plugin",
}


def main() -> None:
    from odoo_instance_sdk.operations import builtin_bindings, contract_bundle, provider_bindings
    from tests.fixtures.operation_provider import provider

    bindings = (*builtin_bindings(), *provider_bindings(provider))
    bundle = contract_bundle(bindings)
    definitions: dict[str, object] = {}
    for name in _ROOT_TYPES:
        schema = bundle["schemas"].get(name)
        if schema is not None:
            definitions.update(schema.get("$defs", {}))
            continue
        for candidate in bundle["schemas"].values():
            if name in candidate.get("$defs", {}):
                definitions[name] = candidate["$defs"][name]
                break
        else:
            raise KeyError(name)

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
