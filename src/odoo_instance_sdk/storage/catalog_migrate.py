"""Alembic-backed catalogue schema migration and verification."""

from __future__ import annotations

import sqlite3
import tempfile
from importlib.resources import files
from pathlib import Path

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

CATALOG_REVISION = "0001"


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


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[tuple[str, str, int, int], ...]:
    return tuple(
        (str(row[1]), str(row[2]), int(row[3]), int(row[5]))
        for row in conn.execute(f"PRAGMA table_info({table})")
    )


def _foreign_keys(conn: sqlite3.Connection, table: str) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (str(row[3]), str(row[2]), str(row[4]))
        for row in conn.execute(f"PRAGMA foreign_key_list({table})")
    )


def _index_names(conn: sqlite3.Connection) -> frozenset[str]:
    names = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    return frozenset(names)


def _schema_fingerprint(
    conn: sqlite3.Connection,
) -> tuple[
    tuple[tuple[str, tuple[tuple[str, str, int, int], ...]], ...],
    frozenset[str],
    tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...],
    bool,
]:
    tables = tuple(
        (table, _table_columns(conn, table))
        for table in sorted(CATALOG_TABLES)
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )
    indexes = _index_names(conn)
    foreign_keys = tuple(
        (table, _foreign_keys(conn, table))
        for table in ("environment_events", "environment_copy_journal", "backups")
    )
    view_exists = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='environment_runtime'"
        ).fetchone()
        is not None
    )
    return tables, indexes, foreign_keys, view_exists


def _reference_fingerprint() -> tuple[
    tuple[tuple[str, tuple[tuple[str, str, int, int], ...]], ...],
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


def verify_schema_equivalence(conn: sqlite3.Connection) -> None:
    """Verify that an existing catalogue matches the first Alembic revision."""
    actual = _schema_fingerprint(conn)
    expected = _reference_fingerprint()
    if actual != expected:
        raise BackupCatalogError("catalog schema is not equivalent to the first Alembic revision")


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
        verify_schema_equivalence(conn)
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
                raise BackupCatalogError("fresh Alembic upgrade did not stamp revision 0001")
        finally:
            conn.close()
