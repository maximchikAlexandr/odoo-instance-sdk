"""Typed, bounded recovery evidence for COPY database replacement."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, cast

import msgspec

from odoo_instance_sdk.exceptions import EnvironmentConflictError

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue


RECOVERY_VERSION = 1
_LEGACY_PREFIX = "copy replacement cleanup_failed; retained="
_SENSITIVE_TEXT = re.compile(r"(?:password|secret|token|authorization|api[_-]?key)\s*=", re.I)
type RowScalar = str | None


class RowLookup(Protocol):
    def __getitem__(self, key: str) -> RowScalar: ...


type RecoveryRow = Mapping[str, RowScalar] | RowLookup


class CopyReplacementRecovery(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """Secret-free identities and topology needed by guarded replacement repair."""

    version: int
    environment_id: uuid.UUID
    backup_id: uuid.UUID
    target_database: str
    rollback_database: str
    filestore: str
    rollback_filestore: str
    cluster_id: str
    data_directory: str
    previous_backup_id: uuid.UUID | None = None
    stage: str = "preflight"
    published: bool = False
    target_present: bool | None = None
    rollback_present: bool | None = None
    rollback_filestore_present: bool | None = None
    restore_stage_id: str | None = None
    restore_stage_elapsed: float | None = None


def encode_recovery(value: CopyReplacementRecovery) -> str:
    if value.version != RECOVERY_VERSION:
        raise ValueError("unsupported replacement recovery version")
    encoded = msgspec.json.encode(value).decode("utf-8")
    if len(encoded.encode("utf-8")) > 16_384:
        raise ValueError("replacement recovery is too large")
    return encoded


def decode_recovery(value: str | None) -> CopyReplacementRecovery:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 16_384:
        raise ValueError("replacement recovery is invalid")
    try:
        recovery = msgspec.json.decode(value.encode("utf-8"), type=CopyReplacementRecovery)
    except (msgspec.DecodeError, msgspec.ValidationError, ValueError, TypeError) as exc:
        raise ValueError("replacement recovery is invalid") from exc
    if recovery.version != RECOVERY_VERSION:
        raise ValueError("unsupported replacement recovery version")
    if (
        not recovery.target_database
        or not recovery.rollback_database
        or not recovery.filestore
        or not recovery.rollback_filestore
        or not recovery.cluster_id
        or not recovery.data_directory
        or "/" in recovery.rollback_filestore
        or "\\" in recovery.rollback_filestore
        or "/" in recovery.filestore
        or "\\" in recovery.filestore
    ):
        raise ValueError("replacement recovery identity is invalid")
    return recovery


def recovery_mapping(value: CopyReplacementRecovery) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", msgspec.to_builtins(value))


def _legacy_mapping(value: str) -> dict[str, JsonValue] | None:  # noqa: C901
    if not isinstance(value, str) or not value.startswith(_LEGACY_PREFIX):
        return None
    if _SENSITIVE_TEXT.search(value):
        return None
    remainder = value[len(_LEGACY_PREFIX) :]
    try:
        payload, suffix = remainder.split(";", 1)
        decoded = json.loads(payload)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    allowed = {
        "backup_id",
        "previous_backup_id",
        "target_database",
        "rollback_database",
        "rollback_filestore",
        "published",
        "target_present",
        "rollback_present",
        "rollback_filestore_present",
        "stage",
    }
    if set(decoded) - allowed:
        return None
    required = {"backup_id", "target_database", "rollback_database"}
    if required - set(decoded):
        return None
    if not isinstance(suffix, str) or len(suffix) > 512:
        return None
    try:
        uuid.UUID(str(decoded["backup_id"]))
        if "previous_backup_id" in decoded:
            uuid.UUID(str(decoded["previous_backup_id"]))
    except (ValueError, TypeError, AttributeError):
        return None
    for key in ("target_database", "rollback_database"):
        if not isinstance(decoded[key], str) or not decoded[key].strip():
            return None
    rollback_filestore = decoded.get("rollback_filestore")
    if rollback_filestore is not None and (
        not isinstance(rollback_filestore, str)
        or not rollback_filestore
        or "/" in rollback_filestore
        or "\\" in rollback_filestore
    ):
        return None
    for key in ("published", "target_present", "rollback_present", "rollback_filestore_present"):
        if key in decoded and decoded[key] is not None and not isinstance(decoded[key], bool):
            return None
    return cast("dict[str, JsonValue]", decoded)


def _row_value(row: RecoveryRow, key: str) -> RowScalar:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def recovery_from_row(
    row: RecoveryRow | None, *, allow_legacy: bool = False
) -> CopyReplacementRecovery | dict[str, JsonValue] | None:
    if row is None:
        return None
    raw = _row_value(row, "recovery_json")
    if raw is not None:
        try:
            return decode_recovery(raw)
        except ValueError as exc:
            raise EnvironmentConflictError("replacement_conflict", str(exc)) from exc
    if allow_legacy:
        last_error = _row_value(row, "last_error")
        return _legacy_mapping(last_error) if last_error is not None else None
    return None


def recovery_values(row: RecoveryRow | None, *, allow_legacy: bool = False) -> dict[str, JsonValue]:
    value = recovery_from_row(row, allow_legacy=allow_legacy)
    if isinstance(value, CopyReplacementRecovery):
        return recovery_mapping(value)
    return value or {}


__all__ = [
    "RECOVERY_VERSION",
    "CopyReplacementRecovery",
    "decode_recovery",
    "encode_recovery",
    "recovery_from_row",
    "recovery_mapping",
    "recovery_values",
]
