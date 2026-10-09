from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import msgspec
import pytest

from odoo_instance_sdk.models import DepsMissingImport
from odoo_instance_sdk.operations import (
    OperationBinding,
    OperationDescriptor,
    OperationErrorDetails,
    OperationRequest,
    OperationResult,
    OperationTransport,
    WireCreateValue,
    WireDeleteValue,
    WireNestedValue,
    WireOperationDocument,
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

    assert len(registry.bindings) == 63
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
    assert bundle["envelope"] == {
        "name": "OutputDocument",
        "schema_version": 1,
        "wire_fixture": "WireOperationDocument",
    }
    assert bundle["operations"][0]["transport"] == OperationTransport.DOCUMENT
    assert bundle["operations"][0]["implementation"]["kind"] == "sdk-primitive"


def test_builtin_bindings_have_concrete_finite_types_and_parameters() -> None:
    bindings = build_registry().bindings

    assert len({binding.descriptor.request_type for binding in bindings}) == len(bindings)
    assert len({binding.descriptor.result_type for binding in bindings}) == len(bindings)
    assert all(binding.descriptor.request_type is not OperationRequest for binding in bindings)
    assert all(binding.descriptor.result_type is not OperationResult for binding in bindings)
    assert all(binding.descriptor.error_types != (OperationErrorDetails,) for binding in bindings)
    assert all(binding.descriptor.parameters for binding in bindings)
    assert all(
        binding.sdk_primitive is not None or binding.click_path == binding.canonical_path
        for binding in bindings
    )
    assert all("cli." not in (binding.sdk_primitive or "") for binding in bindings)


def test_composed_click_tree_checks_canonical_paths_and_aliases() -> None:
    import odoo_instance_sdk.commands.cli_parts.callbacks  # noqa: F401
    from odoo_instance_sdk.cli import cli

    build_registry().validate_click_tree(cli)


def test_nested_tagged_default_null_and_alias_wire_fixture_is_real_json() -> None:
    value = WireOperationDocument(
        nested=WireNestedValue(label="nested", enabled=None),
        action=WireCreateValue(name="new", count=0),
        alias="list",
    )

    payload = msgspec.json.encode(value)
    assert json.loads(payload) == {
        "nested": {"label": "nested", "enabled": None},
        "action": {"name": "new", "count": 0, "type": "create"},
        "alias": "list",
    }
    decoded = msgspec.json.decode(payload, type=WireOperationDocument)
    assert decoded == value
    schema = wire_schema(WireOperationDocument)
    assert "anyOf" in schema["$defs"]["WireOperationDocument"]["properties"]["action"]
    assert schema["$defs"]["WireNestedValue"]["properties"]["enabled"]["anyOf"][-1] == {
        "type": "null"
    }
    assert WireDeleteValue(name="old")


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


@pytest.mark.parametrize(
    ("surface", "expected"),
    (("parameters", "parameters"), ("envelope", "bundle: envelope"), ("schemas", "schemas")),
)
def test_same_contract_version_rejects_every_public_surface_drift(
    surface: str, expected: str
) -> None:
    bundle = contract_bundle()
    changed = deepcopy(bundle)
    if surface == "parameters":
        changed["operations"][0]["parameters"][0]["default"] = "changed"
    elif surface == "envelope":
        changed["envelope"]["schema_version"] = 2
    else:
        changed["schemas"]["OperationRequest"]["title"] = "Changed"

    with pytest.raises(ValueError, match=expected):
        assert_compatible_bundle(bundle, changed)


def test_provider_conflict_and_incomplete_binding_fail_before_factory_execution() -> None:
    core = build_registry().bindings[0]
    called = False

    def factory(_: object) -> object:
        nonlocal called
        called = True
        return None

    conflicting = OperationBinding(replace(core.descriptor, provider="fixture"), factory=factory)
    provider = type(
        "Provider",
        (),
        {"contract_version": 1, "provide": lambda self: (conflicting,)},
    )()

    with pytest.raises(ValueError, match="duplicate operation id"):
        build_registry(providers=(provider,))
    assert called is False

    incomplete = type(
        "Provider",
        (),
        {"contract_version": 1, "provide": lambda self: (OperationBinding(core.descriptor),)},
    )()
    with pytest.raises(ValueError, match="incomplete binding"):
        provider_bindings(incomplete)


def test_request_dto_forbids_unknown_fields() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"unexpected": true}', type=OperationRequest)
