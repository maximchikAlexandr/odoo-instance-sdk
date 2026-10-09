"""Installed-package entry point for the fixture provider."""

from __future__ import annotations

import msgspec

from odoo_instance_sdk.operations import (
    CONTRACT_VERSION,
    OperationBinding,
    OperationDescriptor,
    OperationTransport,
)


class FixtureRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    name: str


class FixturePluginPayload(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    value: str


class FixtureResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    status: str
    greeting: str
    payload: FixturePluginPayload | None = None


class FixtureError(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    code: str
    message: str


def invoke(request: FixtureRequest) -> FixtureResult:
    return FixtureResult(status="ok", greeting=f"hello {request.name}")


operation = OperationBinding(
    descriptor=OperationDescriptor(
        operation_id="fixture.greeting",
        canonical_path=("fixture", "greeting"),
        provider="fixture-provider",
        request_type=FixtureRequest,
        result_type=FixtureResult,
        error_types=(FixtureError,),
        transport=OperationTransport.DOCUMENT,
    ),
    factory=invoke,
)

contract_version = CONTRACT_VERSION


def provide() -> tuple[OperationBinding, ...]:
    return (operation,)
