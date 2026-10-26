"""Typed, revision-safe access to non-secret catalogue settings."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from odoo_instance_sdk.exceptions import BackupCatalogError

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue

_MAX_KEY_BYTES = 512
_MAX_VALUE_BYTES = 64 * 1024
_SECRET_KEY = re.compile(
    r"(?:^|[-_ .])(?:password|passwd|pwd|secret|token|cookie|jwt|oauth|api[-_ ]?key|"
    r"private[-_ ]?key|credential|credentials)(?:$|[-_ .])",
    re.IGNORECASE,
)
_ASSIGNEE_PREFIX = "gitlab.mr.default_assignee:"


@dataclass(frozen=True, slots=True)
class CatalogSetting:
    key: str
    value: JsonValue
    revision: int
    created_at: datetime
    updated_at: datetime

    @property
    def route_id(self) -> str:
        return _route_id(self.key, self.revision)

    @property
    def value_json(self) -> str:
        return json.dumps(self.value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _route_id(key: str, revision: int) -> str:
    import base64

    return (
        base64.urlsafe_b64encode(
            json.dumps([key, revision], ensure_ascii=False, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def _object_no_duplicates(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _assignee_key(key: str) -> bool:
    if not key.startswith(_ASSIGNEE_PREFIX):
        return False
    raw = key.removeprefix(_ASSIGNEE_PREFIX)
    try:
        url = urlsplit(raw)
        host = url.hostname
    except ValueError as exc:
        raise BackupCatalogError("MR assignee setting key is invalid") from exc
    if (
        url.scheme != "https"
        or host is None
        or url.netloc != url.netloc.lower()
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or not url.path.strip("/")
        or url.path != "/" + url.path.strip("/")
        or any(part in {".", ".."} for part in url.path.split("/"))
        or any(char.isspace() for char in raw)
    ):
        raise BackupCatalogError("MR assignee setting key is invalid")
    return True


def _canonical_value(key: str, value: JsonValue) -> str:
    if _assignee_key(key) and not (
        (type(value) is int and value > 0)
        or (type(value) is str and bool(value.strip()) and not any(c.isspace() for c in value))
    ):
        raise BackupCatalogError("MR assignee setting value is invalid")
    try:
        _validate_json_value(value)
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise BackupCatalogError("setting value must be bounded canonical JSON") from exc
    if len(serialized.encode("utf-8")) > _MAX_VALUE_BYTES:
        raise BackupCatalogError("setting value exceeds the catalogue limit")
    return serialized


def _validate_json_value(value: JsonValue, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _validate_json_value(item, depth + 1)
        return
    raise ValueError


def _decode_value(raw: str) -> JsonValue:
    try:
        return cast(
            "JsonValue",
            json.loads(
                raw,
                object_pairs_hook=_object_no_duplicates,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            ),
        )
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise BackupCatalogError("catalogue setting JSON is invalid") from exc


def _setting(row: tuple[object, ...]) -> CatalogSetting:
    key, raw, revision, created, updated = row
    if not (
        isinstance(key, str)
        and isinstance(raw, str)
        and type(revision) is int
        and isinstance(created, str)
        and isinstance(updated, str)
    ):
        raise BackupCatalogError("catalogue setting row is invalid")
    try:
        return CatalogSetting(
            key=key,
            value=_decode_value(raw),
            revision=revision,
            created_at=datetime.fromisoformat(created).replace(tzinfo=UTC),
            updated_at=datetime.fromisoformat(updated).replace(tzinfo=UTC),
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise BackupCatalogError("catalogue setting row is invalid") from exc


class _SettingsMixin:
    _conn: sqlite3.Connection

    def create_setting(self: _SettingsMixin, key: str, value: JsonValue) -> CatalogSetting:
        normalized_key = _validate_key(key)
        serialized = _canonical_value(normalized_key, value)
        now = datetime.now(UTC).isoformat()
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO settings(key,value_json,revision,created_at,updated_at) VALUES(?,?,1,?,?)",
                    (normalized_key, serialized, now, now),
                )
        except Exception as exc:
            if isinstance(exc, BackupCatalogError):
                raise
            raise BackupCatalogError("catalogue setting could not be created") from exc
        result = self.get_setting(normalized_key)
        if result is None:
            raise BackupCatalogError("catalogue setting disappeared after creation")
        return result

    def get_setting(self: _SettingsMixin, key: str) -> CatalogSetting | None:
        row = self._conn.execute(
            "SELECT key,value_json,revision,created_at,updated_at FROM settings WHERE key=?", (key,)
        ).fetchone()
        return None if row is None else _setting(tuple(row))

    def list_settings(self: _SettingsMixin) -> tuple[CatalogSetting, ...]:
        return tuple(
            _setting(tuple(row))
            for row in self._conn.execute(
                "SELECT key,value_json,revision,created_at,updated_at FROM settings ORDER BY key"
            )
        )

    def update_setting(
        self: _SettingsMixin,
        key: str,
        value: JsonValue,
        *,
        expected_revision: int,
        new_key: str | None = None,
    ) -> CatalogSetting:
        normalized_key = _validate_key(key)
        normalized_new_key = _validate_key(new_key) if new_key is not None else normalized_key
        if expected_revision < 1:
            raise BackupCatalogError("setting revision is invalid")
        serialized = _canonical_value(normalized_key, value)
        now = datetime.now(UTC).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE settings SET key=?,value_json=?,revision=revision+1,updated_at=? "
                "WHERE key=? AND revision=?",
                (normalized_new_key, serialized, now, normalized_key, expected_revision),
            )
            if cursor.rowcount != 1:
                raise BackupCatalogError("setting changed; reload and retry")
        result = self.get_setting(normalized_new_key)
        if result is None:
            raise BackupCatalogError("catalogue setting disappeared after update")
        return result

    def delete_setting(self: _SettingsMixin, key: str, *, expected_revision: int) -> None:
        normalized_key = _validate_key(key)
        if expected_revision < 1:
            raise BackupCatalogError("setting revision is invalid")
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM settings WHERE key=? AND revision=?",
                (normalized_key, expected_revision),
            )
            if cursor.rowcount != 1:
                raise BackupCatalogError("setting changed; reload and retry")


def _validate_key(key: str) -> str:
    if not isinstance(key, str) or not key.strip() or len(key.encode("utf-8")) > _MAX_KEY_BYTES:
        raise BackupCatalogError("setting key must be non-empty and within the catalogue limit")
    if key != key.strip() or _SECRET_KEY.search(key):
        raise BackupCatalogError("setting key is invalid or classified as secret-bearing")
    _assignee_key(key)
    return key
