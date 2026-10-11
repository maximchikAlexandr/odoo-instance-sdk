"""Alembic-backed catalogue schema migration and verification."""

from __future__ import annotations

import re
import sqlite3
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.storage.catalog_schema import (
    CATALOG_TABLES,
    ENVIRONMENT_RUNTIME_VIEW_SQL,
    metadata,
)

CATALOG_REVISION = "0007"

type _DdlFingerprint = tuple[
    tuple[
        tuple[
            str,
            tuple[tuple[str, str, int, str | None, int, int], ...],
            frozenset[tuple[str, ...]],
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            str,
        ],
        ...,
    ],
    frozenset[tuple[str, str]],
    tuple[tuple[str, tuple[tuple[str, str, str, str, str, str], ...]], ...],
    frozenset[tuple[str, str]],
    frozenset[tuple[str, str, str]],
]


def _migrations_dir() -> Path:
    return Path(str(files("odoo_instance_sdk.storage") / "catalog_migrations"))


def _alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_migrations_dir()))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.resolve()}")
    return config


def catalog_revision(conn: sqlite3.Connection) -> str | None:
    """Return the stamped Alembic revision, if any."""
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        is None
    ):
        return None
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return None if row is None else str(row[0])


def _has_catalog_tables(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backups'").fetchone()
        is not None
    )


def _backup_catalog(db_path: Path) -> Path:
    """Create a SQLite backup before stamping a known alpha catalogue."""
    backup_path = db_path.with_suffix(f"{db_path.suffix}.pre-alembic.backup")
    source = sqlite3.connect(str(db_path))
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()
    backup_path.chmod(0o600)
    return backup_path


def _identity_keys(conn: sqlite3.Connection, table: str) -> frozenset[tuple[str, ...]]:
    columns = tuple(conn.execute(f"PRAGMA table_info({table})"))
    primary_key = tuple(
        str(row[1]) for row in sorted(columns, key=lambda row: int(row[5])) if int(row[5])
    )
    keys = {primary_key} if primary_key else set()
    for row in conn.execute(f"PRAGMA index_list({table})"):
        if str(row[3]) != "u":
            continue
        keys.add(tuple(str(column[2]) for column in conn.execute(f"PRAGMA index_info({row[1]!s})")))
    return frozenset(keys)


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        sorted(
            (str(row[1]), str(row[2]), int(bool(row[3]) or bool(row[5])))
            for row in conn.execute(f"PRAGMA table_info({table})")
        )
    )


def _foreign_keys(conn: sqlite3.Connection, table: str) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (str(row[3]), str(row[2]), str(row[4]))
        for row in conn.execute(f"PRAGMA foreign_key_list({table})")
    )


def _index_names(conn: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    )


def _schema_fingerprint(
    conn: sqlite3.Connection,
) -> tuple[
    tuple[
        tuple[str, tuple[tuple[str, str, int], ...], frozenset[tuple[str, ...]]],
        ...,
    ],
    frozenset[str],
    tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...],
    bool,
]:
    tables = tuple(
        (table, _table_columns(conn, table), _identity_keys(conn, table))
        for table in sorted(CATALOG_TABLES)
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )
    foreign_keys = tuple(
        (table, _foreign_keys(conn, table))
        for table in ("environment_events", "environment_copy_journal", "backups")
    )
    return (
        tables,
        _index_names(conn),
        foreign_keys,
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='environment_runtime'"
        ).fetchone()
        is not None,
    )


def _without_runtime_launch_identity(
    columns: tuple[tuple[str, str, int], ...],
) -> tuple[tuple[str, str, int], ...]:
    return tuple(item for item in columns if item[0] != "launch_identity_json")


def _reference_fingerprint() -> tuple[
    tuple[
        tuple[str, tuple[tuple[str, str, int], ...], frozenset[tuple[str, ...]]],
        ...,
    ],
    frozenset[str],
    tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...],
    bool,
]:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "reference.sqlite3"
        engine = create_engine(f"sqlite:///{db_path}")
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(ENVIRONMENT_RUNTIME_VIEW_SQL)
        reference = sqlite3.connect(str(db_path))
        try:
            return _schema_fingerprint(reference)
        finally:
            reference.close()


def _normalize_sql(sql: str) -> str:
    """Normalize formatting while preserving SQL literal and identifier meaning."""
    compact = " ".join(sql.split())
    compact = re.sub(r"\s*([(),])\s*", r"\1", compact)
    return re.sub(r"\s*(<>|>=|<=|=|>|<)\s*", r"\1", compact)


