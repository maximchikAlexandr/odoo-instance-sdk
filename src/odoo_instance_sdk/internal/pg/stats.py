"""Versioned statistics diagnostic SQL and its frozen typed decoder."""

from __future__ import annotations

from odoo_instance_sdk.models import PostgresStatsResult

from .diagnostics import decode_typed_json, load_sql_asset, validate_timeout, validate_top

STATS_SQL_VERSION = 1
STATS_SQL = load_sql_asset("stats_v1.sql")


def build_stats_sql(*, top: int = 20, timeout: float = 30.0) -> str:
    validate_top(top)
    validate_timeout(timeout)
    return STATS_SQL.replace("__ODCLI_TOP__", str(top))


def decode_stats(stdout: str | bytes) -> PostgresStatsResult:
    return decode_typed_json(stdout, PostgresStatsResult, "stats")


__all__ = [
    "STATS_SQL",
    "STATS_SQL_VERSION",
    "build_stats_sql",
    "decode_stats",
]
