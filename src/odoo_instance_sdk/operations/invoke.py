"""Local, finite operation invocation over one captured context."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import msgspec

from odoo_instance_sdk.commands.context import OperationContext
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    failure_document,
    sanitize_diagnostic,
    success_document,
)
from odoo_instance_sdk.execution import Command, JsonValue

from .contracts import OperationBinding, OperationRegistry, OperationTransport, build_registry


class OperationInvokeError(RuntimeError):
    """A bounded, sanitized failure at the local machine boundary."""

    def __init__(self, code: str, message: str, *, details: JsonValue | None = None) -> None:
        self.code = code
        self.details = details
        super().__init__(sanitize_diagnostic(message))


@dataclass(frozen=True, slots=True)
class LocalInvocation:
    """The envelope and exit status from one local finite invocation."""

    document: OutputDocument
    exit_code: int
    value: JsonValue | None = None


def decode_request(
    binding: OperationBinding, payload: Mapping[str, JsonValue] | None
) -> msgspec.Struct | None:
    """Decode one strict request DTO, applying only declared defaults."""

    request_type = binding.descriptor.request_type
    if request_type is None:
        if payload:
            raise OperationInvokeError(
                "request_not_supported", "this operation does not accept a finite request"
            )
        return None
    raw: Mapping[str, JsonValue] = {} if payload is None else payload
    try:
        return msgspec.convert(raw, type=request_type, strict=True)
    except (TypeError, ValueError, msgspec.ValidationError) as error:
        raise OperationInvokeError("invalid_request", str(error)) from error


def _json_value(value: object) -> JsonValue:
    if isinstance(value, msgspec.Struct):
        return cast("JsonValue", msgspec.to_builtins(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return sanitize_diagnostic(repr(value))


def _context_projection(context: OperationContext) -> dict[str, JsonValue]:
    projection = context.public_projection()
    return cast("dict[str, JsonValue]", _json_value(projection))


def build_operation_command(
    binding: OperationBinding,
    request: msgspec.Struct | None,
    context: OperationContext,
) -> Command[object]:
    """Build a private command once for a binding that requires execution."""

    factory = binding.factory
    if factory is None:
        raise OperationInvokeError(
            "operation_unavailable",
            f"operation {binding.operation_id} has no local factory",
        )
    try:
        command = factory(request, context)
    except OperationInvokeError:
        raise
    except Exception as error:
        raise OperationInvokeError("operation_build_failed", str(error)) from error
    if not isinstance(command, Command):
        raise OperationInvokeError(
            "operation_not_command", f"operation {binding.operation_id} did not return a Command"
        )
    return command


def _call_factory(
    binding: OperationBinding,
    request: msgspec.Struct | None,
    context: OperationContext,
) -> object:
    factory = binding.factory
    if factory is None:
        raise OperationInvokeError(
            "operation_unavailable",
            f"operation {binding.operation_id} has no local factory",
        )
    try:
        return factory(request, context)
    except OperationInvokeError:
        raise
    except Exception as error:
        raise OperationInvokeError("operation_build_failed", str(error)) from error


def _ensure_transport(binding: OperationBinding) -> None:
    transport = binding.descriptor.transport
    if transport is not OperationTransport.DOCUMENT:
        raise OperationInvokeError(
            "transport_required",
            f"operation {binding.operation_id} requires {transport.value}; use its direct CLI transport",
        )
    if binding.descriptor.approval_required:
        raise OperationInvokeError(
            "session_transport_required",
            "approval-required operations must use the bounded JSONL session transport",
        )


def invoke_local(
    operation_id: str,
    payload: Mapping[str, JsonValue] | None,
    context: OperationContext,
    *,
    registry: OperationRegistry | None = None,
    max_output_bytes: int = 1_048_576,
) -> LocalInvocation:
    """Invoke one finite local operation without dispatch or coordinator code."""

    active_registry = registry or build_registry()
    try:
        binding = active_registry.get(operation_id)
    except KeyError as error:
        raise OperationInvokeError("operation_unavailable", str(error)) from error
    _ensure_transport(binding)
    request = decode_request(binding, payload)
    if binding.factory is None:
        raise OperationInvokeError(
            "operation_unavailable", f"operation {operation_id} is not locally installed"
        )
    value_or_command = _call_factory(binding, request, context)
    try:
        value = _json_value(
            value_or_command.run() if isinstance(value_or_command, Command) else value_or_command
        )
    except KeyboardInterrupt as error:
        raise OperationInvokeError("cancelled", "operation interrupted") from error
    except Exception as error:
        raise OperationInvokeError("operation_failed", str(error)) from error
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_output_bytes:
        raise OperationInvokeError(
            "bounded_output_exceeded", "operation result exceeds output limit"
        )
    document = success_document(
        command=operation_id,
        result=value if isinstance(value, dict) else {"value": value},
        context=_context_projection(context),
        provenance={"source": context.provenance},
    )
    return LocalInvocation(document=document, exit_code=0, value=value)


def failure_invocation(
    error: OperationInvokeError, context: OperationContext, operation_id: str
) -> LocalInvocation:
    """Project a local boundary error through the existing envelope-v1."""

    document = failure_document(
        command=operation_id,
        context=_context_projection(context),
        provenance={"source": context.provenance},
        dry_run=False,
        error_code=error.code,
        error_message=str(error),
        error_details=error.details if isinstance(error.details, dict) else None,
    )
    return LocalInvocation(document=document, exit_code=1)


def invoke_with_context(
    operation_id: str,
    payload: Mapping[str, JsonValue] | None,
    context: OperationContext,
    *,
    registry: OperationRegistry | None = None,
) -> LocalInvocation:
    """Return a failure envelope instead of leaking an invocation exception."""

    try:
        return invoke_local(operation_id, payload, context, registry=registry)
    except OperationInvokeError as error:
        return failure_invocation(error, context, operation_id)


__all__ = [
    "LocalInvocation",
    "OperationInvokeError",
    "build_operation_command",
    "decode_request",
    "failure_invocation",
    "invoke_local",
    "invoke_with_context",
]
