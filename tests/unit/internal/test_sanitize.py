from __future__ import annotations

import pytest

from odoo_instance_sdk.internal.sanitize import sanitize_last_error, sanitize_terminal_text
from tests.cases.normalization import SANITIZE_SECRET_CASES


@pytest.mark.parametrize("value, secrets", SANITIZE_SECRET_CASES)
def test_sanitizes_quoted_secrets_urls_and_multiline_diagnostics(
    value: str, secrets: tuple[str, ...]
) -> None:
    sanitized = sanitize_last_error(value)
    assert sanitized is not None
    for secret in secrets:
        assert secret not in sanitized
    assert "\n" not in sanitized


def test_sanitize_terminal_text_replaces_c0_c1_and_del() -> None:
    value = "before\x00\x1b[2J\x7f\x80\x9b31mafter"
    sanitized = sanitize_terminal_text(value)

    assert sanitized == r"before\x00\x1b[2J\x7f\x80\x9b31mafter"
    assert all(not 0 <= ord(char) <= 0x1F for char in sanitized)
    assert all(not 0x7F <= ord(char) <= 0x9F for char in sanitized)


def test_sanitize_terminal_text_can_preserve_document_line_feeds() -> None:
    value = "before\n\x00\x1b[2J\x9b31m\x7fafter"

    sanitized = sanitize_terminal_text(value, preserve_newlines=True)

    assert sanitized == "before\n\\x00\\x1b[2J\\x9b31m\\x7fafter"
