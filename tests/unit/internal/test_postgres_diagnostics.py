from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.pg.bloat import (
    build_bloat_sql,
    decode_bloat,
)
from odoo_instance_sdk.internal.pg.locks import (
    build_locks_sql,
    decode_locks,
)
from odoo_instance_sdk.internal.pg.stats import (
    build_stats_sql,
    decode_stats,
)
from odoo_instance_sdk.models import (
    DiagnosticWarning,
    IndexBloat,
    IndexStats,
    LockRow,
    LocksResult,
    PostgresBloatResult,
    PostgresStatsResult,
    StatsCapabilities,
    StatsSummary,
    TableBloat,
    TableStats,
)

_CAPTURED_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.unit
def test_locks_asset_uses_real_blockers_and_bounded_order() -> None:
    sql = build_locks_sql(top=7, timeout=1.0)
    assert "pg_blocking_pids(blocked.pid)" in sql
    assert "cardinality(pg_blocking_pids(blocked.pid)) > 0" in sql
    assert "regexp_replace(coalesce(blocked.query, ''), '\\s+', ' ', 'g')" in sql
    assert "ORDER BY query_age_seconds DESC NULLS LAST, blocked_pid ASC" in sql
    assert "LIMIT 7" in sql
    assert sql.count("SELECT json_build_object") == 1
    assert "CREATE TABLE pgdiag" not in sql
    assert "ON COMMIT DROP" in sql


@pytest.mark.unit
def test_stats_asset_preserves_size_order_and_optional_boundary() -> None:
    sql = build_stats_sql(top=11, timeout=2.0)
    assert "current_setting('block_size')::int" in sql
    assert "total_bytes DESC, schema_name ASC, table_name ASC LIMIT 11" in sql
    assert "bytes DESC, schema_name ASC, index_name ASC LIMIT 11" in sql
    assert "b.usagecount >= 3" in sql
    assert "q.cached_buffers * current_setting('block_size')::numeric" in sql
    assert "pgdiag_stats_capability SET usable = true" in sql
    assert "undefined_table OR undefined_function" in sql
    assert "WHEN insufficient_privilege" in sql
    assert "pg_buffercache_query_failed" in sql
    assert "cumulative_statistics" in sql
    assert sql.count("SELECT json_build_object") == 1
    assert "CREATE TABLE pgdiag" not in sql
    assert "ON COMMIT DROP" in sql


@pytest.mark.unit
def test_bloat_asset_keeps_candidate_and_final_null_orders_independent() -> None:
    sql = build_bloat_sql(top=13, exact_max_scan_mb=5, timeout=3.0)
    assert "exact_result.dead_tuple_len + exact_result.free_space" in sql
    assert "floor(pg_total_relation_size(c.oid) *" in sql
    assert (
        "floor(total_bytes * least(1.0, greatest(0.0, exact_result.leaf_fragmentation / 100.0)))"
        in sql
    )
    assert "usage" not in sql.split("unused_candidate", 1)[0]
    assert sql.count("ORDER BY bloat_bytes DESC NULLS LAST") >= 4
    assert "total_bytes <= 5242880" in sql
    assert "LIMIT 13" in sql
    assert "access_method = 'btree'" in sql
    assert "pgstattuple($1::regclass)" in sql
    assert "pgstatindex($1::regclass)" in sql
    assert "pgstattuple_query_failed" in sql
    assert "cumulative_statistics" in sql
    assert sql.count("SELECT json_build_object") == 1
    assert "CREATE TABLE pgdiag" not in sql
    assert "ON COMMIT DROP" in sql


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder, kwargs",
    [
        (build_locks_sql, {"top": 0}),
        (build_locks_sql, {"top": 1001}),
        (build_locks_sql, {"timeout": 0.0}),
        (build_stats_sql, {"top": -1}),
        (build_stats_sql, {"timeout": float("inf")}),
        (build_bloat_sql, {"exact_max_scan_mb": -1}),
        (build_bloat_sql, {"exact_max_scan_mb": 1025}),
        (build_bloat_sql, {"timeout": float("nan")}),
    ],
)
def test_diagnostic_bounds_fail_before_sql_construction(
    builder: object, kwargs: dict[str, object]
) -> None:
    with pytest.raises(ConfigError):
        builder(**kwargs)  # type: ignore[operator]


@pytest.mark.unit
def test_locks_decoder_returns_frozen_typed_rows() -> None:
    payload = {
        "database": "app",
        "captured_at": _CAPTURED_AT.isoformat(),
        "rows": [
            {
                "blocked_pid": 42,
                "blocking_pids": [7, 8],
                "application_name": None,
                "user_name": "odoo",
                "client_address": None,
                "wait_event_type": "Lock",
                "wait_event": "transactionid",
                "state": "active",
                "transaction_age_seconds": 1.5,
                "query_age_seconds": 2.5,
                "query_preview": "select 1",
            }
        ],
        "warnings": [],
    }
    result = decode_locks(json.dumps(payload))
    assert isinstance(result, LocksResult)
    assert isinstance(result.rows, tuple)
    assert isinstance(result.rows[0], LockRow)
    with pytest.raises(ConfigError, match="invalid diagnostic JSON"):
        decode_locks(json.dumps(payload) + "\n{}")


