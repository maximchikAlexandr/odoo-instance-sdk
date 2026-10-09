"""Explicitly local machine-operation CLI entrypoints."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import rich_click as click
from click.exceptions import Exit

from odoo_instance_sdk.commands.context import (
    CliContext,
    OperationContext,
    capture_operation_context,
    pass_cli_context,
)
from odoo_instance_sdk.commands.output import OutputMode, emit, failure_document
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.operations import build_registry
from odoo_instance_sdk.operations.invoke import (
    OperationInvokeError,
    build_operation_command,
    decode_request,
    invoke_with_context,
)
from odoo_instance_sdk.operations.session import run_approval_session, session_error_record


def _request_payload(raw: str) -> Mapping[str, JsonValue]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise click.BadParameter(f"request must be JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise click.BadParameter("request must be a JSON object")
    return cast("Mapping[str, JsonValue]", payload)


def _failure(operation_id: str, error: OperationInvokeError) -> None:
    document = failure_document(
        command=operation_id,
        dry_run=False,
        error_code=error.code,
        error_message=str(error),
        error_details=error.details if isinstance(error.details, dict) else None,
    )
    raise Exit(emit(document, OutputMode.JSON))


def _validate_document_binding(operation_id: str, binding: object) -> None:
    descriptor = cast("object", getattr(binding, "descriptor"))
    transport = getattr(descriptor, "transport")
    if transport.value != "finite-document":
        raise OperationInvokeError(
            "transport_required",
            f"operation {operation_id} requires {transport.value}; use its direct CLI transport",
        )
    if getattr(descriptor, "approval_required"):
        raise OperationInvokeError(
            "session_transport_required",
            "approval-required operations must use the bounded JSONL session transport",
        )


def _validate_session_binding(operation_id: str, binding: object) -> None:
    descriptor = cast("object", getattr(binding, "descriptor"))
    transport = getattr(descriptor, "transport")
    if transport.value != "finite-document":
        raise OperationInvokeError(
            "transport_required",
            f"operation {operation_id} requires {transport.value}; use its direct CLI transport",
        )
    if not getattr(descriptor, "approval_required") or not getattr(descriptor, "preview"):
        raise OperationInvokeError(
            "session_transport_required",
            "the bounded JSONL session transport is only available for approval-required operations",
        )


def _session_failure(operation_id: str, error: OperationInvokeError) -> None:
    click.echo(
        json.dumps(
            session_error_record(operation_id, error.code, str(error)),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    raise Exit(1)


@click.group("operation", help="Invoke one installed operation locally.")
def operation_group() -> None:
    """Machine operations never dispatch to a daemon or coordinator."""


@operation_group.command("invoke")
@click.argument("operation_id")
@click.option("--request", "request_json", default="{}", help="Finite request as a JSON object.")
@pass_cli_context
def invoke_operation(ctx: CliContext, operation_id: str, request_json: str) -> None:
    """Invoke a finite operation through the local Python installation."""

    registry = build_registry()
    try:
        binding = registry.get(operation_id)
        _validate_document_binding(operation_id, binding)
        payload = _request_payload(request_json)
        context = (
            capture_operation_context(ctx)
            if binding.canonical_path != ("contract", "export")
            else OperationContext(cwd=Path.cwd())
        )
    except KeyError as error:
        _failure(operation_id, OperationInvokeError("operation_unavailable", str(error)))
    except OperationInvokeError as error:
        _failure(operation_id, error)
    invocation = invoke_with_context(operation_id, payload, context, registry=registry)
    raise Exit(emit(invocation.document, OutputMode.JSON))


@operation_group.command("session")
@click.argument("operation_id")
@click.option("--request", "request_json", default="{}", help="Finite request as a JSON object.")
@pass_cli_context
def session_operation(ctx: CliContext, operation_id: str, request_json: str) -> None:
    """Preview and approve one command in a bounded JSONL session."""

    registry = build_registry()
    try:
        binding = registry.get(operation_id)
        _validate_session_binding(operation_id, binding)
        payload = _request_payload(request_json)
        context = capture_operation_context(ctx)
        request = decode_request(binding, payload)
        outcome = run_approval_session(
            operation_id,
            lambda: build_operation_command(binding, request, context),
            click.get_text_stream("stdin"),
            click.get_text_stream("stdout"),
        )
    except KeyError as error:
        _session_failure(operation_id, OperationInvokeError("operation_unavailable", str(error)))
    except click.BadParameter as error:
        _session_failure(operation_id, OperationInvokeError("invalid_request", str(error)))
    except OperationInvokeError as error:
        _session_failure(operation_id, error)
    except Exception as error:
        _session_failure(operation_id, OperationInvokeError("operation_failed", str(error)))
    raise Exit(outcome.exit_code)


__all__ = ["invoke_operation", "operation_group", "session_operation"]
