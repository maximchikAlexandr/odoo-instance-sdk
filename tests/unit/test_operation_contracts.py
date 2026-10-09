from __future__ import annotations

import json

import msgspec
import pytest

from odoo_instance_sdk.models import DepsMissingImport
from odoo_instance_sdk.operations import (
    OperationBinding,
    OperationDescriptor,
    OperationRequest,
    OperationResult,
    OperationTransport,
    assert_compatible_bundle,
    build_registry,
    contract_bundle,
    contract_bytes,
    provider_bindings,
    wire_projection,
    wire_schema,
)


def test_builtin_inventory_is_complete_and_aliases_share_identity() -> None:
    registry = build_registry()

    assert len(registry.bindings) == 62
    assert registry.for_path(("env", "create")) is registry.for_path(("env", "checkout"))
    assert registry.for_path(("env", "create")).operation_id == "odcli.env.create"


def test_schema_and_runtime_projection_use_wire_aliases() -> None:
    schema = wire_schema(DepsMissingImport)["$defs"]["DepsMissingImport"]

    assert "import" in schema["properties"]
    assert "import_name" not in schema["properties"]
    assert wire_projection(DepsMissingImport(module="base", import_name="odoo")) == {
        "module": "base",
        "import": "odoo",
    }


def test_contract_export_is_byte_stable_and_includes_schema_policy() -> None:
    first = contract_bytes()
    second = contract_bytes()
    bundle = json.loads(first)

    assert first == second
    assert bundle["contract_version"] == 1
    assert bundle["envelope"] == {"name": "OutputDocument", "schema_version": 1}
    assert bundle["operations"][0]["transport"] == OperationTransport.DOCUMENT


def test_registry_rejects_duplicate_ids_paths_and_incomplete_provider_bindings() -> None:
    descriptor = OperationDescriptor(
        operation_id="fixture.read",
        canonical_path=("fixture", "read"),
        request_type=OperationRequest,
        result_type=OperationResult,
    )
    complete = OperationBinding(descriptor, factory=lambda request: request)

    with pytest.raises(ValueError, match="duplicate operation id"):
        build_registry((complete, complete))

    with pytest.raises(ValueError, match="incomplete binding"):
        provider_bindings(
            type(
                "Provider",
                (),
                {"contract_version": 1, "provide": lambda self: (OperationBinding(descriptor),)},
            )()
        )


def test_same_contract_version_rejects_removed_operations() -> None:
    bundle = contract_bundle()
    reduced = {**bundle, "operations": bundle["operations"][1:]}

    with pytest.raises(ValueError, match="removed operations"):
        assert_compatible_bundle(bundle, reduced)


def test_request_dto_forbids_unknown_fields() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"unexpected": true}', type=OperationRequest)
