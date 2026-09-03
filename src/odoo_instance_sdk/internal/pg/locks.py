"""Versioned lock diagnostic SQL and its frozen typed decoder."""

from __future__ import annotations

from odoo_instance_sdk.models import LocksResult

from .diagnostics import decode_typed_json, load_sql_asset, validate_timeout, validate_top

LOCKS_SQL_VERSION = 1
LOCKS_SQL = load_sql_asset("locks_v1.sql")


def build_locks_sql(*, top: int = 20, timeout: float = 30.0) -> str:
    validate_top(top)
    validate_timeout(timeout)
    return LOCKS_SQL.replace("__ODCLI_TOP__", str(top))


def decode_locks(stdout: str | bytes) -> LocksResult:
    return decode_typed_json(stdout, LocksResult, "locks")


__all__ = [
    "LOCKS_SQL",
    "LOCKS_SQL_VERSION",
    "build_locks_sql",
    "decode_locks",
]
