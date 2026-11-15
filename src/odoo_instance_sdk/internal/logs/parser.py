"""Odoo 19 text logfile parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

_LINE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<pid>\d+) "
    r"(?P<level>[A-Z]+) "
    r"(?P<database>\S+) "
    r"(?P<logger>[^:]+): "
    r"(?P<message>.*)$"
)


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    """One logfile record and the byte range it occupies."""

    start: int
    end: int
    parsed: bool
    timestamp: str | None
    pid: int | None
    level: str | None
    database: str | None
    logger: str | None
    message: str


def parse_logfile(data: bytes) -> tuple[ParsedRecord, ...]:
    """Group Odoo 19 lines and attach continuation text to the open record."""
    records: list[ParsedRecord] = []
    current_start = 0
    current: ParsedRecord | None = None
    for start, end, text in _lines(data):
        matched = _LINE.fullmatch(text)
        if matched is not None:
            if current is not None:
                records.append(current)
            current_start = start
            current = _parsed(start, end, matched)
            continue
        if current is None:
            current_start = start
            current = ParsedRecord(
                start=start,
                end=end,
                parsed=False,
                timestamp=None,
                pid=None,
                level=None,
                database=None,
                logger=None,
                message=text,
            )
            continue
        current = ParsedRecord(
            start=current_start,
            end=end,
            parsed=current.parsed,
            timestamp=current.timestamp,
            pid=current.pid,
            level=current.level,
            database=current.database,
            logger=current.logger,
            message=f"{current.message}\n{text}",
        )
    if current is not None:
        records.append(current)
    return tuple(records)


def _lines(data: bytes) -> tuple[tuple[int, int, str], ...]:
    """Split logfile bytes into newline-terminated text lines with offsets."""
    lines: list[tuple[int, int, str]] = []
    start = 0
    while start < len(data):
        newline = data.find(b"\n", start)
        end = len(data) if newline < 0 else newline
        lines.append(
            (start, end + (0 if newline < 0 else 1), data[start:end].decode("utf-8", "replace"))
        )
        if newline < 0:
            break
        start = newline + 1
    return tuple(lines)


def _parsed(start: int, end: int, matched: re.Match[str]) -> ParsedRecord:
    """Build one record from a matching Odoo prefix."""
    return ParsedRecord(
        start=start,
        end=end,
        parsed=True,
        timestamp=_utc_timestamp(matched.group("timestamp")),
        pid=int(matched.group("pid")),
        level=matched.group("level"),
        database=matched.group("database"),
        logger=matched.group("logger"),
        message=matched.group("message"),
    )


def _utc_timestamp(value: str) -> str:
    """Convert a naive Odoo timestamp from the host zone to UTC RFC 3339."""
    parsed = datetime.strptime(f"{value}+0000", "%Y-%m-%d %H:%M:%S,%f%z")
    local = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo or UTC)
    utc = local.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
