from __future__ import annotations

import subprocess
from typing import Any

import pytest

from odoo_instance_sdk.internal.postgres_size import database_size_bytes


def _run_result(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 0, "123\n", "")


def test_database_size_uses_safe_argv_environment_and_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured.update(kwargs)
        return _run_result(args)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_transport.shutil.which", lambda _: "/psql"
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.postgres_transport.subprocess.run", run)
    assert (
        database_size_bytes(
            host="127.0.0.1", port=5432, user="odoo", password="secret", database_name="db'o"
        )
        == 123
    )
    assert captured["args"][:7] == ["psql", "-h", "127.0.0.1", "-p", "5432", "-U", "odoo"]
    assert "db''o" in captured["args"][-1]
    assert captured["env"]["PGPASSWORD"] == "secret"
    assert captured["shell"] is False
    assert captured["timeout"] == 10.0


def test_database_size_defaults_to_tcp_loopback_and_accepts_custom_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["args"] = args
        seen.update(kwargs)
        return _run_result(args)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_transport.shutil.which", lambda _: "/psql"
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.postgres_transport.subprocess.run", run)
    assert (
        database_size_bytes(
            host=None, port=5433, user="u", password=None, database_name="db", timeout=1.5
        )
        == 123
    )
    assert seen["args"][:3] == ["psql", "-h", "127.0.0.1"]
    assert seen["timeout"] == 1.5
    assert "PGPASSWORD" not in seen["env"]


def test_database_size_never_inherits_ambient_password(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return _run_result(args)

    monkeypatch.setenv("PGPASSWORD", "ambient-secret")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_transport.shutil.which", lambda _: "/psql"
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.postgres_transport.subprocess.run", run)
    assert (
        database_size_bytes(host=None, port=5432, user="u", password=None, database_name="db")
        == 123
    )
    assert "PGPASSWORD" not in seen["env"]


@pytest.mark.parametrize(
    "which, outcome",
    [
        (None, None),
        ("/psql", OSError("nope")),
        ("/psql", subprocess.TimeoutExpired(["psql"], 1)),
        ("/psql", subprocess.CompletedProcess(["psql"], 1, "", "bad")),
        ("/psql", subprocess.CompletedProcess(["psql"], 0, "not-an-int", "")),
    ],
)
def test_database_size_failure_modes(
    monkeypatch: pytest.MonkeyPatch, which: str | None, outcome: object
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_transport.shutil.which", lambda _: which
    )
    if outcome is not None:

        def run(*_args: Any, **_kwargs: Any) -> Any:
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr("odoo_instance_sdk.internal.postgres_transport.subprocess.run", run)
    assert (
        database_size_bytes(host=None, port=5432, user="u", password=None, database_name="db")
        is None
    )


def test_database_size_rejects_missing_user_and_backslash() -> None:
    assert (
        database_size_bytes(host=None, port=5432, user=None, password=None, database_name="db")
        is None
    )
    assert (
        database_size_bytes(
            host=None, port=5432, user="u", password=None, database_name=r"db\\name"
        )
        is None
    )
