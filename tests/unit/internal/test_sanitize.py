from __future__ import annotations

from odoo_instance_sdk.internal.sanitize import sanitize_last_error


def test_sanitizes_quoted_secrets_urls_and_multiline_diagnostics() -> None:
    value = (
        'password="my secret" token=abc123\n'
        'postgresql://alice:secret@example.test/db api_key="another key"'
    )
    sanitized = sanitize_last_error(value)
    assert sanitized is not None
    assert "my secret" not in sanitized
    assert "abc123" not in sanitized
    assert "alice:secret" not in sanitized
    assert "another key" not in sanitized
    assert "\n" not in sanitized
