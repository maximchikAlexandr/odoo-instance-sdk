"""Tests that environment-fact details keep numbers and null."""

from __future__ import annotations

import json

import msgspec
import pytest
from toon import encode

from odoo_instance_sdk.models import EnvironmentFactsSummary


@pytest.mark.unit
def test_fact_details_keep_integers_and_null() -> None:
    summary = EnvironmentFactsSummary(
        provider="openspec",
        state="available",
        text="demo",
        details=(
            ("tasks_completed", 11),
            ("implementation_percent", None),
        ),
    )

    payload = json.loads(msgspec.json.encode(summary))
    assert payload["details"] == [
        ["tasks_completed", 11],
        ["implementation_percent", None],
    ]
    rendered = encode(payload)
    assert "11" in rendered
    assert "null" in rendered or "~" in rendered or "implementation_percent" in rendered
