"""Output primitives owned by the CLI boundary."""

from __future__ import annotations

import json
import sys
from enum import StrEnum
from typing import Any, Never, cast

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


def output_options(command: Any) -> Any:
    """Add the bounded command's local document-format options."""
    command = click.option(
        "--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope."
    )(command)
    return click.option(
        "--format",
        "output_format",
        type=click.Choice([mode.value for mode in OutputMode], case_sensitive=True),
        default=None,
        help="Output format (default: rich).",
    )(command)


def resolve_output_mode(output_format: str | None, json_output: bool) -> OutputMode:
    """Resolve a command-local format and reject ambiguous alias combinations."""
    if json_output and output_format not in {None, OutputMode.JSON.value}:
        raise click.UsageError("--json conflicts with --format unless --format json is used")
    if output_format is not None:
        return OutputMode(output_format)
    return OutputMode.JSON if json_output else OutputMode.RICH


def sanitize_diagnostic(value: object) -> str:
    """Make every non-interactive diagnostic safe and bounded before emission."""
    return sanitize_last_error(str(value)) or "operation failed"


def model_to_dict(value: object) -> dict[str, Any]:
    """Project one public typed model into the shared envelope mapping."""
    builtins = msgspec.to_builtins(value)
    if not isinstance(builtins, dict):
        raise TypeError("CLI model result must be an object")
    return cast("dict[str, Any]", builtins)


def _failure_context(error: BaseException | None) -> dict[str, Any]:
    """Project only the typed, secret-free retained-artifact context."""
    context = getattr(error, "failure_context", None) if error is not None else None
    if not isinstance(context, DatabasePreparationFailureContext):
        return {}
    return model_to_dict(context)


def _failure_message(message: object, context: dict[str, Any]) -> str:
    rendered = sanitize_diagnostic(message)
    details: list[str] = []
    if context.get("retained_backup_id") is not None:
        details.append(f"retained backup {context['retained_backup_id']}")
    if context.get("retained_database") is not None:
        details.append(f"retained database {context['retained_database']}")
    return f"{rendered}; {'; '.join(details)}" if details else rendered


def rich_print(
    value: object,
    *,
    end: str = "\n",
    preserve_newlines: bool = False,
) -> None:
    """Print human output safely, optionally preserving document line feeds."""
    rendered = sanitize_terminal_text(str(value), preserve_newlines=preserve_newlines)
    Console().print(rendered, markup=False, soft_wrap=True, end=end)


def _sanitize_envelope_value(value: Any) -> Any:
    """Recursively make machine-envelope values inert for terminal transports."""
    if isinstance(value, str):
        return sanitize_terminal_text(value)
    if isinstance(value, dict):
        return {
            sanitize_terminal_text(key) if isinstance(key, str) else key: _sanitize_envelope_value(
                item
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_envelope_value(item) for item in value]
    return value


def build_envelope(
    *,
    ok: bool,
    command: str,
    result: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    dry_run: bool = False,
    error_code: str | None = None,
    error_message: object | None = None,
) -> dict[str, Any]:
    """Build the existing v1 envelope without selecting an output transport."""
    envelope: dict[str, Any] = {
        "schema_version": 1,
        "ok": ok,
        "command": command,
        "context": context or {},
        "provenance": provenance or {},
        "dry_run": dry_run,
        "warnings": [],
    }
    if ok:
        safe_result = msgspec.to_builtins(result or {})
        if not isinstance(safe_result, dict):
            raise TypeError("CLI envelope result must be a JSON object")
        envelope["result"] = safe_result
        envelope["data"] = safe_result
    else:
        envelope["error"] = {
            "code": error_code or command.replace(".", "_") + "_failed",
            "message": sanitize_diagnostic(error_message),
        }
    return cast("dict[str, Any]", _sanitize_envelope_value(envelope))


def emit_json_envelope(
    *,
    ok: bool,
    command: str,
    result: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    dry_run: bool = False,
    error_code: str | None = None,
    error_message: object | None = None,
    mode: OutputMode = OutputMode.JSON,
) -> None:
    """Emit the v1 envelope as exactly one JSON or TOON stdout document."""
    envelope = build_envelope(
        ok=ok,
        command=command,
        result=result,
        context=context,
        provenance=provenance,
        dry_run=dry_run,
        error_code=error_code,
        error_message=error_message,
    )
    if mode is OutputMode.TOON:
        click.echo(encode(envelope))
    elif mode is OutputMode.JSON:
        click.echo(json.dumps(envelope, indent=2))
    else:
        raise ValueError("structured envelope emission requires json or toon mode")


def fail(
    output_mode: OutputMode | bool,
    command: str,
    message: object,
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
        emit_json_envelope(
            ok=False,
            command=command,
            context=context,
            error_code="usage_error" if usage else command.replace(".", "_") + "_failed",
            error_message=rendered_message,
            mode=mode,
        )
    else:
        click.echo(rendered_message, err=True)
    if usage:
        raise click.exceptions.Exit(2)
    sys.exit(1)


__all__ = [
    "OutputMode",
    "build_envelope",
    "emit_json_envelope",
    "fail",
    "model_to_dict",
    "output_options",
    "resolve_output_mode",
    "rich_print",
    "sanitize_diagnostic",
    "sanitize_terminal_text",
]
