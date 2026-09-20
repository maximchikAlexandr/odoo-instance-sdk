from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import msgspec

from odoo_instance_sdk.models._validators import (
    _require_bool,
    _require_datetime,
    _require_non_negative_float,
    _require_non_negative_int,
    _require_ratio,
    _require_tuple,
)


class SqlExecutionResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    returncode: int
    stdout: str
    stderr: str

    def __post_init__(self) -> None:
        if type(self.returncode) is not int:
            raise TypeError("SqlExecutionResult.returncode must be an integer")
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise TypeError("SqlExecutionResult output fields must be strings")


class DiagnosticWarning(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    code: Literal[
        "pg_buffercache_not_installed",
        "pg_buffercache_privilege_denied",
        "pg_buffercache_query_failed",
        "pgstattuple_not_installed",
        "pgstattuple_privilege_denied",
        "pgstattuple_query_failed",
        "cumulative_statistics",
    ]
    message: str

    def __post_init__(self) -> None:
        if self.code not in {
            "pg_buffercache_not_installed",
            "pg_buffercache_privilege_denied",
            "pg_buffercache_query_failed",
            "pgstattuple_not_installed",
            "pgstattuple_privilege_denied",
            "pgstattuple_query_failed",
            "cumulative_statistics",
        }:
            raise ValueError(f"unknown DiagnosticWarning.code: {self.code!r}")
        if not isinstance(self.message, str):
            raise TypeError("DiagnosticWarning.message must be a string")


class LockRow(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    blocked_pid: int
    blocking_pids: tuple[int, ...]
    application_name: str | None
    user_name: str | None
    client_address: str | None
    wait_event_type: str | None
    wait_event: str | None
    state: str | None
    transaction_age_seconds: float | None
    query_age_seconds: float | None
    query_preview: str

    def __post_init__(self) -> None:
        _require_non_negative_int(self.blocked_pid, "LockRow.blocked_pid")
        _require_tuple(self.blocking_pids, "LockRow.blocking_pids")
        for pid in self.blocking_pids:
            _require_non_negative_int(pid, "LockRow.blocking_pids item")
        _require_non_negative_float(self.transaction_age_seconds, "LockRow.transaction_age_seconds")
        _require_non_negative_float(self.query_age_seconds, "LockRow.query_age_seconds")
        if not isinstance(self.query_preview, str) or len(self.query_preview) > 240:
            raise ValueError("LockRow.query_preview must be at most 240 characters")


class LocksResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    database: str
    captured_at: Annotated[datetime, "odcli-structural"]
    rows: tuple[LockRow, ...]
    warnings: Annotated[tuple[DiagnosticWarning, ...], "odcli-structural"]

    def __post_init__(self) -> None:
        _require_datetime(self.captured_at, "LocksResult.captured_at")
        _require_tuple(self.rows, "LocksResult.rows")
        _require_tuple(self.warnings, "LocksResult.warnings")
        if any(not isinstance(item, LockRow) for item in self.rows):
            raise TypeError("LocksResult.rows must contain LockRow")
        if any(not isinstance(item, DiagnosticWarning) for item in self.warnings):
            raise TypeError("LocksResult.warnings must contain DiagnosticWarning")


class StatsCapabilities(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    pg_buffercache: bool

    def __post_init__(self) -> None:
        _require_bool(self.pg_buffercache, "StatsCapabilities.pg_buffercache")


class StatsSummary(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    database: str
    server_version: str
    captured_at: datetime
    stats_since: datetime | None
    database_bytes: int
    block_size_bytes: int

    def __post_init__(self) -> None:
        _require_datetime(self.captured_at, "StatsSummary.captured_at")
        if self.stats_since is not None:
            _require_datetime(self.stats_since, "StatsSummary.stats_since")
        _require_non_negative_int(self.database_bytes, "StatsSummary.database_bytes")
        _require_non_negative_int(self.block_size_bytes, "StatsSummary.block_size_bytes")


class TableStats(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    schema: str
    table: str
    estimated_live_rows: int
    heap_bytes: int
    toast_bytes: int
    index_bytes: int
    total_bytes: int
    index_count: int
    heap_blocks_read: int
    heap_blocks_hit: int
    index_blocks_read: int
    index_blocks_hit: int
    shared_buffer_bytes: int | None
    shared_buffer_ratio: float | None
    hot_page_ratio: float | None

    def __post_init__(self) -> None:
        for field in (
            "estimated_live_rows",
            "heap_bytes",
            "toast_bytes",
            "index_bytes",
            "total_bytes",
            "index_count",
            "heap_blocks_read",
            "heap_blocks_hit",
            "index_blocks_read",
            "index_blocks_hit",
        ):
            _require_non_negative_int(getattr(self, field), f"TableStats.{field}")
        if self.shared_buffer_bytes is not None:
            _require_non_negative_int(self.shared_buffer_bytes, "TableStats.shared_buffer_bytes")
        _require_ratio(self.shared_buffer_ratio, "TableStats.shared_buffer_ratio")
        _require_ratio(self.hot_page_ratio, "TableStats.hot_page_ratio")


class IndexStats(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    schema: str
    index: str
    table: str
    access_method: str
    columns: tuple[str, ...]
    bytes: int
    scans: int

    def __post_init__(self) -> None:
        _require_tuple(self.columns, "IndexStats.columns")
        if any(not isinstance(column, str) for column in self.columns):
            raise TypeError("IndexStats.columns must contain strings")
        _require_non_negative_int(self.bytes, "IndexStats.bytes")
        _require_non_negative_int(self.scans, "IndexStats.scans")


class PostgresStatsResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    summary: StatsSummary
    tables: tuple[TableStats, ...]
    indexes: tuple[IndexStats, ...]
    capabilities: Annotated[StatsCapabilities, "odcli-structural"]
    warnings: Annotated[tuple[DiagnosticWarning, ...], "odcli-structural"]

    def __post_init__(self) -> None:
        _require_tuple(self.tables, "PostgresStatsResult.tables")
        _require_tuple(self.indexes, "PostgresStatsResult.indexes")
        _require_tuple(self.warnings, "PostgresStatsResult.warnings")
        if any(not isinstance(item, TableStats) for item in self.tables):
            raise TypeError("PostgresStatsResult.tables must contain TableStats")
        if any(not isinstance(item, IndexStats) for item in self.indexes):
            raise TypeError("PostgresStatsResult.indexes must contain IndexStats")
        if any(not isinstance(item, DiagnosticWarning) for item in self.warnings):
            raise TypeError("PostgresStatsResult.warnings must contain DiagnosticWarning")


class BloatCapabilities(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    pgstattuple: bool

    def __post_init__(self) -> None:
        _require_bool(self.pgstattuple, "BloatCapabilities.pgstattuple")


class TableBloat(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    schema: str
    table: str
    total_bytes: int
    bloat_bytes: int | None
    bloat_ratio: float | None
    live_tuples: int | None
    dead_tuples: int | None
    last_vacuum_at: datetime | None
    last_autovacuum_at: datetime | None
    last_analyze_at: datetime | None
    last_autoanalyze_at: datetime | None
    method: Literal["exact", "estimate", "unavailable"]

    def __post_init__(self) -> None:
        _require_non_negative_int(self.total_bytes, "TableBloat.total_bytes")
        if self.bloat_bytes is not None:
            _require_non_negative_int(self.bloat_bytes, "TableBloat.bloat_bytes")
        if self.live_tuples is not None:
            _require_non_negative_int(self.live_tuples, "TableBloat.live_tuples")
        if self.dead_tuples is not None:
            _require_non_negative_int(self.dead_tuples, "TableBloat.dead_tuples")
        _require_ratio(self.bloat_ratio, "TableBloat.bloat_ratio")
        for field in (
            "last_vacuum_at",
            "last_autovacuum_at",
            "last_analyze_at",
            "last_autoanalyze_at",
        ):
            value = getattr(self, field)
            if value is not None:
                _require_datetime(value, f"TableBloat.{field}")
        if self.method not in {"exact", "estimate", "unavailable"}:
            raise ValueError(f"unknown TableBloat.method: {self.method!r}")


class IndexBloat(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    schema: str
    index: str
    table: str
    total_bytes: int
    bloat_bytes: int | None
    bloat_ratio: float | None
    scans: int
    unused_candidate: bool
    method: Literal["exact", "estimate", "unavailable"]

    def __post_init__(self) -> None:
        _require_non_negative_int(self.total_bytes, "IndexBloat.total_bytes")
        if self.bloat_bytes is not None:
            _require_non_negative_int(self.bloat_bytes, "IndexBloat.bloat_bytes")
        _require_ratio(self.bloat_ratio, "IndexBloat.bloat_ratio")
        _require_non_negative_int(self.scans, "IndexBloat.scans")
        _require_bool(self.unused_candidate, "IndexBloat.unused_candidate")
        if self.method not in {"exact", "estimate", "unavailable"}:
            raise ValueError(f"unknown IndexBloat.method: {self.method!r}")


class PostgresBloatResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    database: str
    captured_at: Annotated[datetime, "odcli-structural"]
    tables: tuple[TableBloat, ...]
    indexes: tuple[IndexBloat, ...]
    capabilities: Annotated[BloatCapabilities, "odcli-structural"]
    warnings: Annotated[tuple[DiagnosticWarning, ...], "odcli-structural"]

    def __post_init__(self) -> None:
        _require_datetime(self.captured_at, "PostgresBloatResult.captured_at")
        _require_tuple(self.tables, "PostgresBloatResult.tables")
        _require_tuple(self.indexes, "PostgresBloatResult.indexes")
        _require_tuple(self.warnings, "PostgresBloatResult.warnings")
        if any(not isinstance(item, TableBloat) for item in self.tables):
            raise TypeError("PostgresBloatResult.tables must contain TableBloat")
        if any(not isinstance(item, IndexBloat) for item in self.indexes):
            raise TypeError("PostgresBloatResult.indexes must contain IndexBloat")
        if any(not isinstance(item, DiagnosticWarning) for item in self.warnings):
            raise TypeError("PostgresBloatResult.warnings must contain DiagnosticWarning")


class MonitoringExtensionSkip(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    extension: Literal["pg_buffercache", "pgstattuple"]
    reason: Literal["not_available", "privilege_denied"]

    def __post_init__(self) -> None:
        if self.extension not in {"pg_buffercache", "pgstattuple"}:
            raise ValueError(f"unknown MonitoringExtensionSkip.extension: {self.extension!r}")
        if self.reason not in {"not_available", "privilege_denied"}:
            raise ValueError(f"unknown MonitoringExtensionSkip.reason: {self.reason!r}")


class MonitoringInitializationResult(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    installed: tuple[Literal["pg_buffercache", "pgstattuple"], ...]
    already_present: tuple[Literal["pg_buffercache", "pgstattuple"], ...]
    skipped: tuple[MonitoringExtensionSkip, ...]

    def __post_init__(self) -> None:
        installed = _require_tuple(self.installed, "MonitoringInitializationResult.installed")
        already_present = _require_tuple(
            self.already_present, "MonitoringInitializationResult.already_present"
        )
        skipped = _require_tuple(self.skipped, "MonitoringInitializationResult.skipped")
        if any(not isinstance(item, MonitoringExtensionSkip) for item in skipped):
            raise TypeError(
                "MonitoringInitializationResult.skipped must contain MonitoringExtensionSkip"
            )
        if len(set(installed)) != len(installed) or tuple(sorted(installed)) != installed:
            raise ValueError("MonitoringInitializationResult.installed must be sorted and unique")
        if (
            len(set(already_present)) != len(already_present)
            or tuple(sorted(already_present)) != already_present
        ):
            raise ValueError(
                "MonitoringInitializationResult.already_present must be sorted and unique"
            )
        skipped_extensions = tuple(item.extension for item in skipped)
        if (
            len(set(skipped_extensions)) != len(skipped_extensions)
            or tuple(sorted(skipped_extensions)) != skipped_extensions
        ):
            raise ValueError("MonitoringInitializationResult.skipped must be sorted and unique")
        if set(installed) & set(already_present) or set(installed) & set(skipped_extensions):
            raise ValueError("MonitoringInitializationResult outcomes must be disjoint")
        if set(already_present) & set(skipped_extensions):
            raise ValueError("MonitoringInitializationResult outcomes must be disjoint")


class PostgresServerInfo(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    version: str
    postmaster_started_at: datetime
    uptime_seconds: int
    connections_total: int
    connections_active: int
    connections_idle: int
    max_connections: int
    connectable_databases: int

    def __post_init__(self) -> None:
        _require_datetime(self.postmaster_started_at, "PostgresServerInfo.postmaster_started_at")
        for field in (
            "uptime_seconds",
            "connections_total",
            "connections_active",
            "connections_idle",
            "max_connections",
            "connectable_databases",
        ):
            _require_non_negative_int(getattr(self, field), f"PostgresServerInfo.{field}")
