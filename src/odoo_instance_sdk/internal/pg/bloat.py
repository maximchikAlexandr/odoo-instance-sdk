"""Versioned bloat diagnostic SQL and its frozen typed decoder."""

from __future__ import annotations

from odoo_instance_sdk.models import PostgresBloatResult

from .diagnostics import (
    decode_typed_json,
    load_sql_asset,
    validate_exact_max_scan_mb,
    validate_timeout,
    validate_top,
)

BLOAT_SQL_VERSION = 1
BLOAT_SQL = load_sql_asset("bloat_v1.sql")


def build_bloat_sql(*, top: int = 20, exact_max_scan_mb: int = 64, timeout: float = 30.0) -> str:
    validate_top(top)
    validate_exact_max_scan_mb(exact_max_scan_mb)
    validate_timeout(timeout)
    return BLOAT_SQL.replace("__ODCLI_TOP__", str(top)).replace(
        "__ODCLI_EXACT_MAX_SCAN_BYTES__", str(exact_max_scan_mb * 1024 * 1024)
    )


def decode_bloat(stdout: str | bytes) -> PostgresBloatResult:
    return decode_typed_json(stdout, PostgresBloatResult, "bloat")


build_bloat_query = build_bloat_sql
decode_bloat_result = decode_bloat

__all__ = [
    "BLOAT_SQL",
    "BLOAT_SQL_VERSION",
    "build_bloat_query",
    "build_bloat_sql",
    "decode_bloat",
    "decode_bloat_result",
]
