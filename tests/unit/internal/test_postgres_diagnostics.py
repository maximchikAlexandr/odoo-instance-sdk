from __future__ import annotations

import json
from dataclasses import dataclass, field
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
from odoo_instance_sdk.internal.pg.monitoring import (
    MONITORING_SQL,
    build_monitoring_sql,
    decode_monitoring,
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


@dataclass
class _RecordedPsqlBoundary:
    """Small recorded PostgreSQL boundary for final JSON and cleanup contracts."""

    persistent_objects: set[str] = field(default_factory=set)
    temporary_objects: set[str] = field(default_factory=set)
    stdout_documents: list[str] = field(default_factory=list)
    committed: bool = False
    rolled_back: bool = False

    def execute(self, sql: str, payload: dict[str, object], *, fail: bool = False) -> str:
        assert "BEGIN;" in sql
        assert "CREATE TEMP TABLE" in sql
        assert "ON COMMIT DROP" in sql
        assert sql.count("SELECT json_build_object(") == 1
        self.temporary_objects.add("pg_temp.pgdiag_result")
        if fail:
            self.rolled_back = True
            self.temporary_objects.clear()
            raise ConfigError("recorded PostgreSQL query failed")
        document = json.dumps(payload)
        self.stdout_documents.append(document)
        self.committed = True
        self.temporary_objects.clear()
        return document


@dataclass
class _RecordedMonitoringBoundary:
    """Record extension outcomes at the final psql JSON boundary."""

    installed: set[str] = field(default_factory=set)
    available: set[str] = field(default_factory=lambda: {"pg_buffercache", "pgstattuple"})
    privilege_denied: set[str] = field(default_factory=set)
    create_errors: dict[str, str] = field(default_factory=dict)
    create_attempts: list[str] = field(default_factory=list)
    stdout_documents: list[str] = field(default_factory=list)
    temporary_objects: set[str] = field(default_factory=set)
    persistent_objects: set[str] = field(default_factory=set)
    committed: bool = False
    rolled_back: bool = False

    def execute(self, sql: str) -> str:
        assert sql.count("SELECT json_build_object(") == 1
        self.temporary_objects.add("pg_temp.pgdiag_monitoring_outcomes")
        outcomes: dict[str, str] = {}
        for extension in ("pg_buffercache", "pgstattuple"):
            if extension in self.installed:
                outcomes[extension] = "already_present"
            elif extension not in self.available:
                outcomes[extension] = "not_available"
            else:
                self.create_attempts.append(extension)
                if extension in self.create_errors:
                    self.rolled_back = True
                    self.temporary_objects.clear()
                    raise ConfigError(f"CREATE EXTENSION failed: {self.create_errors[extension]}")
                if extension in self.privilege_denied:
                    outcomes[extension] = "privilege_denied"
                else:
                    self.installed.add(extension)
                    outcomes[extension] = "installed"
        payload = {
            "installed": [
                extension for extension in sorted(outcomes) if outcomes[extension] == "installed"
            ],
            "already_present": [
                extension
                for extension in sorted(outcomes)
                if outcomes[extension] == "already_present"
            ],
            "skipped": [
                {"extension": extension, "reason": outcomes[extension]}
                for extension in sorted(outcomes)
                if outcomes[extension] in {"not_available", "privilege_denied"}
            ],
        }
        document = json.dumps(payload)
        self.stdout_documents.append(document)
        self.committed = True
        self.temporary_objects.clear()
        return document


def _stats_payload(
    *, pg_buffercache: bool, cache_fields: tuple[int | None, float | None, float | None]
) -> dict[str, object]:
    cache_bytes, cache_ratio, hot_ratio = cache_fields
    return {
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
                "table": "uncached",
                "estimated_live_rows": 1,
                "heap_bytes": 4096,
                "toast_bytes": 0,
                "index_bytes": 0,
                "total_bytes": 4096,
                "index_count": 0,
                "heap_blocks_read": 0,
                "heap_blocks_hit": 0,
                "index_blocks_read": 0,
                "index_blocks_hit": 0,
                "shared_buffer_bytes": cache_bytes,
                "shared_buffer_ratio": cache_ratio,
                "hot_page_ratio": hot_ratio,
            },
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
                "shared_buffer_bytes": cache_bytes,
                "shared_buffer_ratio": cache_ratio,
                "hot_page_ratio": hot_ratio,
            },
        ],
        "indexes": [],
        "capabilities": {"pg_buffercache": pg_buffercache},
        "warnings": (
            [{"code": "cumulative_statistics", "message": "counters are cumulative"}]
            if pg_buffercache
            else [
                {
                    "code": "pg_buffercache_query_failed",
                    "message": "cache fields are unavailable",
                },
                {"code": "cumulative_statistics", "message": "counters are cumulative"},
            ]
        ),
    }


