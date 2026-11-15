"""Run files, generations, marks, and the structured Odoo scan."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from odoo_instance_sdk.exceptions import LogQueryError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.logs.cursor import (
    CursorPosition,
    decode_cursor,
    encode_cursor,
    fingerprint,
)
from odoo_instance_sdk.internal.logs.parser import ParsedRecord, parse_logfile
from odoo_instance_sdk.internal.proc.terminate import is_process_alive
from odoo_instance_sdk.models.logs import LogEvent, LogMark, LogPage

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_ROTATED = re.compile(r"odoo\.log\.[0-9]+\Z")


@dataclass(frozen=True, slots=True)
class LogScope:
    """Identity bound into every cursor and event."""

    project_id: str
    environment_id: str
    environment_root: Path


@dataclass(frozen=True, slots=True)
class LogFilters:
    """The closed set of structured search filters."""

    text: str | None = None
    levels: tuple[str, ...] = ()
    logger_prefix: str | None = None
    database: str | None = None
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True, slots=True)
class _Generation:
    id: int
    dev: int
    ino: int
    name: str


@dataclass(frozen=True, slots=True)
class _Run:
    run_id: str
    directory: Path
    started_at: str
    state: str
    pid: int | None
    create_time: float | None


@dataclass(frozen=True, slots=True)
class _Located:
    generation: _Generation
    record: ParsedRecord
    data: bytes


def select_run(scope: LogScope, selector: str | None) -> _Run:
    """Resolve ``current``, ``last``, an omitted selector, or one owned run id."""
    runs = _runs(scope.environment_root)
    active = [run for run in runs if run.state == "running"]
    if selector is None:
        chosen = active or runs
        if not chosen:
            raise LogQueryError("run_not_found", "run was not found")
        return max(chosen, key=lambda run: run.started_at)
    if selector == "current":
        if not active:
            raise LogQueryError("run_not_found", "run was not found")
        return max(active, key=lambda run: run.started_at)
    if selector == "last":
        if not runs:
            raise LogQueryError("run_not_found", "run was not found")
        return max(runs, key=lambda run: run.started_at)
    matched = [run for run in runs if run.run_id == selector]
    if len(matched) == 1:
        return matched[0]
    raise LogQueryError("run_not_found", "run was not found")


def search_page(
    scope: LogScope,
    selector: str | None,
    filters: LogFilters,
    *,
    after: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> LogPage:
    """Return one stable page. New records stay outside a frozen window."""
    run = select_run(scope, selector)
    generations = _observe(run.directory)
    after_position = _checked_cursor(scope, run, generations, after) if after else None
    window = (
        _checked_cursor(scope, run, generations, until)
        if until
        else _high_water(scope, run, generations)
    )
    if after_position is not None and _position_key(after_position) > _position_key(window):
        return _empty_page(encode_cursor(window), encode_cursor(window))
    matches: list[LogEvent] = []
    examined = after_position
    located = _records(run.directory, generations)
    stopped = False
    for item in located:
        if not _in_window(item, after_position, window):
            continue
        examined = _position_after(scope, run, item)
        if _matches(item.record, filters):
            matches.append(_event(scope, run.run_id, item.record, encode_cursor(examined)))
            if limit is not None and len(matches) >= limit:
                stopped = True
                break
    has_more = stopped and any(
        _in_window(item, examined, window) and _starts_after(item, examined) for item in located
    )
    next_cursor = encode_cursor(examined or window) if has_more else encode_cursor(window)
    return LogPage(
        events=tuple(matches),
        next_cursor=next_cursor,
        has_more=has_more,
        window_end_cursor=encode_cursor(window),
        complete=not has_more,
    )


def iter_follow(
    scope: LogScope,
    selector: str | None,
    filters: LogFilters,
    *,
    after: str | None = None,
) -> Iterator[LogEvent]:
    """Yield matching events, then poll for records appended after them."""
    cursor = after
    while True:
        page = search_page(scope, selector, filters, after=cursor)
        yield from page.events
        cursor = page.next_cursor
        if page.events:
            continue
        time.sleep(0.2)


def high_water_cursor(scope: LogScope, selector: str | None) -> tuple[str, str]:
    """Return the current boundary cursor and its run id."""
    run = select_run(scope, selector)
    generations = _observe(run.directory)
    position = _high_water(scope, run, generations)
    return encode_cursor(position), run.run_id


def create_mark(
    scope: LogScope,
    name: str,
    selector: str | None,
    *,
    replace: bool,
) -> LogMark:
    """Read to the current boundary, then atomically store a private mark."""
    if _NAME.fullmatch(name) is None:
        raise LogQueryError("mark_not_found", "mark was not found")
    target = scope.environment_root / "logs" / "marks" / f"{name}.json"
    if target.exists() and not replace:
        raise LogQueryError("mark_already_exists", "mark already exists")
    cursor, run_id = high_water_cursor(scope, selector)
    mark = LogMark(
        name=name,
        cursor=cursor,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        environment_id=scope.environment_id,
        run_id=run_id,
        project_id=scope.project_id,
    )
    _write_json(target, _mark_payload(mark))
    os.chmod(target.parent, 0o700)
    os.chmod(target.parent.parent, 0o700)
    return mark


def show_mark(scope: LogScope, name: str) -> LogMark:
    """Return one stored mark."""
    return _read_mark(_mark_path(scope, name))


def list_marks(scope: LogScope) -> tuple[LogMark, ...]:
    """Return every mark for the environment in name order."""
    directory = scope.environment_root / "logs" / "marks"
    if not directory.is_dir():
        return ()
    marks = [
        _read_mark(path)
        for path in sorted(directory.glob("*.json"))
        if path.is_file() and not path.is_symlink()
    ]
    return tuple(sorted(marks, key=lambda mark: mark.name))


def delete_mark(scope: LogScope, name: str) -> None:
    """Remove one mark without creating a database."""
    target = _mark_path(scope, name)
    if target.is_symlink() or not target.is_file():
        raise LogQueryError("mark_not_found", "mark was not found")
    target.unlink()


def resolve_mark_cursor(scope: LogScope, name: str) -> str:
    """Return the cursor stored for a mark."""
    return show_mark(scope, name).cursor


def write_run_meta(directory: Path, payload: dict[str, JsonValue]) -> None:
    """Atomically store non-secret run metadata."""
    _write_json(directory / "meta.json", payload)


def finish_pid(root: Path, pid: int, *, state: str, exit_code: int | None) -> None:
    """Record a terminal state for the run that belongs to one process id."""
    logs = root / "logs"
    if not logs.is_dir():
        return
    ended_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    for child in logs.iterdir():
        meta_path = child / "meta.json"
        if child.is_symlink() or meta_path.is_symlink() or not child.is_dir():
            continue
        payload = _read_json(meta_path)
        if not isinstance(payload, dict) or payload.get("state") != "running":
            continue
        if payload.get("pid") != pid:
            continue
        payload["state"] = state
        payload["exit_code"] = exit_code
        payload["ended_at"] = ended_at
        _write_json(meta_path, payload)


def run_logfile(scope: LogScope, selector: str | None) -> Path:
    """Return the raw logfile for a selected owned run."""
    run = select_run(scope, selector)
    path = run.directory / "odoo.log"
    if path.is_symlink() or not path.is_file():
        raise LogQueryError("run_not_found", "run was not found")
    return path


def _runs(root: Path) -> tuple[_Run, ...]:
    logs = root / "logs"
    if not logs.is_dir():
        return ()
    runs: list[_Run] = []
    for child in logs.iterdir():
        meta_path = child / "meta.json"
        if not child.is_dir() or child.is_symlink() or not meta_path.is_file():
            continue
        payload = _read_json(meta_path)
        if not isinstance(payload, dict) or payload.get("run_id") != child.name:
            continue
        pid = payload.get("pid")
        create_time = payload.get("process_create_time")
        state = str(payload.get("state") or "stale")
        if state == "running" and isinstance(pid, int) and not isinstance(pid, bool):
            try:
                alive = is_process_alive(
                    pid,
                    expected_create_time=(
                        float(create_time) if isinstance(create_time, (int, float)) else None
                    ),
                )
            except RuntimeError:
                alive = False
            if not alive:
                state = "stale"
                _write_json(meta_path, {**payload, "state": "stale"})
        runs.append(
            _Run(
                run_id=child.name,
                directory=child,
                started_at=str(payload.get("started_at") or ""),
                state=state,
                pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
                create_time=create_time if isinstance(create_time, (int, float)) else None,
            )
        )
    return tuple(runs)


def _observe(run_dir: Path) -> tuple[_Generation, ...]:
    existing = _load_generations(run_dir)
    by_inode = {(item.dev, item.ino): item for item in existing}
    next_id = max((item.id for item in existing), default=0) + 1
    for path in _log_files(run_dir):
        stat = path.stat()
        key = (stat.st_dev, stat.st_ino)
        current = by_inode.get(key)
        if current is None:
            current = _Generation(next_id, stat.st_dev, stat.st_ino, path.name)
            next_id += 1
        elif current.name != path.name:
            current = _Generation(current.id, current.dev, current.ino, path.name)
        by_inode[key] = current
    saved = tuple(sorted(by_inode.values(), key=lambda item: item.id))
    _write_json(
        run_dir / "generations.json",
        {
            "schema_version": 1,
            "generations": [
                {"id": item.id, "dev": item.dev, "ino": item.ino, "name": item.name}
                for item in saved
            ],
        },
    )
    return saved


def _load_generations(run_dir: Path) -> tuple[_Generation, ...]:
    payload = _read_json(run_dir / "generations.json")
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("generations")
    if not isinstance(raw, list):
        return ()
    generations: list[_Generation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        generation_id = item.get("id")
        dev = item.get("dev")
        ino = item.get("ino")
        name = item.get("name")
        if (
            type(generation_id) is int
            and type(dev) is int
            and type(ino) is int
            and isinstance(name, str)
        ):
            generations.append(_Generation(generation_id, dev, ino, name))
    return tuple(generations)


def _log_files(run_dir: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    active = run_dir / "odoo.log"
    if active.is_file() and not active.is_symlink():
        files.append(active)
    for path in sorted(run_dir.iterdir()):
        if path.is_file() and not path.is_symlink() and _ROTATED.fullmatch(path.name):
            files.append(path)
    return tuple(files)


def _records(run_dir: Path, generations: tuple[_Generation, ...]) -> tuple[_Located, ...]:
    located: list[_Located] = []
    for generation in generations:
        path = _generation_path(run_dir, generations, generation.id)
        if path is None:
            continue
        data = _read_bytes(path)
        located.extend(_Located(generation, record, data) for record in parse_logfile(data))
    return tuple(located)


def _generation_path(
    run_dir: Path, generations: tuple[_Generation, ...], generation_id: int
) -> Path | None:
    matched = next((item for item in generations if item.id == generation_id), None)
    if matched is None:
        return None
    for path in _log_files(run_dir):
        stat = path.stat()
        if (stat.st_dev, stat.st_ino) == (matched.dev, matched.ino):
            return path
    return None


def _checked_cursor(
    scope: LogScope,
    run: _Run,
    generations: tuple[_Generation, ...],
    token: str,
) -> CursorPosition:
    position = decode_cursor(
        token,
        project_id=scope.project_id,
        environment_id=scope.environment_id,
        run_id=run.run_id,
    )
    path = _generation_path(run.directory, generations, position.generation)
    if path is None:
        earliest = _earliest_cursor(scope, run, generations)
        details = {"earliest_cursor": earliest} if earliest is not None else None
        raise LogQueryError(
            "cursor_expired",
            "cursor generation is no longer retained",
            details=details,
        )
    data = _read_bytes(path)
    if len(data) < position.offset or fingerprint(data, position.offset) != position.fingerprint:
        raise LogQueryError("source_reset", "log source was reset")
    return position


def _earliest_cursor(
    scope: LogScope, run: _Run, generations: tuple[_Generation, ...]
) -> str | None:
    for generation in generations:
        if _generation_path(run.directory, generations, generation.id) is None:
            continue
        return encode_cursor(
            CursorPosition(
                scope.project_id,
                scope.environment_id,
                run.run_id,
                generation.id,
                0,
                "",
            )
        )
    return None


def _high_water(scope: LogScope, run: _Run, generations: tuple[_Generation, ...]) -> CursorPosition:
    available = [
        item
        for item in generations
        if _generation_path(run.directory, generations, item.id) is not None
    ]
    if not available:
        raise LogQueryError("source_reset", "log source was reset")
    generation = max(available, key=lambda item: item.id)
    active = _generation_path(run.directory, generations, generation.id)
    if active is None:
        raise LogQueryError("source_reset", "log source was reset")
    data = _read_bytes(active)
    records = parse_logfile(data)
    offset = records[-1].end if records else 0
    return CursorPosition(
        scope.project_id,
        scope.environment_id,
        run.run_id,
        generation.id,
        offset,
        fingerprint(data, offset),
    )


def _position_after(scope: LogScope, run: _Run, item: _Located) -> CursorPosition:
    return CursorPosition(
        scope.project_id,
        scope.environment_id,
        run.run_id,
        item.generation.id,
        item.record.end,
        fingerprint(item.data, item.record.end),
    )


def _position_key(position: CursorPosition) -> tuple[int, int]:
    return position.generation, position.offset


def _in_window(item: _Located, after: CursorPosition | None, window: CursorPosition) -> bool:
    start = (item.generation.id, item.record.start)
    if after is not None and start < (after.generation, after.offset):
        return False
    return start < (window.generation, window.offset)


def _starts_after(item: _Located, position: CursorPosition | None) -> bool:
    if position is None:
        return True
    return (item.generation.id, item.record.start) >= (position.generation, position.offset)


def _matches(record: ParsedRecord, filters: LogFilters) -> bool:
    if filters.text and filters.text.casefold() not in record.message.casefold():
        return False
    if filters.levels and (record.level or "").casefold() not in {
        level.casefold() for level in filters.levels
    }:
        return False
    if filters.logger_prefix and not (record.logger or "").startswith(filters.logger_prefix):
        return False
    if filters.database and record.database != filters.database:
        return False
    if filters.since is not None or filters.until is not None:
        if record.timestamp is None:
            return False
        instant = datetime.fromisoformat(record.timestamp.replace("Z", "+00:00"))
        if filters.since is not None and instant < filters.since.astimezone(UTC):
            return False
        if filters.until is not None and instant > filters.until.astimezone(UTC):
            return False
    return True


def _event(scope: LogScope, run_id: str, record: ParsedRecord, cursor: str) -> LogEvent:
    return LogEvent(
        schema_version=1,
        source="odoo",
        timestamp=record.timestamp,
        level=record.level,
        message=record.message,
        project_id=scope.project_id,
        environment_id=scope.environment_id,
        run_id=run_id,
        pid=record.pid,
        database=record.database,
        logger=record.logger,
        parsed=record.parsed,
        attributes={},
        cursor=cursor,
    )


def _empty_page(cursor: str, window: str) -> LogPage:
    return LogPage(
        events=(),
        next_cursor=cursor,
        has_more=False,
        window_end_cursor=window,
        complete=True,
    )


def _mark_path(scope: LogScope, name: str) -> Path:
    if _NAME.fullmatch(name) is None:
        raise LogQueryError("mark_not_found", "mark was not found")
    return scope.environment_root / "logs" / "marks" / f"{name}.json"


def _mark_payload(mark: LogMark) -> dict[str, JsonValue]:
    return {
        "name": mark.name,
        "cursor": mark.cursor,
        "created_at": mark.created_at,
        "environment_id": mark.environment_id,
        "run_id": mark.run_id,
        "project_id": mark.project_id,
    }


def _read_mark(path: Path) -> LogMark:
    if path.is_symlink() or not path.is_file():
        raise LogQueryError("mark_not_found", "mark was not found")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise LogQueryError("mark_not_found", "mark was not found")
    try:
        return LogMark(
            name=str(payload["name"]),
            cursor=str(payload["cursor"]),
            created_at=str(payload["created_at"]),
            environment_id=str(payload["environment_id"]),
            run_id=str(payload["run_id"]),
            project_id=str(payload["project_id"]),
        )
    except KeyError as exc:
        raise LogQueryError("mark_not_found", "mark was not found") from exc


def _read_bytes(path: Path) -> bytes:
    """Read one regular log file or raise a path-free query error."""
    try:
        if path.is_symlink():
            raise LogQueryError("source_reset", "log source was reset")
        return path.read_bytes()
    except OSError as exc:
        raise LogQueryError("source_reset", "log source was reset") from exc


def _read_json(path: Path) -> JsonValue:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return cast("JsonValue", json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: JsonValue) -> None:
    if any(item.is_symlink() for item in (path, path.parent, path.parent.parent)):
        raise LogQueryError("source_reset", "log source was reset")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, 0o600)
    finally:
        if temporary is not None:
            os.unlink(temporary)
