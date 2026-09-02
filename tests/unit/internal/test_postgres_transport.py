from __future__ import annotations

import pytest

from odoo_instance_sdk.internal.pg import transport as transport_module
from odoo_instance_sdk.internal.proc import ProcessResult


def _capture_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    which: str | None = "/usr/bin/psql",
    outcome: ProcessResult | BaseException | None = None,
) -> dict[str, object]:
    captured: dict[str, object] = {}
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: which)

    def execute(specification: object) -> ProcessResult:
        step = specification.prepared_step  # type: ignore[attr-defined]
        captured["step"] = step
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is not None:
            return outcome
        return ProcessResult(
            argv=step.argv,
            returncode=0,
            stdout="1\n",
            stderr="",
            duration=0.0,
            cwd=step.cwd,
            environment=step.environment,
        )

    monkeypatch.setattr(transport_module, "execute_psql", execute)
    return captured


def test_run_psql_scrubs_ambient_startup_and_transport_inputs_but_keeps_pgpassfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_transport(monkeypatch)
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
        transport_module.run_psql(
            host="127.0.0.1", port=5432, user="odoo", password=None, query="SELECT 1", timeout=1
        )
        is not None
    )
    step = captured["step"]
    assert step.argv[0] == "/usr/bin/psql"  # type: ignore[attr-defined]
    env = dict(step.environment_snapshot)  # type: ignore[attr-defined]
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
    captured = _capture_transport(monkeypatch)
    monkeypatch.setenv("PGPASSWORD", "ambient")
    transport_module.run_psql(
        host=None, port=5432, user="odoo", password="configured", query="SELECT 1", timeout=1
    )
    step = captured["step"]
    assert dict(step.environment_snapshot)["PGPASSWORD"] == "configured"  # type: ignore[attr-defined]


def test_run_psql_none_host_uses_unix_socket_without_h(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_transport(monkeypatch)
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.2")
    transport_module.run_psql(
        host=None, port=5432, user="odoo", password=None, query="SELECT 1", timeout=1
    )
    step = captured["step"]
    assert "-h" not in step.argv  # type: ignore[attr-defined]
    assert step.argv[:4] == ("/usr/bin/psql", "-X", "-p", "5432")  # type: ignore[attr-defined]


def test_run_psql_explicit_host_uses_tcp_h(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_transport(monkeypatch)
    monkeypatch.setenv("PGHOST", "/wrong/socket")
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.2")
    transport_module.run_psql(
        host="127.0.0.1", port=5432, user="odoo", password=None, query="SELECT 1", timeout=1
    )
    step = captured["step"]
    assert step.argv[:6] == (  # type: ignore[attr-defined]
        "/usr/bin/psql",
        "-X",
        "-h",
        "127.0.0.1",
        "-p",
        "5432",
    )


def test_run_psql_targets_requested_database_without_changing_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_transport(monkeypatch)
    transport_module.run_psql(
        host=None,
        port=5432,
        user="odoo",
        password=None,
        query="SELECT name FROM ir_module_module",
        timeout=3,
        database="bound_db",
    )
    step = captured["step"]
    assert step.argv[step.argv.index("-d") + 1] == "bound_db"  # type: ignore[attr-defined]
    assert step.timeout == 3  # type: ignore[attr-defined]


@pytest.mark.parametrize("outcome", [OSError("nope"), RuntimeError("timeout")])
def test_run_psql_returns_none_for_missing_tool_or_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    outcome: BaseException,
) -> None:
    if isinstance(outcome, OSError):
        captured = _capture_transport(monkeypatch, which=None)
    else:
        captured = _capture_transport(monkeypatch, outcome=outcome)
    result = transport_module.run_psql(
        host=None, port=5432, user="odoo", password=None, query="SELECT 1", timeout=1
    )
    assert result is None
    if isinstance(outcome, RuntimeError):
        assert "step" in captured
