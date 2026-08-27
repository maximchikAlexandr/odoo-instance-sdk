from __future__ import annotations

import json
from typing import Any, cast

from toon import DecodeOptions, decode, encode

from tests.fixtures.output_envelopes import OUTPUT_FIXTURES


def _json_safe(value: dict[str, Any]) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(json.dumps(value, ensure_ascii=False)))


def test_committed_envelope_v1_snapshot_v2_fixtures_round_trip_strict_toon() -> None:
    """Pin the supported TOON v4.1 (2026-07-26) envelope syntax boundary."""
    for fixture in OUTPUT_FIXTURES:
        expected = _json_safe(fixture)
        encoded = encode(expected)
        decoded = decode(encoded, DecodeOptions(indent=2, strict=True))
        assert decoded == expected