@pytest.mark.unit
def test_stats_and_bloat_decoders_keep_nullable_zero_and_warning_models() -> None:
    warning = {"code": "cumulative_statistics", "message": "counters are cumulative"}
    stats_payload = {
        "summary": {
            "database": "app",
            "server_version": "PostgreSQL 16",
            "captured_at": _CAPTURED_AT.isoformat(),
            "stats_since": None,
            "database_bytes": 0,
            "block_size_bytes": 16384,
        },
        "tables": [
            {
                "schema": "public",
                "table": "empty",
                "estimated_live_rows": 0,
                "heap_bytes": 0,
                "toast_bytes": 0,
                "index_bytes": 0,
                "total_bytes": 0,
                "index_count": 0,
                "heap_blocks_read": 0,
                "heap_blocks_hit": 0,
                "index_blocks_read": 0,
                "index_blocks_hit": 0,
                "shared_buffer_bytes": None,
                "shared_buffer_ratio": None,
                "hot_page_ratio": None,
            }
        ],
        "indexes": [
            {
                "schema": "public",
                "index": "empty_pkey",
                "table": "empty",
                "access_method": "btree",
                "columns": [],
                "bytes": 0,
                "scans": 0,
            }
        ],
        "capabilities": {"pg_buffercache": False},
        "warnings": [warning],
    }
    stats = decode_stats(json.dumps(stats_payload))
    assert isinstance(stats, PostgresStatsResult)
    assert stats.summary.block_size_bytes == 16384
    assert stats.tables[0].shared_buffer_bytes is None
    assert stats.tables[0].total_bytes == 0
    assert isinstance(stats.warnings[0], DiagnosticWarning)

    bloat_payload = {
        "database": "app",
        "captured_at": _CAPTURED_AT.isoformat(),
        "tables": [
            {
                "schema": "public",
                "table": "empty",
                "total_bytes": 0,
                "bloat_bytes": None,
                "bloat_ratio": None,
                "live_tuples": 0,
                "dead_tuples": 0,
                "last_vacuum_at": None,
                "last_autovacuum_at": None,
                "last_analyze_at": None,
                "last_autoanalyze_at": None,
                "method": "unavailable",
            }
        ],
        "indexes": [
            {
                "schema": "public",
                "index": "empty_pkey",
                "table": "empty",
                "total_bytes": 0,
                "bloat_bytes": 0,
                "bloat_ratio": 0.0,
                "scans": 0,
                "unused_candidate": False,
                "method": "estimate",
            }
        ],
        "capabilities": {"pgstattuple": False},
        "warnings": [warning],
    }
    bloat = decode_bloat(json.dumps(bloat_payload))
    assert isinstance(bloat, PostgresBloatResult)
    assert isinstance(bloat.tables, tuple)
    assert bloat.tables[0].method == "unavailable"
    assert bloat.indexes[0].bloat_ratio == 0.0


@pytest.mark.unit
def test_models_used_by_decoders_are_frozen_typed_contracts() -> None:
    assert StatsCapabilities(pg_buffercache=False).pg_buffercache is False
    assert (
        StatsSummary(
            database="app",
            server_version="PostgreSQL 16",
            captured_at=_CAPTURED_AT,
            stats_since=None,
            database_bytes=0,
            block_size_bytes=16384,
        ).stats_since
        is None
    )
    assert isinstance(
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
            shared_buffer_ratio=None,
            hot_page_ratio=None,
        ),
        TableStats,
    )
    assert isinstance(
        IndexStats(
            schema="public",
            index="i",
            table="t",
            access_method="btree",
            columns=(),
            bytes=0,
            scans=0,
        ),
        IndexStats,
    )
    assert isinstance(
        TableBloat(
            schema="public",
            table="t",
            total_bytes=0,
            bloat_bytes=None,
            bloat_ratio=None,
            live_tuples=None,
            dead_tuples=None,
            last_vacuum_at=None,
            last_autovacuum_at=None,
            last_analyze_at=None,
            last_autoanalyze_at=None,
            method="unavailable",
        ),
        TableBloat,
    )
    assert isinstance(
        IndexBloat(
            schema="public",
            index="i",
            table="t",
            total_bytes=0,
            bloat_bytes=None,
            bloat_ratio=None,
            scans=0,
            unused_candidate=False,
            method="unavailable",
        ),
        IndexBloat,
    )
