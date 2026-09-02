"""Versioned monitoring-extension initialization and typed decoding."""

from __future__ import annotations

from odoo_instance_sdk.models import MonitoringInitializationResult

from .diagnostics import decode_typed_json, load_sql_asset, validate_timeout

MONITORING_SQL_VERSION = 1
MONITORING_SQL = load_sql_asset("init_monitoring_v1.sql")


def build_monitoring_sql(*, timeout: float = 30.0) -> str:
    validate_timeout(timeout)
    return MONITORING_SQL


def decode_monitoring(stdout: str | bytes) -> MonitoringInitializationResult:
    return decode_typed_json(stdout, MonitoringInitializationResult, "monitoring initialization")


__all__ = [
    "MONITORING_SQL",
    "MONITORING_SQL_VERSION",
    "build_monitoring_sql",
    "decode_monitoring",
]
