from __future__ import annotations

from datetime import UTC, datetime
from math import inf, nan

import msgspec
import pytest

from odoo_instance_sdk import (
    BloatCapabilities,
    DiagnosticWarning,
    IndexBloat,
    IndexStats,
    LockRow,
    LocksResult,
    MonitoringExtensionSkip,
    MonitoringInitializationResult,
    PostgresBloatResult,
    PostgresServerInfo,
    PostgresStatsResult,
    SqlExecutionResult,
    StatsCapabilities,
    StatsSummary,
    TableBloat,
    TableStats,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _lock() -> LockRow:
    return LockRow(
        blocked_pid=10,
        blocking_pids=(11,),
        application_name=None,
        user_name=None,
        client_address=None,
        wait_event_type=None,
        wait_event=None,
        state="active",
        transaction_age_seconds=0.0,
        query_age_seconds=None,
        query_preview="SELECT 1",
    )


def _summary() -> StatsSummary:
    return StatsSummary(
        database="postgres",
        server_version="16",
        captured_at=NOW,
        stats_since=None,
        database_bytes=0,
        block_size_bytes=8192,
    )


def test_public_diagnostic_models_are_frozen_msgspec_structs() -> None:
    for model in (
        DiagnosticWarning,
        LockRow,
        LocksResult,
        StatsCapabilities,
        StatsSummary,
        TableStats,
        IndexStats,
        PostgresStatsResult,
        BloatCapabilities,
        TableBloat,
        IndexBloat,
        PostgresBloatResult,
        MonitoringExtensionSkip,
        MonitoringInitializationResult,
        SqlExecutionResult,
        PostgresServerInfo,
    ):
        assert issubclass(model, msgspec.Struct)
        assert model.__struct_config__.frozen


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (LockRow, "blocking_pids", [11]),
        (LocksResult, "rows", [_lock()]),
        (PostgresStatsResult, "tables", []),
        (IndexStats, "columns", ["id"]),
        (MonitoringInitializationResult, "installed", ["pg_buffercache"]),
    ],
)
def test_collection_fields_require_tuples(model: type[object], field: str, value: object) -> None:
    kwargs: dict[str, object]
    if model is LockRow:
        kwargs = {
            "blocked_pid": 10,
            "blocking_pids": value,
            "application_name": None,
            "user_name": None,
            "client_address": None,
            "wait_event_type": None,
            "wait_event": None,
            "state": None,
            "transaction_age_seconds": None,
            "query_age_seconds": None,
            "query_preview": "",
        }
    elif model is LocksResult:
        kwargs = {"database": "postgres", "captured_at": NOW, "rows": value, "warnings": ()}
    elif model is PostgresStatsResult:
        kwargs = {
            "summary": _summary(),
            "tables": value,
            "indexes": (),
            "capabilities": StatsCapabilities(pg_buffercache=False),
            "warnings": (),
        }
    elif model is IndexStats:
        kwargs = {
            "schema": "public",
            "index": "i",
            "table": "t",
            "access_method": "btree",
            "columns": value,
            "bytes": 0,
            "scans": 0,
        }
    else:
        kwargs = {"installed": value, "already_present": (), "skipped": ()}
    with pytest.raises((TypeError, ValueError), match="tuple"):
        model(**kwargs)


@pytest.mark.parametrize("ratio", [-0.01, 1.01, inf, nan])
def test_table_stats_rejects_invalid_ratios(ratio: float) -> None:
    with pytest.raises((TypeError, ValueError), match="ratio"):
        TableStats(
            schema="public",
            table="t",
            estimated_live_rows=0,
            heap_bytes=0,
            toast_bytes=0,
            index_bytes=0,
            total_bytes=0,
            index_count=0,
            heap_blocks_read=0,
            heap_blocks_hit=0,
            index_blocks_read=0,
            index_blocks_hit=0,
            shared_buffer_bytes=None,
            shared_buffer_ratio=ratio,
            hot_page_ratio=None,
        )


def test_models_preserve_nullability_and_zero_values() -> None:
    table = TableBloat(
        schema="public",
        table="t",
        total_bytes=0,
        bloat_bytes=None,
        bloat_ratio=None,
        live_tuples=0,
        dead_tuples=0,
        last_vacuum_at=None,
        last_autovacuum_at=None,
        last_analyze_at=None,
        last_autoanalyze_at=None,
        method="unavailable",
    )
    server = PostgresServerInfo(
        version="16",
        postmaster_started_at=NOW,
        uptime_seconds=0,
        connections_total=0,
        connections_active=0,
        connections_idle=0,
        max_connections=0,
        connectable_databases=0,
    )
    assert table.live_tuples == 0
    assert table.bloat_bytes is None
    assert server.postmaster_started_at == NOW


def test_warning_and_monitoring_outcomes_are_closed_and_disjoint() -> None:
    with pytest.raises(ValueError, match=r"DiagnosticWarning\.code"):
        DiagnosticWarning(code="unknown", message="bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="disjoint"):
        MonitoringInitializationResult(
            installed=("pg_buffercache",),
            already_present=("pg_buffercache",),
            skipped=(),
        )
    result = MonitoringInitializationResult(
        installed=("pg_buffercache",),
        already_present=(),
        skipped=(MonitoringExtensionSkip(extension="pgstattuple", reason="not_available"),),
    )
    assert result.installed == ("pg_buffercache",)


def test_nested_result_schemas_keep_typed_tuple_members() -> None:
    row = _lock()
    locks = LocksResult(
        database="postgres",
        captured_at=NOW,
        rows=(row,),
        warnings=(DiagnosticWarning(code="cumulative_statistics", message="cumulative"),),
    )
    stats = PostgresStatsResult(
        summary=_summary(),
        tables=(),
        indexes=(),
        capabilities=StatsCapabilities(pg_buffercache=False),
        warnings=(),
    )
    bloat = PostgresBloatResult(
        database="postgres",
        captured_at=NOW,
        tables=(),
        indexes=(),
        capabilities=BloatCapabilities(pgstattuple=False),
        warnings=(),
    )
    assert locks.rows == (row,)
    assert stats.tables == ()
    assert bloat.indexes == ()
    assert SqlExecutionResult(returncode=0, stdout="", stderr="").returncode == 0