def _bloat_payload(*, mixed_optional_failure: bool = False) -> dict[str, object]:
    warnings: list[dict[str, str]] = [
        {"code": "cumulative_statistics", "message": "counters are cumulative"}
    ]
    if mixed_optional_failure:
        warnings.insert(
            0,
            {
                "code": "pgstattuple_query_failed",
                "message": "exact bloat is unavailable",
            },
        )
    return {
        "database": "app",
        "captured_at": _CAPTURED_AT.isoformat(),
        "tables": [
            {
                "schema": "public",
                "table": "exact_table",
                "total_bytes": 100,
                "bloat_bytes": 10,
                "bloat_ratio": 0.1,
                "live_tuples": 9,
                "dead_tuples": 1,
                "last_vacuum_at": None,
                "last_autovacuum_at": None,
                "last_analyze_at": None,
                "last_autoanalyze_at": None,
                "method": "estimate" if mixed_optional_failure else "exact",
            },
            {
                "schema": "public",
                "table": "estimated_table",
                "total_bytes": 200,
                "bloat_bytes": 20,
                "bloat_ratio": 0.1,
                "live_tuples": 18,
                "dead_tuples": 2,
                "last_vacuum_at": None,
                "last_autovacuum_at": None,
                "last_analyze_at": None,
                "last_autoanalyze_at": None,
                "method": "estimate",
            },
            {
                "schema": "public",
                "table": "unknown_table",
                "total_bytes": 0,
                "bloat_bytes": None,
                "bloat_ratio": None,
                "live_tuples": None,
                "dead_tuples": None,
                "last_vacuum_at": None,
                "last_autovacuum_at": None,
                "last_analyze_at": None,
                "last_autoanalyze_at": None,
                "method": "unavailable",
            },
        ],
        "indexes": [
            {
                "schema": "public",
                "index": "exact_index",
                "table": "exact_table",
                "total_bytes": 100,
                "bloat_bytes": 5,
                "bloat_ratio": 0.05,
                "scans": 1,
                "unused_candidate": False,
                "method": "exact",
            },
            {
                "schema": "public",
                "index": "estimated_index",
                "table": "estimated_table",
                "total_bytes": 200,
                "bloat_bytes": 20,
                "bloat_ratio": 0.1,
                "scans": 0,
                "unused_candidate": True,
                "method": "estimate",
            },
            {
                "schema": "public",
                "index": "unknown_index",
                "table": "unknown_table",
                "total_bytes": 0,
                "bloat_bytes": None,
                "bloat_ratio": None,
                "scans": 0,
                "unused_candidate": False,
                "method": "unavailable",
            },
        ],
        "capabilities": {"pgstattuple": not mixed_optional_failure},
        "warnings": warnings,
    }


@pytest.mark.unit
def test_locks_asset_uses_real_blockers_and_bounded_order() -> None:
    sql = build_locks_sql(top=7, timeout=1.0)
    assert "pg_blocking_pids(blocked.pid)" in sql
    assert "cardinality(pg_blocking_pids(blocked.pid)) > 0" in sql
    assert "regexp_replace(coalesce(blocked.query, ''), '\\s+', ' ', 'g')" in sql
    assert "ORDER BY wait_age_seconds DESC NULLS LAST, blocked_pid ASC" in sql
    assert "LIMIT 7" in sql
    assert sql.count("SELECT json_build_object") == 1
    assert "CREATE TABLE pgdiag" not in sql
    assert "ON COMMIT DROP" in sql


@pytest.mark.unit
def test_monitoring_asset_checks_catalogs_in_order_and_has_one_final_json() -> None:
    sql = build_monitoring_sql(timeout=2.0)
    assert sql == MONITORING_SQL
    assert sql.index("FROM pg_extension") < sql.index("FROM pg_available_extensions")
    assert "CREATE EXTENSION %I" in sql
    assert "WHEN insufficient_privilege" in sql
    assert "not_available" in sql
    assert "privilege_denied" in sql
    assert "ON COMMIT DROP" in sql
    assert sql.count("SELECT json_build_object(") == 1
    assert "pageinspect" not in sql
    assert "pg_visibility" not in sql
    assert "pgrowlocks" not in sql


@pytest.mark.unit
def test_monitoring_decoder_preserves_sorted_disjoint_outcomes() -> None:
    payload = {
        "installed": ["pg_buffercache"],
        "already_present": [],
        "skipped": [{"extension": "pgstattuple", "reason": "privilege_denied"}],
    }
    result = decode_monitoring(json.dumps(payload))
    assert result.installed == ("pg_buffercache",)
    assert result.already_present == ()
    assert result.skipped[0].extension == "pgstattuple"
    assert result.skipped[0].reason == "privilege_denied"


