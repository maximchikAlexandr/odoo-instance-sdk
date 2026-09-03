from __future__ import annotations

import pytest

from odoo_instance_sdk.exceptions import ConfigError, InvalidBaseUrlError
from odoo_instance_sdk.internal.test_instance_trust import (
    approved_test_instance_origins,
    require_test_instance_origin_approval,
)


def test_remote_origin_requires_external_exact_approval() -> None:
    with pytest.raises(ConfigError, match="not approved outside the repository"):
        require_test_instance_origin_approval("https://example.com")


def test_one_environment_pin_is_comma_separated_and_canonicalized() -> None:
    origins = approved_test_instance_origins(
        environ={"ODCLI_TEST_INSTANCE_ORIGIN_PINS": "https://example.com:443, https://two.test"}
    )

    assert origins == frozenset({"https://example.com:443", "https://two.test:443"})


def test_approval_requires_exact_origin_pin() -> None:
    with pytest.raises(ConfigError, match="not approved outside the repository"):
        require_test_instance_origin_approval("https://example.com:8443")


def test_approved_remote_http_origin_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", "http://example.com:8069")

    assert require_test_instance_origin_approval("http://example.com:8069") == (
        "http://example.com:8069"
    )


def test_unapproved_remote_http_origin_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", raising=False)

    with pytest.raises(ConfigError, match="not approved outside the repository"):
        require_test_instance_origin_approval("http://example.com:8069")


def test_loopback_is_operator_controlled_without_a_pin() -> None:
    assert require_test_instance_origin_approval("https://127.0.0.1:8443") == (
        "https://127.0.0.1:8443"
    )


@pytest.mark.parametrize(("url", "error"), [("ftp://example.com", InvalidBaseUrlError)])
def test_approval_rejects_unsafe_transport(url: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        require_test_instance_origin_approval(url)