def _normalize_ddl_sql(sql: str) -> str:
    """Normalize DDL whitespace without changing quoted literals/identifiers."""
    output: list[str] = []
    pending_space = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char.isspace():
            pending_space = True
            index += 1
            continue
        if pending_space and output:
            output.append(" ")
        pending_space = False
        if char in "'\"`[":
            closing = "]" if char == "[" else char
            start = index
            index += 1
            while index < len(sql):
                if sql[index] == closing:
                    if index + 1 < len(sql) and sql[index + 1] == closing:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            output.append(sql[start:index])
            continue
        output.append(char)
        index += 1
    return "".join(output).strip()


def _check_expressions(sql: str | None) -> tuple[str, ...]:
    """Extract SQLite CHECK expressions without reducing them to a presence bit."""
    if not sql:
        return ()
    expressions: list[str] = []
    upper_sql = sql.upper()
    offset = 0
    while True:
        match = re.search(r"\bCHECK\b", upper_sql[offset:])
        if match is None:
            break
        start = offset + match.start()
        opening = upper_sql.find("(", start + len("CHECK"))
        if opening < 0:
            break
        depth = 0
        for index in range(opening, len(sql)):
            if sql[index] == "(":
                depth += 1
            elif sql[index] == ")":
                depth -= 1
                if depth == 0:
                    expressions.append(_normalize_sql(sql[opening + 1 : index]))
                    offset = index + 1
                    break
        else:
            break
    return tuple(expressions)


def _generated_column_definitions(sql: str) -> tuple[str, ...]:
    """Preserve generated-column expressions omitted by SQLite table_info."""
    definitions: list[str] = []
    upper_sql = sql.upper()
    offset = 0
    while True:
        generated_match = re.search(r"\b(?:GENERATED\s+ALWAYS\s+)?AS\s*\(", upper_sql[offset:])
        if generated_match is None:
            break
        generated = offset + generated_match.start()
        opening = offset + generated_match.end() - 1
        depth = 0
        for index in range(opening, len(sql)):
            if sql[index] == "(":
                depth += 1
            elif sql[index] == ")":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    while end < len(sql) and sql[end].isspace():
                        end += 1
                    for keyword in ("STORED", "VIRTUAL"):
                        if upper_sql.startswith(keyword, end):
                            end += len(keyword)
                            break
                    definitions.append(_normalize_sql(sql[generated:end]))
                    offset = end
                    break
        else:
            break
    return tuple(sorted(definitions))


def _table_options(sql: str) -> tuple[str, ...]:
    return tuple(
        option
        for option in ("AUTOINCREMENT", "STRICT", "WITHOUT ROWID")
        if re.search(rf"\b{option}\b", sql, re.IGNORECASE)
    )


def _table_collations(sql: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            _normalize_sql(match.group(1))
            for match in re.finditer(
                r"\bCOLLATE\s+([\"'`\[]?[A-Za-z_][\w$]*(?:\]|[\"'`])?)",
                sql,
                re.IGNORECASE,
            )
        )
    )


def _ddl_fingerprint(
    conn: sqlite3.Connection,
) -> _DdlFingerprint:
    """Return the complete DDL semantics used by catalogue recognition.

    Include all catalogue DDL objects and the table semantics SQLite exposes,
    including hidden/generated columns, collations, and table options.
    """
    table_names = tuple(
        sorted(
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name != 'alembic_version'"
            )
        )
    )
    tables = []
    for table in table_names:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if table_sql is None:
            continue
        columns = tuple(
            (
                str(row[1]),
                str(row[2]),
                int(bool(row[3]) or bool(row[5])),
                None if row[4] is None else _normalize_sql(str(row[4])),
                int(row[5]),
                int(row[6]),
            )
            for row in conn.execute(f"PRAGMA table_xinfo({table})")
        )
        tables.append(
            (
                table,
                columns,
                _identity_keys(conn, table),
                tuple(sorted(_check_expressions(str(table_sql[0])))),
                _table_collations(str(table_sql[0])),
                _generated_column_definitions(str(table_sql[0])),
                _table_options(str(table_sql[0])),
                _normalize_ddl_sql(str(table_sql[0])),
            )
        )

    indexes = frozenset(
        (str(row[0]), _normalize_sql(str(row[1])))
        for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
        if row[1] is not None
    )
    foreign_keys = []
    for table in table_names:
        rows = tuple(
            sorted(
                (
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    str(row[5]),
                    str(row[6]),
                    str(row[7]),
                )
                for row in conn.execute(f"PRAGMA foreign_key_list({table})")
            )
        )
        foreign_keys.append((table, rows))
    views = frozenset(
        (str(row[0]), _normalize_sql(str(row[1])))
        for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='view'")
        if row[1] is not None
    )
    triggers = frozenset(
        (str(row[0]), str(row[1]), _normalize_sql(str(row[2])))
        for row in conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'"
        )
        if row[2] is not None
    )
    return tuple(tables), indexes, tuple(foreign_keys), views, triggers