@pytest.mark.unit
def test_recorded_monitoring_boundary_skips_unavailable_without_create() -> None:
    boundary = _RecordedMonitoringBoundary(available={"pg_buffercache"})
    result = decode_monitoring(boundary.execute(build_monitoring_sql()))

    assert result.installed == ("pg_buffercache",)
    assert result.already_present == ()
    assert result.skipped[0].extension == "pgstattuple"
    assert result.skipped[0].reason == "not_available"
    assert boundary.create_attempts == ["pg_buffercache"]
    assert len(boundary.stdout_documents) == 1
    assert boundary.committed is True
    assert boundary.temporary_objects == set()
    assert boundary.persistent_objects == set()


@pytest.mark.unit
def test_recorded_monitoring_boundary_allows_privilege_partial_commit_and_is_idempotent() -> None:
    boundary = _RecordedMonitoringBoundary(privilege_denied={"pgstattuple"})
    first = decode_monitoring(boundary.execute(build_monitoring_sql()))

    assert first.installed == ("pg_buffercache",)
    assert first.skipped[0].reason == "privilege_denied"
    assert boundary.create_attempts == ["pg_buffercache", "pgstattuple"]
    assert boundary.committed is True
    assert boundary.temporary_objects == set()
    assert boundary.persistent_objects == set()

    boundary.stdout_documents.clear()
    boundary.committed = False
    second = decode_monitoring(boundary.execute(build_monitoring_sql()))
    assert second.installed == ()
    assert second.already_present == ("pg_buffercache",)
    assert second.skipped[0].reason == "privilege_denied"
    assert len(boundary.stdout_documents) == 1
    assert boundary.committed is True
    assert boundary.temporary_objects == set()
    assert boundary.persistent_objects == set()


@pytest.mark.unit
def test_recorded_monitoring_boundary_reraises_create_error_and_rolls_back() -> None:
    boundary = _RecordedMonitoringBoundary(create_errors={"pg_buffercache": "0A000"})
    with pytest.raises(ConfigError, match="0A000"):
        boundary.execute(build_monitoring_sql())

    assert boundary.create_attempts == ["pg_buffercache"]
    assert boundary.committed is False
    assert boundary.rolled_back is True
    assert boundary.stdout_documents == []
    assert boundary.temporary_objects == set()
    assert boundary.persistent_objects == set()


@pytest.mark.unit
def test_stats_asset_preserves_size_order_and_optional_boundary() -> None:
    sql = build_stats_sql(top=11, timeout=2.0)
    assert "current_setting('block_size')::int" in sql
    assert "LEFT JOIN pg_statio_user_tables AS io ON io.relid = c.oid" in sql
    assert "coalesce(io.heap_blks_read, 0)" in sql
    assert "coalesce(io.idx_blks_hit, 0)" in sql
    assert "total_bytes DESC, schema_name ASC, table_name ASC LIMIT 11" in sql
    assert "bytes DESC, schema_name ASC, index_name ASC LIMIT 11" in sql
    assert "b.usagecount >= 3" in sql
    assert "coalesce(q.cached_buffers, 0)" in sql
    assert "pgdiag_stats_capability SET usable = true" in sql
    assert "undefined_table OR undefined_function" in sql
    assert "WHEN insufficient_privilege" in sql
    assert "pg_buffercache_query_failed" in sql
    assert "cumulative_statistics" in sql
    assert sql.count("SELECT json_build_object") == 1
    assert "CREATE TABLE pgdiag" not in sql
    assert "ON COMMIT DROP" in sql


@pytest.mark.unit
def test_stats_asset_measures_zero_cache_for_uncached_and_empty_tables() -> None:
    sql = build_stats_sql()
    assert "FROM pgdiag_stats_tables AS all_tables\n            LEFT JOIN (" in sql
    assert "coalesce(q.cached_buffers, 0)" in sql
    assert "WHEN t.total_bytes = 0 THEN 0.0" in sql
    assert "WHEN coalesce(q.cached_buffers, 0) = 0 THEN 0.0" in sql


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
    loop_marker = "FOR item IN SELECT * FROM"
    table_loop = sql.index(loop_marker)
    index_exact = sql[sql.index(loop_marker, table_loop + 1) :]
    assert index_exact.index("LIMIT 13") < index_exact.index("WHERE access_method = 'btree'")
    assert "pgstattuple($1::regclass)" in sql
    assert "pgstatindex($1::regclass)" in sql
    assert "pgstattuple_query_failed" in sql
    assert "cumulative_statistics" in sql
    assert sql.count("SELECT json_build_object") == 1
    assert "CREATE TABLE pgdiag" not in sql
    assert "ON COMMIT DROP" in sql


