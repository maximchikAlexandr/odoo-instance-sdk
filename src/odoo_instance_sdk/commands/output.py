"""Output primitives owned by the CLI boundary."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from enum import StrEnum
from typing import Generic, Never, Protocol, TypeVar, cast, overload

import click
import msgspec
from rich.console import Console
from toon import encode

from odoo_instance_sdk.internal.database_preparation import DatabasePreparationFailureContext
from odoo_instance_sdk.internal.sanitize import sanitize_last_error, sanitize_terminal_text


class OutputMode(StrEnum):
    """Output modes understood by the CLI composition layer."""

    RICH = "rich"
    JSON = "json"
    TOON = "toon"


type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type DiagnosticValue = str | BaseException


class OutputError(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """The stable, machine-readable error part of a CLI document."""

    code: str
    message: str


class OutputDocument(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
    omit_defaults=True,
):
    """CLI-private immutable v1 document shared by every bounded transport."""

    schema_version: int
    ok: bool
    command: str
    context: JsonValue
    provenance: JsonValue
    dry_run: bool
    warnings: tuple[str, ...]
    # Optional fields are omitted by msgspec when they do not belong to the
    # success/failure variant.  This preserves the existing v1 envelope shape
    # while keeping construction and transport projection typed.
    result: JsonValue = msgspec.field(default=None)
    data: JsonValue = msgspec.field(default=None)
    error: OutputError | None = msgspec.field(default=None)


_ResultT = TypeVar("_ResultT")
_ResultT_co = TypeVar("_ResultT_co", covariant=True)


class _PlannedCommand(Protocol):
    @property
    def plan(self) -> msgspec.Struct: ...


class _InspectableCommand(Protocol, Generic[_ResultT_co]):
    @property
    def plan(self) -> msgspec.Struct: ...

    def run(self) -> _ResultT_co: ...


@overload
def output_options(command: click.Command) -> click.Command: ...


@overload
def output_options(command: Callable[..., None]) -> Callable[..., None]: ...


def output_options(
    command: click.Command | Callable[..., None],
) -> click.Command | Callable[..., None]:
    """Add the bounded command's local document-format options."""
    decorated = click.option(
        "--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope."
    )(command)
    return click.option(
        "--format",
        "output_format",
        type=click.Choice([mode.value for mode in OutputMode], case_sensitive=True),
        default=None,
        help="Output format (default: rich).",
    )(decorated)


def command_options(command: Callable[..., None]) -> Callable[..., None]:
    """Add format aliases and the local preview switch to a spawning leaf."""
    return click.option(
        "--dry-run", "dry_run", is_flag=True, default=False, help="Inspect without starting."
    )(output_options(command))


def resolve_output_mode(output_format: str | None, json_output: bool) -> OutputMode:
    """Resolve a command-local format and reject ambiguous alias combinations."""
    if json_output and output_format not in {None, OutputMode.JSON.value}:
        raise click.UsageError("--json conflicts with --format unless --format json is used")
    if output_format is not None:
        return OutputMode(output_format)
    return OutputMode.JSON if json_output else OutputMode.RICH


def resolve_command_options(
    output_format: str | None,
    json_output: bool,
    dry_run: bool,
    *,
    command: str,
) -> OutputMode:
    """Resolve output aliases and enforce preview-only raw-stream formats."""
    if not dry_run and (output_format is not None or json_output):
        raise click.UsageError(f"--format/--json require --dry-run for raw-stream {command}")
    return resolve_output_mode(output_format, json_output)


def sanitize_diagnostic(value: DiagnosticValue) -> str:
    """Make every non-interactive diagnostic safe and bounded before emission."""
    return sanitize_last_error(str(value)) or "operation failed"


def model_to_dict(value: msgspec.Struct) -> JsonObject:
    """Project one public typed model into the shared envelope mapping."""
    builtins = msgspec.to_builtins(value)
    if not isinstance(builtins, dict):
        raise TypeError("CLI model result must be an object")
    return cast("JsonObject", builtins)


def _failure_context(error: BaseException | None) -> JsonObject:
    """Project only the typed, secret-free retained-artifact context."""
    context = getattr(error, "failure_context", None) if error is not None else None
    if not isinstance(context, DatabasePreparationFailureContext):
        return {}
    return model_to_dict(context)


