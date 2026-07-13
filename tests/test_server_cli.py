"""Minimal self-check for server.run()."""

import sys

from odoo_instance_sdk.exceptions import CommandTimeoutError
from tests._helpers import make_client


def test_run_version_command() -> None:
    client = make_client()
    result = client.server.run(["-c", "print('hello from sdk')"], timeout=5.0)
    assert result.returncode == 0, f"returncode={result.returncode}"
    assert "hello from sdk" in result.stdout, f"stdout={result.stdout!r}"
    assert result.duration >= 0.0
    assert result.args[0] == sys.executable
    print("test_run_version_command PASSED")


def test_run_timeout() -> None:
    client = make_client()
    try:
        client.server.run(["-c", "import time; time.sleep(10)"], timeout=0.5)
        assert False, "Should have raised"
    except CommandTimeoutError:
        print("test_run_timeout PASSED")


if __name__ == "__main__":
    test_run_version_command()
    test_run_timeout()
