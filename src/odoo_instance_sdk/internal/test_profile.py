"""Validation and deterministic projection for native Odoo test statistics."""

from __future__ import annotations

import msgspec

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.models import OdooTestProfile, OdooTestProfileEntry


def _test_profile_entry(item: JsonValue) -> OdooTestProfileEntry:
    if not isinstance(item, dict):
        raise TypeError("profile entry must be a mapping")
    return msgspec.convert(item, type=OdooTestProfileEntry)


def test_profile(payload: dict[str, JsonValue]) -> OdooTestProfile:
    raw_result = payload.get("result")
    raw_entries = raw_result.get("profile") if isinstance(raw_result, dict) else None
    if not isinstance(raw_entries, list):
        raise ConfigError("native Odoo test stats are invalid")
    try:
        entries = [_test_profile_entry(item) for item in raw_entries]
        ordered = tuple(sorted(entries, key=lambda entry: (-entry.duration, entry.test_id)))
        return OdooTestProfile(
            total_duration=sum(entry.duration for entry in ordered),
            total_query_count=sum(entry.query_count for entry in ordered),
            entries=ordered,
        )
    except (msgspec.ValidationError, TypeError, ValueError) as exc:
        raise ConfigError("native Odoo test stats are invalid") from exc
