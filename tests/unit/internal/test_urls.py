from __future__ import annotations

import warnings

import pytest

from odoo_instance_sdk.exceptions import InvalidBaseUrlError, NonLocalInstanceError
from odoo_instance_sdk.internal import urls
from odoo_instance_sdk.internal.urls import (
    assert_local,
    canonical_origin,
    is_loopback_host,
    normalize_base_url,
    warn_if_cleartext_secret,
)
from tests.cases.normalization import (
    LOCAL_URL_CASES,
    LOOPBACK_HOST_CASES,
    NORMALIZE_URL_CASES,
    REJECT_URL_CASES,
    REMOTE_URL_CASES,
)


@pytest.mark.parametrize("raw, expected", NORMALIZE_URL_CASES)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize("raw", REJECT_URL_CASES)
def test_normalize_base_url_rejects(raw: str) -> None:
    with pytest.raises(InvalidBaseUrlError):
        normalize_base_url(raw)


def test_normalize_base_url_rejects_missing_hostname() -> None:
    with pytest.raises(InvalidBaseUrlError, match="hostname"):
        normalize_base_url("http://")


def test_canonical_origin_keeps_nondefault_port() -> None:
    assert canonical_origin("https://Example.com:8443") == "https://example.com:8443"


def test_canonical_origin_normalizes_through_shared_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def normalize(raw: str) -> str:
        calls.append(raw)
        return "https://example.com"

    monkeypatch.setattr(urls, "normalize_base_url", normalize)

    assert canonical_origin("HTTPS://Example.com/") == "https://example.com:443"
    assert calls == ["HTTPS://Example.com/"]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("ftp://example.com", "Unsupported scheme"),
        ("http://user@example.com", "Credentials in URL are not allowed"),
        ("http://example.com/?query=1", "Query parameters in URL are not allowed"),
        ("http://example.com/#fragment", "Fragment in URL is not allowed"),
    ],
)
def test_normalize_base_url_rejection_messages_are_stable(raw: str, message: str) -> None:
    with pytest.raises(InvalidBaseUrlError, match=message):
        normalize_base_url(raw)


@pytest.mark.parametrize("host, expected", LOOPBACK_HOST_CASES)
def test_is_loopback_host(host: str, expected: bool) -> None:
    assert is_loopback_host(host) is expected


@pytest.mark.parametrize("url", LOCAL_URL_CASES)
def test_assert_local_allows(url: str) -> None:
    assert_local(url)


@pytest.mark.parametrize("url", REMOTE_URL_CASES)
def test_assert_local_refuses(url: str) -> None:
    with pytest.raises(NonLocalInstanceError):
        assert_local(url)


def test_assert_local_rejects_missing_hostname() -> None:
    with pytest.raises(NonLocalInstanceError, match="hostname"):
        assert_local("http://")


class TestWarnIfCleartextSecret:
    def setup_method(self) -> None:
        urls._cleartext_warned = [False]

    def test_https_no_warning(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            warn_if_cleartext_secret("https://example.com")

    def test_localhost_no_warning(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            warn_if_cleartext_secret("http://localhost:8069")

    def test_loopback_no_warning(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            warn_if_cleartext_secret("http://127.0.0.1:8069")

    def test_remote_http_warns(self) -> None:
        with pytest.warns(UserWarning, match="cleartext"):
            warn_if_cleartext_secret("http://example.com")
        assert urls._cleartext_warned == [True]

    def test_per_process_dedup(self) -> None:
        with pytest.warns(UserWarning, match="cleartext"):
            warn_if_cleartext_secret("http://example.com")
        assert urls._cleartext_warned == [True]
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            warn_if_cleartext_secret("http://example.com")
