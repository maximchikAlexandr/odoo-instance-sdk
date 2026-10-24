"""Bounded, safe backup catalogue read projections."""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import BackupCatalogError, BackupNotFoundError
from odoo_instance_sdk.internal.sanitize import sanitize_event_message, sanitize_last_error
from odoo_instance_sdk.models import BackupEventType, BackupFormat, BackupState
from odoo_instance_sdk.storage.catalog.helpers import (
    BackupEventPage,
    BackupEventSummary,
    BackupSummary,
    BackupSummaryPage,
    _translate_sqlite_error,
    catalogue_datetime,
    decode_catalog_cursor,
    encode_catalog_cursor,
)


class _BackupReadMixin:
    if TYPE_CHECKING:
        _conn: sqlite3.Connection

        @staticmethod
        def _canonical_backup_id(backup_id: str) -> str: ...

        def get_by_id(self, backup_id: str) -> sqlite3.Row | None: ...

    @staticmethod
    def _encode_backup_cursor(catalogue_time: str, backup_id: str) -> str:
        payload = json.dumps(
            {"catalogue_time": catalogue_time, "id": backup_id},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def _decode_backup_cursor(cls, cursor: str) -> tuple[str, str]:
        if not isinstance(cursor, str) or not cursor:
            raise BackupCatalogError("Backup cursor is invalid")
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
            )
        except (ValueError, TypeError, binascii.Error) as exc:
            raise BackupCatalogError("Backup cursor is invalid") from exc
        if not isinstance(payload, dict) or set(payload) != {"catalogue_time", "id"}:
            raise BackupCatalogError("Backup cursor is invalid")
        catalogue_time = payload["catalogue_time"]
        if not isinstance(catalogue_time, str) or not catalogue_time:
            raise BackupCatalogError("Backup cursor is invalid")
        try:
            backup_id = cls._canonical_backup_id(payload["id"])
        except BackupNotFoundError as exc:
            raise BackupCatalogError("Backup cursor is invalid") from exc
        return catalogue_time, backup_id

    @classmethod
    def _summary_from_row(cls, row: sqlite3.Row) -> BackupSummary:
        try:
            backup_id = uuid.UUID(str(row["id"]))
            backup_format = BackupFormat(str(row["format"]))
            state = BackupState(str(row["state"]))
        except (ValueError, TypeError, KeyError) as exc:
            raise BackupCatalogError("catalogue backup summary is malformed") from exc
        downloaded_at = (
            None
            if row["downloaded_at"] is None
            else catalogue_datetime(row["downloaded_at"], field="downloaded_at")
        )
        source_name = row["source_name"]
        safe_source = (
            sanitize_last_error(str(source_name))
            if isinstance(source_name, str) and source_name.strip()
            else None
        )
        source_label = safe_source.strip() if safe_source else "remote source"
        if "://" in source_label or source_label.startswith(("/", "~")):
            source_label = "remote source"
        project_id = row["project_id"]
        safe_project_value = (
            sanitize_last_error(str(project_id))
            if isinstance(project_id, str) and project_id.strip()
            else None
        )
        safe_project = safe_project_value.strip() if safe_project_value else None
        return BackupSummary(
            backup_id=backup_id,
            project_id=safe_project,
            project_label=safe_project,
            source_label=source_label,
            database_name=sanitize_last_error(str(row["database_name"])) or "unknown",
            format=backup_format,
            size_bytes=row["size_bytes"] if isinstance(row["size_bytes"], int) else None,
            state=state,
            downloaded_at=downloaded_at,
        )

    @_translate_sqlite_error
    def list_backup_summaries(  # noqa: C901 -- filter validation and bounded query stay together
        self,
        *,
        project_id: str | None = None,
        source_base_url: str | None = None,
        source_label: str | None = None,
        database_name: str | None = None,
        format: BackupFormat | str | None = None,
        state: BackupState | str | None = None,
        downloaded_after: datetime | None = None,
        downloaded_before: datetime | None = None,
        limit: int = 100,
        cursor: str | None = None,
        include_all_states: bool = True,
    ) -> BackupSummaryPage:
        """Return a bounded, allowlisted backup summary page.

        Every filter is bound as a value.  The internal point projection is
        intentionally not reused because it contains filesystem metadata.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise BackupCatalogError("Backup limit must be an integer between 1 and 1000")
        if type(include_all_states) is not bool:
            raise BackupCatalogError("include_all_states must be a boolean")
        try:
            backup_format = None if format is None else BackupFormat(format)
            backup_state = None if state is None else BackupState(state)
        except (TypeError, ValueError) as exc:
            raise BackupCatalogError("backup summary filter is invalid") from exc
        after_value = (
            None
            if downloaded_after is None
            else catalogue_datetime(
                downloaded_after.isoformat() if isinstance(downloaded_after, datetime) else None,
                field="downloaded_after",
            )
        )
        before_value = (
            None
            if downloaded_before is None
            else catalogue_datetime(
                downloaded_before.isoformat() if isinstance(downloaded_before, datetime) else None,
                field="downloaded_before",
            )
        )
        if after_value is not None and before_value is not None and after_value > before_value:
            raise BackupCatalogError("downloaded date range is invalid")
        after: tuple[str, str] | None = None
        if cursor is not None:
            after = self._decode_backup_cursor(cursor)
        clauses: list[str] = []
        params: list[str | int] = []
        if not include_all_states:
            clauses.append("b.state = ?")
            params.append(BackupState.AVAILABLE.value)
        if project_id is not None:
            clauses.append("b.project_id = ?")
            params.append(project_id)
        if source_base_url is not None:
            clauses.append("b.source_base_url = ?")
            params.append(source_base_url)
        if source_label is not None:
            clauses.append("b.source_name = ?")
            params.append(source_label)
        if database_name is not None:
            clauses.append("b.database_name = ?")
            params.append(database_name)
        if backup_format is not None:
            clauses.append("b.format = ?")
            params.append(backup_format.value)
        if backup_state is not None:
            clauses.append("b.state = ?")
            params.append(backup_state.value)
        if after_value is not None:
            clauses.append("julianday(b.downloaded_at) >= julianday(?)")
            params.append(after_value.isoformat())
        if before_value is not None:
            clauses.append("julianday(b.downloaded_at) <= julianday(?)")
            params.append(before_value.isoformat())
        if after is not None:
            clauses.append(
                "(julianday(COALESCE(b.downloaded_at, b.started_at)) < julianday(?) OR "
                "(julianday(COALESCE(b.downloaded_at, b.started_at)) = julianday(?) "
                "AND b.id > ?))"
            )
            params.extend((after[0], after[0], after[1]))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = (
            "SELECT b.id, b.project_id, b.source_name, b.database_name, b.format, "
            "b.size_bytes, b.state, b.downloaded_at, "
            "COALESCE(b.downloaded_at, b.started_at) AS catalogue_time "
            "FROM backups b LEFT JOIN projects p ON p.project_id = b.project_id"
            f"{where} ORDER BY julianday(catalogue_time) DESC, b.id ASC LIMIT ?"
        )
        rows = self._conn.execute(query, (*params, limit + 1)).fetchall()
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page_rows[-1]
            next_cursor = self._encode_backup_cursor(last["catalogue_time"], last["id"])
        return BackupSummaryPage(
            items=tuple(self._summary_from_row(row) for row in page_rows),
            next_cursor=next_cursor,
        )

    @_translate_sqlite_error
    def list_backup_events(
        self,
        backup_id: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> BackupEventPage:
        """Return only safe event fields for one backup detail view."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise BackupCatalogError("Backup event limit must be between 1 and 1000")
        canonical_id = self._canonical_backup_id(backup_id)
        if self.get_by_id(canonical_id) is None:
            raise BackupNotFoundError(f"Backup {canonical_id} not found in catalog")
        after = None if cursor is None else decode_catalog_cursor(cursor)
        after_sequence: int | None = None
        if after is not None:
            try:
                after_sequence = int(after[1])
            except ValueError as exc:
                raise BackupCatalogError("catalog cursor is invalid") from exc
        clauses = ["backup_id = ?"]
        params: list[str | int] = [canonical_id]
        if after is not None and after_sequence is not None:
            clauses.append(
                "(julianday(occurred_at) > julianday(?) OR "
                "(julianday(occurred_at) = julianday(?) AND sequence > ?))"
            )
            params.extend((after[0], after[0], after_sequence))
        rows = self._conn.execute(
            "SELECT sequence, event_type, occurred_at, validator, exit_code, message "
            "FROM backup_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY julianday(occurred_at) ASC, sequence ASC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page_rows[-1]
            next_cursor = encode_catalog_cursor(last["occurred_at"], last["sequence"])
        try:
            items = tuple(
                BackupEventSummary(
                    sequence=int(row["sequence"]),
                    event_type=BackupEventType(str(row["event_type"])),
                    occurred_at=catalogue_datetime(row["occurred_at"], field="event time"),
                    validator=(
                        sanitize_last_error(str(row["validator"]))
                        if row["validator"] is not None
                        else None
                    ),
                    exit_code=row["exit_code"],
                    message=sanitize_event_message(row["message"]),
                )
                for row in page_rows
            )
        except (TypeError, ValueError) as exc:
            raise BackupCatalogError("backup event is malformed") from exc
        return BackupEventPage(items=items, next_cursor=next_cursor)
