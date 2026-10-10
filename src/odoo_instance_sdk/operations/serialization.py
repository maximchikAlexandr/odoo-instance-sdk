"""Wire projection and compatibility helpers for operation contracts."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, cast

import msgspec

from odoo_instance_sdk.execution import JsonValue

if TYPE_CHECKING:
    from .contracts import OperationBinding


def _type_schema(model: type[msgspec.Struct]) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", msgspec.json.schema(model, ref_template="#/$defs/{name}"))


def wire_projection(value: msgspec.Struct) -> JsonValue:
    """Return the same JSON-safe projection used by the runtime encoder."""
    return cast("JsonValue", msgspec.to_builtins(value))


def wire_schema(model: type[msgspec.Struct]) -> dict[str, JsonValue]:
    """Project one concrete DTO through msgspec's actual wire metadata."""
    return _type_schema(model)


def assert_compatible_bundle(  # noqa: C901
    previous: Mapping[str, JsonValue], current: Mapping[str, JsonValue]
) -> None:
    """Reject any public descriptor/schema drift unless the version advances."""
    if previous.get("contract_version") != current.get("contract_version"):
        return
    for key in ("entry_point_group", "envelope"):
        if previous.get(key) != current.get(key):
            raise ValueError(f"breaking contract change in bundle: {key}")
    if previous.get("schemas") != current.get("schemas"):
        raise ValueError("breaking contract change in bundle: schemas")

    def objects(value: JsonValue) -> tuple[dict[str, JsonValue], ...]:
        if not isinstance(value, list):
            return ()
        return tuple(item for item in value if isinstance(item, dict))

    old_operations: dict[str, dict[str, JsonValue]] = {}
    for item in objects(previous.get("operations")):
        operation_id = item.get("id")
        if isinstance(operation_id, str):
            old_operations[operation_id] = item
    new_operations: dict[str, dict[str, JsonValue]] = {}
    for item in objects(current.get("operations")):
        operation_id = item.get("id")
        if isinstance(operation_id, str):
            new_operations[operation_id] = item
    removed = sorted(set(old_operations) - set(new_operations))
    if removed:
        raise ValueError(f"breaking contract change removed operations: {removed!r}")
    for operation_id in sorted(old_operations):
        old = old_operations[operation_id]
        new = new_operations[operation_id]
        keys = set(old) | set(new)
        for key in sorted(keys):
            if old.get(key) != new.get(key):
                raise ValueError(f"breaking contract change in {operation_id}: {key}")


def assert_compatible_contract(
    previous: Mapping[str, JsonValue], current: Mapping[str, JsonValue]
) -> None:
    """Compatibility-fixture spelling retained for callers and tooling."""
    assert_compatible_bundle(previous, current)


def contract_bundle(bindings: Iterable[OperationBinding] | None = None) -> dict[str, JsonValue]:
    """Build a deterministic metadata-only contract bundle."""
    from .contracts import (
        _NO_DEFAULT,
        CONTRACT_VERSION,
        ENTRY_POINT_GROUP,
        OperationErrorDetails,
        OperationRequest,
        OperationResult,
        WireCreateValue,
        WireDeleteValue,
        WireNestedValue,
        WireOperationDocument,
        build_registry,
    )

    registry = build_registry(bindings)
    models: dict[str, type[msgspec.Struct]] = {
        "OperationRequest": OperationRequest,
        "OperationResult": OperationResult,
        "OperationErrorDetails": OperationErrorDetails,
        "WireCreateValue": WireCreateValue,
        "WireDeleteValue": WireDeleteValue,
        "WireNestedValue": WireNestedValue,
        "WireOperationDocument": WireOperationDocument,
    }
    from odoo_instance_sdk.models.deps import DepsMissingImport, DepsVerifyResult

    models.update(
        {
            "DepsMissingImport": DepsMissingImport,
            "DepsVerifyResult": DepsVerifyResult,
        }
    )
    for binding in registry.bindings:
        for model in (
            binding.descriptor.request_type,
            binding.descriptor.result_type,
            *binding.descriptor.error_types,
        ):
            if model is not None:
                models.setdefault(model.__name__, model)

    schemas: dict[str, JsonValue] = {name: _type_schema(models[name]) for name in sorted(models)}
    operations: list[dict[str, JsonValue]] = []
    for binding in registry.bindings:
        descriptor = binding.descriptor
        operations.append(
            {
                "id": descriptor.operation_id,
                "provider": descriptor.provider,
                "contract_version": descriptor.contract_version,
                "canonical_path": list(descriptor.canonical_path),
                "aliases": [list(path) for path in descriptor.aliases],
                "request_schema": (
                    descriptor.request_type.__name__
                    if descriptor.request_type is not None
                    else None
                ),
                "result_schema": (
                    descriptor.result_type.__name__ if descriptor.result_type is not None else None
                ),
                "error_schemas": [model.__name__ for model in descriptor.error_types],
                "parameters": [
                    {
                        "name": item.name,
                        "schema": item.schema,
                        "required": item.required,
                        **(
                            {}
                            if item.default is _NO_DEFAULT
                            else {"default": cast("JsonValue", item.default)}
                        ),
                    }
                    for item in descriptor.parameters
                ],
                "context_policy": descriptor.context_policy,
                "transport": descriptor.transport.value,
                "preview": descriptor.preview,
                "approval_required": descriptor.approval_required,
                "cancellation": descriptor.cancellation,
                "exit_mapping": descriptor.exit_mapping,
                "domain_status_field": descriptor.domain_status_field,
                "implementation": (
                    {"kind": "sdk-primitive", "reference": binding.sdk_primitive}
                    if binding.sdk_primitive is not None
                    else {"kind": "click-callback", "path": list(descriptor.canonical_path)}
                ),
            }
        )
    return cast(
        "dict[str, JsonValue]",
        {
            "contract_version": CONTRACT_VERSION,
            "entry_point_group": ENTRY_POINT_GROUP,
            "envelope": {
                "name": "OutputDocument",
                "schema_version": 1,
                "wire_fixture": "WireOperationDocument",
            },
            "operations": operations,
            "schemas": schemas,
        },
    )


def contract_bytes(bindings: Iterable[OperationBinding] | None = None) -> bytes:
    return json.dumps(
        contract_bundle(bindings),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = [
    "assert_compatible_bundle",
    "assert_compatible_contract",
    "contract_bundle",
    "contract_bytes",
    "wire_projection",
    "wire_schema",
]
