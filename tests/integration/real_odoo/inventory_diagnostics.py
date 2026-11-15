"""Secret-free inventory process diagnostics for a focused real-Odoo test."""

from __future__ import annotations

import re
import subprocess
from typing import Literal, TypedDict

from odoo_instance_sdk.internal.proc import ProcessResult

InventoryFailureCategory = Literal["auth", "connection", "sql", "timeout", "process", "unknown"]

_SQLSTATE_CATEGORIES: dict[str, InventoryFailureCategory] = {
    "08000": "connection",
    "08001": "connection",
    "08003": "connection",
    "08004": "connection",
    "08006": "connection",
    "08P01": "connection",
    "28000": "auth",
    "28P01": "auth",
    "3D000": "sql",
    "42501": "sql",
    "42601": "sql",
    "42703": "sql",
    "42883": "sql",
    "42P01": "sql",
    "57014": "timeout",
}
_SQLSTATE_LABEL = re.compile(r"\bSQLSTATE\s*[:=]?\s*([0-9A-Z]{5})\b", re.IGNORECASE)
_AUTH_TEXT = ("password authentication failed", "no password supplied", "authentication failed")
_CONNECTION_TEXT = (
    "connection refused",
    "could not connect to server",
    "server closed the connection unexpectedly",
    "no route to host",
    "connection reset",
)
_TIMEOUT_TEXT = ("timed out", "timeout expired", "statement timeout", "query canceled")


class InventoryDiagnostic(TypedDict):
    """The only values permitted in the diagnostic artifact."""

    exit_code: int | None
    category: InventoryFailureCategory
    sqlstate: str | None


def _allowed_sqlstate(stderr: str) -> str | None:
    match = _SQLSTATE_LABEL.search(stderr)
    if match is None:
        return None
    candidate = match.group(1).upper()
    return candidate if candidate in _SQLSTATE_CATEGORIES else None


def _classify_stderr(stderr: str, returncode: int) -> tuple[InventoryFailureCategory, str | None]:
    lowered = stderr.lower()
    sqlstate = _allowed_sqlstate(stderr)
    if sqlstate is not None:
        return _SQLSTATE_CATEGORIES[sqlstate], sqlstate
    if any(fragment in lowered for fragment in _TIMEOUT_TEXT):
        return "timeout", None
    if any(fragment in lowered for fragment in _AUTH_TEXT):
        return "auth", None
    if any(fragment in lowered for fragment in _CONNECTION_TEXT):
        return "connection", None
    if returncode in {126, 127} or "exec format error" in lowered:
        return "process", None
    if "error:" in lowered or "fatal:" in lowered:
        return "sql", None
    return "unknown", None


def inventory_diagnostic(
    result: ProcessResult | None, *, error: BaseException | None = None
) -> InventoryDiagnostic:
    """Classify a private child result without retaining any child text or identity."""
    if result is None:
        if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
            return {"exit_code": None, "category": "timeout", "sqlstate": None}
        if isinstance(error, OSError):
            return {"exit_code": None, "category": "process", "sqlstate": None}
        return {"exit_code": None, "category": "unknown", "sqlstate": None}

    raw_exit = result.returncode
    exit_code = raw_exit if -128 <= raw_exit <= 255 else None
    if isinstance(result.stderr, bytes):
        stderr = result.stderr.decode("utf-8", errors="replace")
    else:
        stderr = result.stderr or ""
    category, sqlstate = _classify_stderr(stderr, raw_exit)
    return {"exit_code": exit_code, "category": category, "sqlstate": sqlstate}


def inventory_step_id(step: object) -> bool:
    """Return whether a prepared child is the inventory query under observation."""
    return getattr(step, "step_id", None) == "database.list.inventory"
