from __future__ import annotations

import pytest

from odoo_instance_sdk.internal.redact import format_error
from tests.cases.normalization import REDACT_CASES


class _Exc(Exception):
    pass


@pytest.mark.parametrize("raw, expected", REDACT_CASES)
def test_redact_cases(raw: str, expected: str) -> None:
    assert format_error(_Exc(raw)) == expected