def _failure_message(message: DiagnosticValue, context: JsonObject) -> str:
    rendered = sanitize_diagnostic(message)
    details: list[str] = []
    if context.get("retained_backup_id") is not None:
        details.append(f"retained backup {context['retained_backup_id']}")
    if context.get("retained_database") is not None:
        details.append(f"retained database {context['retained_database']}")
    return f"{rendered}; {'; '.join(details)}" if details else rendered


def rich_print(
    value: str,
    *,
    end: str = "\n",
    preserve_newlines: bool = False,
) -> None:
    """Print human output safely, optionally preserving document line feeds."""
    rendered = sanitize_terminal_text(str(value), preserve_newlines=preserve_newlines)
    Console().print(rendered, markup=False, soft_wrap=True, end=end)


def _sanitize_envelope_value(value: JsonValue) -> JsonValue:
    """Recursively make machine-envelope values inert for terminal transports."""
    if isinstance(value, str):
        return sanitize_terminal_text(value)
    if isinstance(value, dict):
        return {
            sanitize_terminal_text(key): _sanitize_envelope_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_envelope_value(item) for item in value]
    return value


def _document_payload(document: OutputDocument) -> JsonObject:
    """Build the exact v1 envelope projection for one immutable document."""
    payload: JsonObject = {
        "schema_version": document.schema_version,
        "ok": document.ok,
        "command": document.command,
        "context": document.context,
        "provenance": document.provenance,
        "dry_run": document.dry_run,
        "warnings": list(document.warnings),
    }
    if document.ok:
        # ``result`` and ``data`` are intentionally equal in every success
        # document, including an explicit JSON null result.
        payload["result"] = document.result
        payload["data"] = document.data
    elif document.error is not None:
        payload["error"] = {
            "code": document.error.code,
            "message": document.error.message,
        }
    return cast("JsonObject", _sanitize_envelope_value(payload))


def _document(
    *,
    ok: bool,
    command: str,
    result: JsonObject | None = None,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    dry_run: bool = False,
    warnings: tuple[str, ...] = (),
    error_code: str | None = None,
    error_message: DiagnosticValue | None = None,
) -> OutputDocument:
    safe_result = _sanitize_envelope_value(result or {}) if ok else None
    message = (
        sanitize_diagnostic(error_message) if error_message is not None else "operation failed"
    )
    error = (
        None
        if ok
        else OutputError(
            code=error_code or command.replace(".", "_") + "_failed",
            message=message,
        )
    )
    return OutputDocument(
        schema_version=1,
        ok=ok,
        command=command,
        context=_sanitize_envelope_value(context or {}),
        provenance=_sanitize_envelope_value(provenance or {}),
        dry_run=dry_run,
        warnings=warnings,
        result=safe_result,
        data=safe_result,
        error=error,
    )


def _default_rich_projection(document: OutputDocument) -> str:
    """Return a human projection without owning a terminal or output mode."""
    if not document.ok and document.error is not None:
        return document.error.message
    if document.result in (None, {}):
        return ""
    return json.dumps(document.result, ensure_ascii=False, default=str, indent=2)


def emit(
    document: OutputDocument,
    mode: OutputMode,
    *,
    rich: Callable[[OutputDocument], str] | None = None,
    diagnostic: str | None = None,
) -> int:
    """Emit one immutable document and return its normal CLI exit status."""
    payload = _document_payload(document)
    if mode is OutputMode.JSON:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    elif mode is OutputMode.TOON:
        click.echo(encode(payload))
    elif document.ok:
        rendered = (rich or _default_rich_projection)(document)
        if rendered:
            rich_print(rendered, preserve_newlines=True)
    else:
        rendered = (rich or _default_rich_projection)(document)
        click.echo(sanitize_diagnostic(rendered), err=True)
    if diagnostic:
        click.echo(sanitize_diagnostic(diagnostic), err=True)
    return 0 if document.ok else 1


def success_document(
    *,
    command: str,
    result: JsonObject | None = None,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    dry_run: bool = False,
    warnings: tuple[str, ...] = (),
) -> OutputDocument:
    """Construct a typed successful v1 document for the shared emitter."""
    return _document(
        ok=True,
        command=command,
        result=result,
        context=context,
        provenance=provenance,
        dry_run=dry_run,
        warnings=warnings,
    )


def failure_document(
    *,
    command: str,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    dry_run: bool = False,
    warnings: tuple[str, ...] = (),
    error_code: str | None = None,
    error_message: DiagnosticValue | None = None,
) -> OutputDocument:
    """Construct a typed failed v1 document for the shared emitter."""
    return _document(
        ok=False,
        command=command,
        context=context,
        provenance=provenance,
        dry_run=dry_run,
        warnings=warnings,
        error_code=error_code,
        error_message=error_message,
    )


