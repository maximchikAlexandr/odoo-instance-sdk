"""Public structured Odoo log models."""

from __future__ import annotations

import msgspec


class LogEvent(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One immutable Odoo logfile event."""

    schema_version: int
    source: str
    timestamp: str | None
    level: str | None
    message: str
    project_id: str
    environment_id: str
    run_id: str
    pid: int | None
    database: str | None
    logger: str | None
    parsed: bool
    attributes: dict[str, str]
    cursor: str


class LogPage(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One bounded search page."""

    events: tuple[LogEvent, ...]
    next_cursor: str
    has_more: bool
    window_end_cursor: str
    complete: bool


class LogCursor(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """The high-water cursor for one run."""

    cursor: str
    run_id: str


class LogMark(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """A named cursor stored for one environment."""

    name: str
    cursor: str
    created_at: str
    environment_id: str
    run_id: str
    project_id: str


class LogMarkList(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """The marks stored for one environment."""

    marks: tuple[LogMark, ...]


class LogMarkDeletion(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Confirmation that one mark name was removed."""

    name: str
    deleted: bool
