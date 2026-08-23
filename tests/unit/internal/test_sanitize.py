from __future__ import annotations

import pytest

from odoo_instance_sdk.internal.sanitize import sanitize_last_error
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