def emit_command_plan(
    command: _PlannedCommand,
    *,
    command_name: str,
    mode: OutputMode,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    rich: Callable[[OutputDocument], str] | None = None,
) -> int:
    """Emit the inspected plan of an already-built command without running it."""
    return emit(
        success_document(
            command=command_name,
            result=model_to_dict(command.plan),
            context=context,
            provenance=provenance,
            dry_run=True,
        ),
        mode,
        rich=rich,
    )


def run_or_preview(
    build_command: Callable[[], _InspectableCommand[_ResultT]],
    *,
    command_name: str,
    mode: OutputMode,
    dry_run: bool,
    result: Callable[[_ResultT | None], JsonObject] | None = None,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    confirm: Callable[[], None] | None = None,
    rich: Callable[[OutputDocument], str] | None = None,
) -> tuple[int, _ResultT | None]:
    """Build one command, then either inspect it or run that same instance.

    Confirmation is deliberately invoked after construction, so a caller can
    present or validate the complete snapshot before asking for consent.
    """
    command = build_command()
    if dry_run:
        return (
            emit_command_plan(
                command,
                command_name=command_name,
                mode=mode,
                context=context,
                provenance=provenance,
                rich=rich,
            ),
            None,
        )
    if confirm is not None:
        confirm()
    value = command.run()
    payload = result(value) if result is not None else {}
    status = emit(
        success_document(
            command=command_name,
            result=payload,
            context=context,
            provenance=provenance,
        ),
        mode,
        rich=rich,
    )
    return status, value


def build_envelope(
    *,
    ok: bool,
    command: str,
    result: JsonObject | None = None,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    dry_run: bool = False,
    error_code: str | None = None,
    error_message: DiagnosticValue | None = None,
) -> JsonObject:
    """Build the existing v1 envelope without selecting an output transport."""
    return _document_payload(
        success_document(
            command=command,
            result=result,
            context=context,
            provenance=provenance,
            dry_run=dry_run,
        )
        if ok
        else failure_document(
            command=command,
            context=context,
            provenance=provenance,
            dry_run=dry_run,
            error_code=error_code,
            error_message=error_message,
        )
    )


def emit_json_envelope(
    *,
    ok: bool,
    command: str,
    result: JsonObject | None = None,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    dry_run: bool = False,
    error_code: str | None = None,
    error_message: DiagnosticValue | None = None,
    mode: OutputMode = OutputMode.JSON,
) -> None:
    """Emit the v1 envelope as exactly one JSON or TOON stdout document."""
    emit(
        success_document(
            command=command,
            result=result,
            context=context,
            provenance=provenance,
            dry_run=dry_run,
        )
        if ok
        else failure_document(
            command=command,
            context=context,
            provenance=provenance,
            dry_run=dry_run,
            error_code=error_code,
            error_message=error_message,
        ),
        mode,
    )


def fail(
    output_mode: OutputMode | bool,
    command: str,
    message: DiagnosticValue,
    *,
    usage: bool = False,
) -> Never:
    mode = (
        output_mode
        if isinstance(output_mode, OutputMode)
        else OutputMode.JSON
        if output_mode
        else OutputMode.RICH
    )
    context = _failure_context(message if isinstance(message, BaseException) else None)
    rendered_message = _failure_message(message, context)
    if mode is not OutputMode.RICH:
        emit(
            failure_document(
                command=command,
                context=context,
                error_code="usage_error" if usage else command.replace(".", "_") + "_failed",
                error_message=rendered_message,
            ),
            mode,
        )
    else:
        emit(
            failure_document(command=command, error_message=rendered_message),
            mode,
        )
    if usage:
        raise click.exceptions.Exit(2)
    sys.exit(1)


__all__ = [
    "JsonValue",
    "OutputDocument",
    "OutputError",
    "OutputMode",
    "build_envelope",
    "command_options",
    "emit",
    "emit_command_plan",
    "emit_json_envelope",
    "fail",
    "failure_document",
    "model_to_dict",
    "output_options",
    "resolve_command_options",
    "resolve_output_mode",
    "rich_print",
    "run_or_preview",
    "sanitize_diagnostic",
    "sanitize_terminal_text",
    "success_document",
]
