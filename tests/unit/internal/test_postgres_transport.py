from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

from odoo_instance_sdk.internal.postgres_transport import run_psql

if TYPE_CHECKING:
    import pytest


def test_run_psql_scrubs_ambient_startup_and_transport_inputs_but_keeps_pgpassfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args, 0, "1\n", "")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_transport.shutil.which", lambda _: "/usr/bin/psql"
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.postgres_transport.subprocess.run", run)
    for key in (
        "PSQLRC",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGOPTIONS",
        "PGPASSWORD",
        "PGHOST",
        "PGHOSTADDR",
    ):
        monkeypatch.setenv(key, "ambient")
    monkeypatch.setenv("PGPASSFILE", "/tmp/passwords")

    assert (
        run_psql(
            host="127.0.0.1", port=5432, user="odoo", password=None, query="SELECT 1", timeout=1
        )
        is not None
    )
    assert "-X" in captured["args"]
    env = captured["env"]
    assert isinstance(env, dict)
    for key in (
        "PSQLRC",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGOPTIONS",
        "PGPASSWORD",
        "PGHOST",
        "PGHOSTADDR",
    ):
        assert key not in env
    assert env["PGPASSFILE"] == "/tmp/passwords"


def test_run_psql_explicit_password_overrides_ambient_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args, 0, "1\n", "")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_transport.shutil.which", lambda _: "/usr/bin/psql"
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.postgres_transport.subprocess.run", run)
    monkeypatch.setenv("PGPASSWORD", "ambient")
    run_psql(host=None, port=5432, user="odoo", password="configured", query="SELECT 1", timeout=1)
    assert captured["env"]["PGPASSWORD"] == "configured"


def test_run_psql_none_host_uses_unix_socket_without_h(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args, 0, "1\n", "")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_transport.shutil.which", lambda _: "/usr/bin/psql"
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.postgres_transport.subprocess.run", run)
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.2")
    run_psql(host=None, port=5432, user="odoo", password=None, query="SELECT 1", timeout=1)
    assert "-h" not in captured["args"]
    assert captured["args"][:4] == ["psql", "-X", "-p", "5432"]
    assert "PGHOST" not in captured["env"]
    assert "PGHOSTADDR" not in captured["env"]


def test_run_psql_explicit_host_uses_tcp_h(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args, 0, "1\n", "")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_transport.shutil.which", lambda _: "/usr/bin/psql"
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.postgres_transport.subprocess.run", run)
    monkeypatch.setenv("PGHOST", "/wrong/socket")
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.2")
    run_psql(host="127.0.0.1", port=5432, user="odoo", password=None, query="SELECT 1", timeout=1)
    assert captured["args"][:6] == ["psql", "-X", "-h", "127.0.0.1", "-p", "5432"]
    assert "PGHOST" not in captured["env"]
    assert "PGHOSTADDR" not in captured["env"]
