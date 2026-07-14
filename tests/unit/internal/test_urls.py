from __future__ import annotations

import warnings

import pytest

from odoo_instance_sdk.exceptions import InvalidBaseUrlError, NonLocalInstanceError
from odoo_instance_sdk.internal import urls
from odoo_instance_sdk.internal.urls import (
    assert_local,
    is_loopback_host,
    normalize_base_url,
    warn_if_cleartext_secret,
)


class TestNormalizeBaseUrl:
    def test_http_default_port_removed(self) -> None:
        assert normalize_base_url("http://example.com:80") == "http://example.com"

    def test_https_default_port_removed(self) -> None:
        assert normalize_base_url("https://example.com:443") == "https://example.com"

    def test_non_default_port_preserved(self) -> None:
        assert normalize_base_url("http://example.com:8080") == "http://example.com:8080"

    def test_ipv6_loopback_with_explicit_port(self) -> None:
        assert normalize_base_url("http://[::1]:8069") == "http://[::1]:8069"

    def test_case_normalization(self) -> None:
        assert normalize_base_url("HTTP://EXAMPLE") == "http://example"

    def test_credentials_rejected(self) -> None:
        with pytest.raises(InvalidBaseUrlError):
            normalize_base_url("http://user:pass@example.com")

    def test_query_rejected(self) -> None:
        with pytest.raises(InvalidBaseUrlError):
            normalize_base_url("http://example.com?foo=bar")

    def test_fragment_rejected(self) -> None:
        with pytest.raises(InvalidBaseUrlError):
            normalize_base_url("http://example.com#frag")

    def test_non_root_path_rejected(self) -> None:
        with pytest.raises(InvalidBaseUrlError):
            normalize_base_url("http://example.com/path")

    def test_unsupported_scheme_rejected(self) -> None:
        with pytest.raises(InvalidBaseUrlError):
            normalize_base_url("ftp://example.com")

    def test_malformed_url_rejected(self) -> None:
        with pytest.raises(InvalidBaseUrlError):
            normalize_base_url("not a url")


class TestIsLoopbackHost:
    def test_localhost_is_local(self) -> None:
        assert is_loopback_host("localhost") is True
        assert is_loopback_host("LOCALHOST") is True

    def test_ipv4_loopback(self) -> None:
        assert is_loopback_host("127.0.0.1") is True
        assert is_loopback_host("127.0.0.0") is True
        assert is_loopback_host("127.255.255.255") is True

    def test_ipv6_loopback(self) -> None:
        assert is_loopback_host("::1") is True

    def test_private_ips_not_local(self) -> None:
        assert is_loopback_host("10.0.0.1") is False
        assert is_loopback_host("192.168.0.1") is False
        assert is_loopback_host("172.16.0.1") is False

    def test_dns_name_not_local(self) -> None:
        assert is_loopback_host("example.com") is False


class TestAssertLocal:
    def test_localhost_allowed(self) -> None:
        assert_local("http://localhost:8069")
        assert_local("http://LOCALHOST:8069")

    def test_ipv4_loopback_allowed(self) -> None:
        assert_local("http://127.0.0.1:8069")
        assert_local("http://127.1.2.3:8069")
        assert_local("http://127.255.255.255:8069")

    def test_ipv6_loopback_allowed(self) -> None:
        assert_local("http://[::1]:8069")

    def test_remote_refused(self) -> None:
        for url in ["http://example.com:8069", "http://192.168.0.1:8069", "http://10.0.0.1:8069"]:
            with pytest.raises(NonLocalInstanceError):
                assert_local(url)


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


if __name__ == "__main__":
    pytest.main([__file__])