def test_bloat_zero_threshold_guards_every_exact_invocation() -> None:
    sql = build_bloat_sql(top=3, exact_max_scan_mb=0)
    assert sql.count("0 > 0") == 2
    assert "pgstattuple($1::regclass)" in sql
    assert "pgstatindex($1::regclass)" in sql


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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pg_buffercache", "cache_fields", "warning_code"),
    [
        pytest.param(True, (0, 0.0, 0.0), None, id="usable-but-uncached-and-empty"),
        pytest.param(
            False,
            (None, None, None),
            "pg_buffercache_query_failed",
            id="unusable-cache-is-null",
        ),
    ],
)
def test_recorded_stats_boundary_decodes_zero_and_null_capability_rows(
    pg_buffercache: bool,
    cache_fields: tuple[int | None, float | None, float | None],
    warning_code: str | None,
) -> None:
    boundary = _RecordedPsqlBoundary()
    output = boundary.execute(
        build_stats_sql(top=2),
        _stats_payload(pg_buffercache=pg_buffercache, cache_fields=cache_fields),
    )
    result = decode_stats(output)

    assert boundary.committed is True
    assert boundary.rolled_back is False
    assert boundary.stdout_documents == [output]
    assert boundary.temporary_objects == set()
    assert boundary.persistent_objects == set()
    assert result.capabilities.pg_buffercache is pg_buffercache
    assert len(result.tables) == 2
    for table in result.tables:
        assert table.total_bytes in (0, 4096)
        assert table.shared_buffer_bytes == cache_fields[0]
        assert table.shared_buffer_ratio == cache_fields[1]
        assert table.hot_page_ratio == cache_fields[2]
    warning_codes = {warning.code for warning in result.warnings}
    assert (
        warning_code in warning_codes
        if warning_code
        else not any(code.startswith("pg_buffercache_") for code in warning_codes)
    )
    assert "cumulative_statistics" in warning_codes


@pytest.mark.unit
@pytest.mark.parametrize(
    ("threshold_mb", "threshold_bytes"),
    [(0, 0), (64, 64 * 1024 * 1024), (1024, 1024 * 1024 * 1024)],
)
def test_recorded_bloat_boundary_covers_methods_thresholds_and_cleanup(
    threshold_mb: int, threshold_bytes: int
) -> None:
    boundary = _RecordedPsqlBoundary()
    sql = build_bloat_sql(top=3, exact_max_scan_mb=threshold_mb)
    result = decode_bloat(boundary.execute(sql, _bloat_payload()))

    assert f"total_bytes <= {threshold_bytes}" in sql
    assert [row.method for row in result.tables] == ["exact", "estimate", "unavailable"]
    assert [row.method for row in result.indexes] == ["exact", "estimate", "unavailable"]
    assert result.capabilities.pgstattuple is True
    assert result.tables[0].bloat_bytes == 10
    assert result.indexes[0].bloat_ratio == 0.05
    assert result.tables[2].bloat_bytes is None
    assert result.indexes[2].bloat_ratio is None
    assert [warning.code for warning in result.warnings] == ["cumulative_statistics"]
    assert boundary.stdout_documents == [json.dumps(_bloat_payload())]
    assert boundary.temporary_objects == set()
    assert boundary.persistent_objects == set()


@pytest.mark.unit
def test_recorded_bloat_boundary_preserves_estimates_after_mixed_optional_failure() -> None:
    boundary = _RecordedPsqlBoundary()
    result = decode_bloat(
        boundary.execute(build_bloat_sql(), _bloat_payload(mixed_optional_failure=True))
    )

    assert result.capabilities.pgstattuple is False
    assert result.tables[0].method == "estimate"
    assert result.tables[0].bloat_bytes == 10
    assert result.tables[0].bloat_ratio == 0.1
    assert result.indexes[0].method == "exact"
    assert {warning.code for warning in result.warnings} == {
        "pgstattuple_query_failed",
        "cumulative_statistics",
    }
    assert len(boundary.stdout_documents) == 1
    assert boundary.temporary_objects == set()
    assert boundary.persistent_objects == set()


@pytest.mark.unit
@pytest.mark.parametrize("builder", [build_stats_sql, build_bloat_sql])
def test_recorded_postgres_failure_rolls_back_without_partial_json(
    builder: object,
) -> None:
    boundary = _RecordedPsqlBoundary()
    with pytest.raises(ConfigError, match="recorded PostgreSQL query failed"):
        boundary.execute(builder(), {}, fail=True)  # type: ignore[operator]

    assert boundary.committed is False
    assert boundary.rolled_back is True
    assert boundary.stdout_documents == []
    assert boundary.temporary_objects == set()
    assert boundary.persistent_objects == set()
