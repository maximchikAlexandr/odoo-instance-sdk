"""Minimal self-check for readiness polling."""

from odoo_instance_sdk._health import poll_health
from odoo_instance_sdk.exceptions import ReadinessTimeoutError
from odoo_instance_sdk.models import OdooClientConfig
from tests._helpers import SilentHandler, start_stub_server


class HealthPassHandler(SilentHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "pass"}')


class HealthSlowHandler(SilentHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "starting"}')


def test_poll_health_success() -> None:
    server, port = start_stub_server(HealthPassHandler)

    config = OdooClientConfig(
        executable="python3",
        base_url=f"http://127.0.0.1:{port}",
        master_pwd="test",
    )

    result = poll_health(config, timeout=5.0)
    assert result.ok is True
    assert result.attempts >= 1
    assert result.final_status == "pass"
    assert result.elapsed >= 0.0

    server.shutdown()
    print("test_poll_health_success PASSED")


def test_poll_health_timeout() -> None:
    server, port = start_stub_server(HealthSlowHandler)

    config = OdooClientConfig(
        executable="python3",
        base_url=f"http://127.0.0.1:{port}",
        master_pwd="test",
    )

    try:
        poll_health(config, timeout=1.0, poll_interval=0.1)
        assert False, "Should have raised ReadinessTimeoutError"
    except ReadinessTimeoutError as e:
        assert e.timeout == 1.0

    server.shutdown()
    print("test_poll_health_timeout PASSED")


if __name__ == "__main__":
    test_poll_health_success()
    test_poll_health_timeout()
