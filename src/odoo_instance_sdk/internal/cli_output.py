from __future__ import annotations

import json
import sys
from typing import Any

import click

from odoo_instance_sdk.internal.sanitize import sanitize_last_error


def sanitize_diagnostic(value: object) -> str:
    """Make every non-interactive diagnostic safe and bounded before emission."""
    return sanitize_last_error(str(value)) or "operation failed"


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
) -> None:
    """Emit the v1 JSON envelope. Success copies result into data."""
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
        safe_result = result or {}
        envelope["result"] = safe_result
        envelope["data"] = safe_result
    else:
        envelope["error"] = {
            "code": error_code or command.replace(".", "_") + "_failed",
            "message": sanitize_diagnostic(error_message),
        }
    click.echo(json.dumps(envelope, indent=2, default=str))


def fail(json_output: bool, command: str, message: str, *, usage: bool = False) -> None:
    if json_output:
        emit_json_envelope(
            ok=False,
            command=command,
            error_code="usage_error" if usage else command.replace(".", "_") + "_failed",
            error_message=message,
        )
    else:
        click.echo(sanitize_diagnostic(message), err=True)
    if usage:
        raise click.exceptions.Exit(2)
    sys.exit(1)