_HISTORICAL_COMMON_OMISSIONS: dict[str, frozenset[str]] = {
    "environments": frozenset(
        {
            "project_id",
            "checkout_repository_root",
            "checkout_git_common_dir",
            "checkout_commit_sha",
            "code_ownership",
            "artifact_root",
            "adoption_input_fingerprint",
        }
    ),
    "backups": frozenset({"source_name", "pinned"}),
    "environment_copy_journal": frozenset({"backup_ownership"}),
    "runtime": frozenset({"launch_identity_json"}),
    "restores": frozenset({"source_kind", "source_sha256"}),
    "database_events": frozenset({"source_kind", "source_sha256"}),
}


_V16_TABLE_DEFINITIONS = {
    "projects": """CREATE TABLE projects (
        project_id TEXT PRIMARY KEY,
        repository_root TEXT NOT NULL,
        git_common_dir TEXT NOT NULL,
        registered_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "backups": """CREATE TABLE backups (
        id TEXT PRIMARY KEY,
        source_base_url TEXT NOT NULL,
        database_name TEXT NOT NULL,
        format TEXT NOT NULL CHECK (format IN ('zip', 'dump')),
        filestore_requested INTEGER NOT NULL CHECK (filestore_requested IN (0, 1)),
        path TEXT,
        filename TEXT,
        size_bytes INTEGER,
        sha256 TEXT,
        state TEXT NOT NULL CHECK (state IN ('downloading', 'available', 'failed', 'deleted')),
        started_at TEXT NOT NULL,
        downloaded_at TEXT,
        failed_at TEXT,
        deleted_at TEXT,
        error_type TEXT,
        error_message TEXT,
        project_id TEXT REFERENCES projects(project_id),
        source_git_branch TEXT
    )""",
    "backup_events": """CREATE TABLE backup_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        backup_id TEXT NOT NULL REFERENCES backups(id),
        event_type TEXT NOT NULL CHECK (event_type IN ('download_started', 'download_succeeded', 'download_failed', 'validation_succeeded', 'validation_failed', 'validation_unavailable', 'deleted')),
        occurred_at TEXT NOT NULL,
        path TEXT,
        validator TEXT,
        exit_code INTEGER,
        message TEXT
    )""",
    "restores": """CREATE TABLE restores (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        db_host TEXT NOT NULL,
        db_port INTEGER NOT NULL,
        database_name TEXT NOT NULL,
        backup_id TEXT NOT NULL REFERENCES backups(id),
        restored_at TEXT NOT NULL,
        cluster_id TEXT,
        data_directory TEXT
    )""",
    "database_events": """CREATE TABLE database_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        db_host TEXT NOT NULL,
        db_port INTEGER NOT NULL,
        database_name TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (event_type IN ('restored', 'dropped')),
        occurred_at TEXT NOT NULL,
        backup_id TEXT,
        cluster_id TEXT,
        data_directory TEXT,
        CHECK (event_type = 'dropped' OR backup_id IS NOT NULL),
        FOREIGN KEY (backup_id) REFERENCES backups(id)
    )""",
    "environments": """CREATE TABLE environments (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        repository_root TEXT NOT NULL,
        git_common_dir TEXT NOT NULL,
        branch TEXT NOT NULL,
        base_ref TEXT NOT NULL,
        worktree_path TEXT NOT NULL,
        generated_config_path TEXT NOT NULL,
        python_environment_path TEXT NOT NULL,
        python_environment_owned INTEGER NOT NULL,
        dependency_lock_path TEXT NOT NULL,
        db_mode TEXT NOT NULL,
        source_db_name TEXT,
        target_db_name TEXT,
        backup_id TEXT,
        runtime_json TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        removed_at TEXT,
        last_error TEXT,
        applied_settings_json TEXT NOT NULL DEFAULT '{"components":{"addons":{"status":"unknown"},"dependencies":{"status":"unknown"},"git":{"status":"unknown"},"odoo":{"status":"unknown"},"python":{"status":"unknown"}},"version":1}',
        FOREIGN KEY (backup_id) REFERENCES backups(id)
    )""",
    "environment_events": """CREATE TABLE environment_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        environment_id TEXT NOT NULL,
        operation TEXT NOT NULL CHECK (operation IN ('checkout', 'sync', 'use', 'shell', 'remove')),
        outcome TEXT NOT NULL CHECK (outcome IN ('started', 'succeeded', 'failed')),
        occurred_at TEXT NOT NULL,
        message TEXT,
        FOREIGN KEY (environment_id) REFERENCES environments(id)
    )""",
    "environment_copy_journal": """CREATE TABLE environment_copy_journal (
        environment_id TEXT PRIMARY KEY REFERENCES environments(id),
        target_database TEXT NOT NULL,
        db_host TEXT NOT NULL,
        db_port INTEGER NOT NULL,
        db_user TEXT,
        backup_id TEXT REFERENCES backups(id),
        stage TEXT NOT NULL CHECK (stage IN ('prepared', 'backed_up', 'restore_pending', 'restored', 'dropped', 'backup_deleted')),
        updated_at TEXT NOT NULL
    )""",
    "postgres_clusters": """CREATE TABLE postgres_clusters (
        cluster_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        compose_project TEXT NOT NULL,
        volume_name TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending', 'active')),
        created_at TEXT NOT NULL,
        activated_at TEXT
    )""",
    "runtime": """CREATE TABLE runtime (
        owner_kind TEXT NOT NULL CHECK (owner_kind IN ('environment', 'project')),
        owner_id TEXT NOT NULL CHECK (length(trim(owner_id)) > 0),
        root_pid INTEGER NOT NULL,
        create_time REAL NOT NULL,
        started_at TEXT NOT NULL,
        checkout_branch TEXT NOT NULL,
        commit_sha TEXT NOT NULL,
        http_url TEXT NOT NULL,
        http_port INTEGER NOT NULL,
        database_name TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (owner_kind, owner_id)
    )""",
}


_PRE_SOURCE_NEUTRAL_TABLE_DEFINITIONS = {
    table: _normalize_ddl_sql(
        definition.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INTEGER PRIMARY KEY")
    )
    for table, definition in _V16_TABLE_DEFINITIONS.items()
}
_PRE_SOURCE_NEUTRAL_TABLE_DEFINITIONS["restores"] = _PRE_SOURCE_NEUTRAL_TABLE_DEFINITIONS[
    "restores"
].replace(
    "data_directory TEXT )",
    "data_directory TEXT, state TEXT NOT NULL DEFAULT 'complete' "
    "CHECK (state IN ('complete', 'incomplete')) )",
)


def _historical_table_definition(version: Literal["v16", "pre_source_neutral"], table: str) -> str:
    if version == "v16":
        return _normalize_ddl_sql(_V16_TABLE_DEFINITIONS[table])
    return _PRE_SOURCE_NEUTRAL_TABLE_DEFINITIONS[table]


def _historical_ddl_fingerprint(
    version: Literal["v16", "pre_source_neutral"],
) -> _DdlFingerprint:
    """Build exact known historical DDL semantics from the canonical shape."""
    tables, indexes, foreign_keys, views, _triggers = _reference_ddl_fingerprint()
    omissions = _HISTORICAL_COMMON_OMISSIONS
    variant_omissions = {
        "restores": frozenset({"state"}) if version == "v16" else frozenset(),
        "database_events": frozenset({"state"}) if version == "v16" else frozenset(),
    }
    historical_tables = []
    for table, columns, keys, checks, collations, generated, options, _definition in tables:
        omitted = omissions.get(table, frozenset()) | variant_omissions.get(table, frozenset())
        historical_columns = tuple(column for column in columns if column[0] not in omitted)
        if table == "restores":
            historical_columns = tuple(
                (
                    name,
                    type_name,
                    1 if name == "backup_id" else required,
                    default,
                    primary_key,
                    hidden,
                )
                for name, type_name, required, default, primary_key, hidden in historical_columns
            )
        historical_checks = _historical_checks(table, checks, version)
        historical_defaults = tuple(
            (
                name,
                type_name,
                required,
                None if name in omitted else default,
                primary_key,
                hidden,
            )
            for name, type_name, required, default, primary_key, hidden in historical_columns
        )
        if table == "runtime":
            historical_defaults = tuple(
                (*column[:4], index + 1 if index < 2 else 0, column[5])
                for index, column in enumerate(historical_defaults)
            )
        historical_options = options
        if version == "v16" and table in {
            "backup_events",
            "restores",
            "database_events",
            "environment_events",
        }:
            historical_options = tuple(sorted((*options, "AUTOINCREMENT")))
        historical_tables.append(
            (
                table,
                historical_defaults,
                _identity_keys_for_columns(historical_defaults, keys),
                historical_checks,
                collations,
                generated,
                historical_options,
                _historical_table_definition(version, table),
            )
        )

    historical_indexes = frozenset(
        (name, sql)
        for name, sql in indexes
        if name not in {"backups_source_group_idx", "environments_project_checkout_idx"}
        and not (version == "v16" and name == "environments_one_active_branch")
    )
    historical_foreign_keys = tuple(
        (
            table,
            tuple(fk for fk in rows if fk[1] not in omissions.get(table, frozenset())),
        )
        for table, rows in foreign_keys
    )
    historical_views = frozenset(
        (
            name,
            _normalize_sql("""CREATE VIEW environment_runtime AS
