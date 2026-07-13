"""Self-check tests for local-only guard."""

from odoo_instance_sdk._local_guard import assert_local
from odoo_instance_sdk.exceptions import RemoteInstanceError


def test_localhost_allowed():
    assert_local("http://localhost:8069")
    assert_local("http://LOCALHOST:8069")
    print("test_localhost_allowed PASSED")


def test_ipv4_loopback_allowed():
    assert_local("http://127.0.0.1:8069")
    assert_local("http://127.1.2.3:8069")
    assert_local("http://127.255.255.255:8069")
    print("test_ipv4_loopback_allowed PASSED")


def test_ipv6_loopback_allowed():
    assert_local("http://[::1]:8069")
    print("test_ipv6_loopback_allowed PASSED")


def test_remote_refused():
    for url in ["http://example.com:8069", "http://192.168.0.1:8069", "http://10.0.0.1:8069"]:
        try:
            assert_local(url)
            assert False, f"Should have raised for {url}"
        except RemoteInstanceError:
            pass
    print("test_remote_refused PASSED")


if __name__ == "__main__":
    test_localhost_allowed()
    test_ipv4_loopback_allowed()
    test_ipv6_loopback_allowed()
    test_remote_refused()
