from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli


@pytest.mark.unit
def test_monitor_headless_passes_flags_and_exits_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run_server(
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        headless: bool = False,
        no_open: bool = False,
    ) -> None:
        captured.update({"host": host, "port": port, "headless": headless, "no_open": no_open})

    monkeypatch.setattr("odoo_instance_sdk.internal.serve.run_server", fake_run_server)
    result = CliRunner().invoke(cli, ["monitor", "--headless", "--no-open"])
    assert result.exit_code == 0, result.output
    assert captured == {
        "host": "127.0.0.1",
        "port": None,
        "headless": True,
        "no_open": True,
    }


@pytest.mark.unit
def test_monitor_port_and_host_pass_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run_server(
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        headless: bool = False,
        no_open: bool = False,
    ) -> None:
        captured.update({"host": host, "port": port, "headless": headless, "no_open": no_open})

    monkeypatch.setattr("odoo_instance_sdk.internal.serve.run_server", fake_run_server)
    result = CliRunner().invoke(cli, ["monitor", "--host", "127.0.0.1", "--port", "8111"])
    assert result.exit_code == 0, result.output
    assert captured["port"] == 8111
    assert captured["host"] == "127.0.0.1"
    assert captured["headless"] is False
    assert captured["no_open"] is False


@pytest.mark.unit
def test_monitor_missing_dashboard_extra_exits_one_with_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raising_run_server(
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        headless: bool = False,
        no_open: bool = False,
    ) -> None:
        raise SystemExit(
            "monitor command requires the dashboard extra: "
            "pip install odoo-instance-sdk[dashboard] (no module named 'uvicorn')"
        )

    monkeypatch.setattr("odoo_instance_sdk.internal.serve.run_server", raising_run_server)
    result = CliRunner().invoke(cli, ["monitor", "--headless"])
    # SystemExit raised inside the command propagates as exit 1.
    assert result.exit_code == 1
    assert "pip install odoo-instance-sdk[dashboard]" in result.output


@pytest.mark.unit
def test_monitor_missing_extra_hint_in_exception_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The hint must appear in the CLI output (stderr or stdout) so a human
    # running without the extra sees the actionable install command.
    def raising_run_server(
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        headless: bool = False,
        no_open: bool = False,
    ) -> None:
        raise SystemExit("pip install odoo-instance-sdk[dashboard] (missing uvicorn)")

    monkeypatch.setattr("odoo_instance_sdk.internal.serve.run_server", raising_run_server)
    result = CliRunner().invoke(cli, ["monitor"])
    assert result.exit_code == 1
    assert "[dashboard]" in result.output