SELECT owner_id AS environment_id, root_pid, create_time, started_at,
       checkout_branch, commit_sha, http_url, http_port, database_name, updated_at
FROM runtime WHERE owner_kind = 'environment'"""),
        )
        for name, _sql in views
        if name == "environment_runtime"
    )
    return (
        tuple(historical_tables),
        historical_indexes,
        historical_foreign_keys,
        historical_views,
        frozenset(),
    )


def _historical_checks(
    table: str, checks: tuple[str, ...], version: Literal["v16", "pre_source_neutral"]
) -> tuple[str, ...]:
    if table == "backups":
        checks = tuple(check for check in checks if "pinned IN" not in check)
    elif table == "backup_events":
        checks = tuple(check.replace(",'pin_set'", "") for check in checks)
    elif table == "environments":
        checks = tuple(check for check in checks if "code_ownership IN" not in check)
    elif table == "environment_copy_journal":
        checks = tuple(check for check in checks if "backup_ownership IS NULL" not in check)
    elif table == "restores":
        checks = tuple(
            check
            for check in checks
            if "source_kind" not in check and not (version == "v16" and "state IN" in check)
        )
    elif table == "database_events":
        checks = (
            *(
                check
                for check in checks
                if "source_kind" not in check and "bootstrapped" not in check
            ),
            _normalize_sql("event_type IN ('restored', 'dropped')"),
            _normalize_sql("event_type = 'dropped' OR backup_id IS NOT NULL"),
        )
    return tuple(sorted(checks))


def _identity_keys_for_columns(
    columns: tuple[tuple[str, str, int, str | None, int, int], ...],
    keys: frozenset[tuple[str, ...]],
) -> frozenset[tuple[str, ...]]:
    available = {column[0] for column in columns}
    return frozenset(key for key in keys if set(key) <= available)


def _reference_ddl_fingerprint() -> _DdlFingerprint:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "reference.sqlite3"
        engine = create_engine(f"sqlite:///{db_path}")
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(ENVIRONMENT_RUNTIME_VIEW_SQL)
        reference = sqlite3.connect(str(db_path))
        try:
            return _ddl_fingerprint(reference)
        finally:
            reference.close()


def verify_schema_equivalence(conn: sqlite3.Connection) -> None:
    """Verify that an existing catalogue matches the canonical metadata."""
    if _ddl_fingerprint(conn) != _reference_ddl_fingerprint():
        if (
            catalog_revision(conn) == CATALOG_REVISION
            and int(conn.execute("PRAGMA user_version").fetchone()[0]) == 16
            and _schema_fingerprint(conn) == _reference_fingerprint()
        ):
            return
        raise BackupCatalogError("catalog schema is not equivalent to the canonical revision")


def _repair_known_v16_catalog(conn: sqlite3.Connection) -> bool:
    """Restore the index lost by the historical v16 table rebuild."""
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if user_version != 16:
        return False
    expected_ddl = _historical_ddl_fingerprint("v16")
    if _ddl_fingerprint(conn) != expected_ddl:
        return False
    try:
        with conn:
            conn.execute(
                "ALTER TABLE restores ADD COLUMN state TEXT NOT NULL DEFAULT 'complete' "
                "CHECK (state IN ('complete', 'incomplete'))"
            )
            conn.execute(
                "CREATE UNIQUE INDEX environments_one_active_branch "
                "ON environments(git_common_dir, branch) WHERE state <> 'removed'"
            )
    except sqlite3.IntegrityError as exc:
        raise BackupCatalogError(
            "catalog has multiple active environments for the same branch"
        ) from exc
    return True


def _is_pre_project_ownership_schema(conn: sqlite3.Connection) -> bool:
    actual_tables, actual_indexes, actual_foreign_keys, actual_view = _schema_fingerprint(conn)
    expected_tables, expected_indexes, expected_foreign_keys, expected_view = (
        _reference_fingerprint()
    )
    legacy_indexes = expected_indexes - {
        "backups_source_group_idx",
        "environments_project_checkout_idx",
        "backups_project_idx",
        "restores_cluster_identity_idx",
        "database_events_cluster_identity_idx",
    }
    expected_foreign_keys = tuple(
        (table, () if table == "backups" else keys) for table, keys in expected_foreign_keys
    )
    if actual_indexes != legacy_indexes or actual_foreign_keys != expected_foreign_keys:
        return False
    if actual_view != expected_view:
        return False
    expected_by_name = {table: (columns, keys) for table, columns, keys in expected_tables}
    for table, columns, keys in actual_tables:
        expected_columns, expected_keys = expected_by_name.get(table, ((), frozenset()))
        if table == "environments":
            expected_columns = tuple(
                item
                for item in expected_columns
                if item[0]
                not in {
                    "project_id",
                    "checkout_repository_root",
                    "checkout_git_common_dir",
                    "checkout_commit_sha",
                    "code_ownership",
                    "artifact_root",
                    "adoption_input_fingerprint",
                }
            )
        if table == "runtime":
            expected_columns = _without_runtime_launch_identity(expected_columns)
        if table == "backups":
            expected_columns = tuple(
                item
                for item in expected_columns
                if item[0] not in {"project_id", "source_name", "pinned"}
            )
        if table == "environment_copy_journal":
            expected_columns = tuple(
                item for item in expected_columns if item[0] != "backup_ownership"
            )
        if table in {"restores", "database_events"}:
            expected_columns = tuple(
                item
                for item in expected_columns
                if item[0] not in {"source_kind", "source_sha256", "state"}
            )
            expected_columns = tuple(
                (name, type_name, 1 if name == "backup_id" and table == "restores" else required)
                for name, type_name, required in expected_columns
            )
        if tuple(columns) != expected_columns or keys != expected_keys:
            return False
    return len(actual_tables) == len(expected_tables)


def _is_legacy_provenance_schema(
    conn: sqlite3.Connection, *, pre_project_ownership: bool = False
) -> bool:
    """Recognize the pre-source-neutral schema before stamping it as 0001."""
    if pre_project_ownership:
        return _is_pre_project_ownership_schema(conn)
    return _ddl_fingerprint(conn) == _historical_ddl_fingerprint("pre_source_neutral")


def _repair_known_v15_catalog(conn: sqlite3.Connection) -> bool:
    """Bring the verified pre-project-ownership schema to the Alembic 0001 shape."""
    if int(conn.execute("PRAGMA user_version").fetchone()[0]) != 15:
        return False
    if not _is_legacy_provenance_schema(conn, pre_project_ownership=True):
        return False
    foreign_keys_enabled = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        with conn:
            conn.execute("ALTER TABLE backups ADD COLUMN project_id TEXT")
            conn.execute(
                """UPDATE backups
                   SET project_id = (
                       SELECT MIN(p.project_id)
                       FROM environments e
                       JOIN projects p ON p.repository_root = e.repository_root
                                      AND p.git_common_dir = e.git_common_dir
                       WHERE e.backup_id = backups.id
                   )
                   WHERE (SELECT COUNT(DISTINCT p.project_id)
                          FROM environments e
                          JOIN projects p ON p.repository_root = e.repository_root
                                         AND p.git_common_dir = e.git_common_dir
                          WHERE e.backup_id = backups.id) = 1"""
            )
            conn.execute(
                """CREATE TABLE backups__v16 (
                    id TEXT PRIMARY KEY,
                    source_base_url TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    format TEXT NOT NULL CHECK (format IN ('zip', 'dump')),
                    filestore_requested INTEGER NOT NULL CHECK (filestore_requested IN (0, 1)),
                    path TEXT, filename TEXT, size_bytes INTEGER, sha256 TEXT,
                    state TEXT NOT NULL CHECK (state IN ('downloading', 'available', 'failed', 'deleted')),
                    started_at TEXT NOT NULL, downloaded_at TEXT, failed_at TEXT, deleted_at TEXT,
                    error_type TEXT, error_message TEXT,
                    project_id TEXT REFERENCES projects(project_id), source_git_branch TEXT
                )"""
            )
            columns = ", ".join(str(row[1]) for row in conn.execute("PRAGMA table_info(backups)"))
            conn.execute(f"INSERT INTO backups__v16 ({columns}) SELECT {columns} FROM backups")
            conn.execute("DROP TABLE backups")
            conn.execute("ALTER TABLE backups__v16 RENAME TO backups")
            conn.execute(
                "CREATE INDEX backups_lookup_idx ON backups "
                "(source_base_url, database_name, downloaded_at DESC)"
            )
            conn.execute("CREATE INDEX backups_state_idx ON backups(state)")
            conn.execute(
                "CREATE INDEX backups_point_order_idx ON backups "
                "(COALESCE(downloaded_at, started_at) DESC, id ASC)"
            )
            conn.execute("CREATE INDEX backups_project_idx ON backups(project_id)")
            conn.execute(
                "CREATE INDEX restores_cluster_identity_idx ON restores "
                "(cluster_id, db_host, db_port, database_name, restored_at DESC)"
            )
            conn.execute(
                "CREATE INDEX database_events_cluster_identity_idx ON database_events "
                "(cluster_id, db_host, db_port, database_name, sequence DESC)"
            )
            conn.execute(
                "ALTER TABLE restores ADD COLUMN state TEXT NOT NULL DEFAULT 'complete' "
                "CHECK (state IN ('complete', 'incomplete'))"
            )
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise BackupCatalogError("legacy catalog has invalid foreign key references")
            conn.execute("PRAGMA user_version = 16")
    finally:
        conn.execute(f"PRAGMA foreign_keys={foreign_keys_enabled}")
    return True


def _assert_single_head() -> str:
    script = ScriptDirectory.from_config(_alembic_config(Path(":memory:")))
    heads = script.get_heads()
    if len(heads) != 1:
        raise BackupCatalogError(
            f"catalog migrations must have exactly one Alembic head, found {heads!r}"
        )
    return heads[0]


def ensure_catalog_migrated(db_path: Path) -> None:
    """Apply or stamp the catalogue schema through Alembic."""
    head = _assert_single_head()
    config = _alembic_config(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        revision = catalog_revision(conn)
        if revision == head:
            return
        if revision is not None and revision != head:
            command.upgrade(config, "head")
            return
        if not _has_catalog_tables(conn):
            command.upgrade(config, "head")
            return
        _backup_catalog(db_path)
        repaired_v15 = _repair_known_v15_catalog(conn)
        if repaired_v15:
            command.stamp(config, "0001")
            command.upgrade(config, "head")
            return
        repaired_v16 = _repair_known_v16_catalog(conn)
        if repaired_v16:
            command.stamp(config, "0001")
            command.upgrade(config, "head")
            return
        try:
            verify_schema_equivalence(conn)
        except BackupCatalogError:
            if not _is_legacy_provenance_schema(conn):
                raise
            command.stamp(config, "0001")
            command.upgrade(config, "head")
            return
    finally:
        conn.close()
    command.stamp(config, head)


def assert_schema_metadata_matches_revision() -> None:
    """Reject unverified divergence between Alembic metadata and the revision."""
    head = _assert_single_head()
    if head != CATALOG_REVISION:
        raise BackupCatalogError(
            f"catalog revision constant {CATALOG_REVISION!r} does not match Alembic head {head!r}"
        )
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "fresh.sqlite3"
        config = _alembic_config(db_path)
        command.upgrade(config, "head")
        conn = sqlite3.connect(str(db_path))
        try:
            verify_schema_equivalence(conn)
            if catalog_revision(conn) != CATALOG_REVISION:
                raise BackupCatalogError(
                    f"fresh Alembic upgrade did not stamp revision {CATALOG_REVISION}"
                )
        finally:
            conn.close()
