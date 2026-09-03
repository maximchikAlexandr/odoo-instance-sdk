from __future__ import annotations

import pytest

from odoo_instance_sdk.internal.pg import size as size_module
from odoo_instance_sdk.internal.pg import transport as transport_module
from odoo_instance_sdk.internal.proc import ProcessResult


def _patch_size(
    monkeypatch: pytest.MonkeyPatch,
    *,
    which: str | None = "/psql",
    outcome: ProcessResult | BaseException | None = None,
) -> dict[str, object]:
    captured: dict[str, object] = {}
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: which)

    def execute(specification: object) -> ProcessResult:
        step = specification.prepared_step  # type: ignore[attr-defined]
        captured["step"] = step
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome or ProcessResult(
            argv=step.argv,
            returncode=0,
            stdout="123\n",
            stderr="",
            duration=0.0,
            cwd=step.cwd,
            environment=step.environment,
        )

    monkeypatch.setattr(transport_module, "execute_psql", execute)
    return captured


def test_database_size_uses_safe_argv_environment_and_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_size(monkeypatch)
    assert (
        size_module.database_size_bytes(
            host="127.0.0.1", port=5432, user="odoo", password="secret", database_name="db'o"
        )
        == 123
    )
    step = captured["step"]
    assert step.argv[:8] == ("/psql", "-X", "-h", "127.0.0.1", "-p", "5432", "-U", "odoo")  # type: ignore[attr-defined]
    assert "db''o" in step.argv[-1]  # type: ignore[attr-defined]
    assert dict(step.environment_snapshot)["PGPASSWORD"] == "secret"  # type: ignore[attr-defined]
    assert step.timeout == 10.0  # type: ignore[attr-defined]


def test_database_size_defaults_to_tcp_loopback_and_accepts_custom_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_size(monkeypatch)
    assert (
        size_module.database_size_bytes(
            host=None, port=5433, user="u", password=None, database_name="db", timeout=1.5
        )
        == 123
    )
    step = captured["step"]
    assert step.argv[:4] == ("/psql", "-X", "-h", "127.0.0.1")  # type: ignore[attr-defined]
    assert step.timeout == 1.5  # type: ignore[attr-defined]
    assert "PGPASSWORD" not in dict(step.environment_snapshot)  # type: ignore[attr-defined]


def test_database_size_never_inherits_ambient_password(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_size(monkeypatch)
    monkeypatch.setenv("PGPASSWORD", "ambient-secret")
    assert (
        size_module.database_size_bytes(
            host=None, port=5432, user="u", password=None, database_name="db"
        )
        == 123
    )
    step = captured["step"]
    assert "PGPASSWORD" not in dict(step.environment_snapshot)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "which, outcome",
    [
        (None, None),
        ("/psql", OSError("nope")),
        ("/psql", RuntimeError("timeout")),
        (
            "/psql",
            ProcessResult(
                argv=(),
                returncode=1,
                stdout="",
                stderr="bad",
                duration=0.0,
                cwd=None,
                environment=(),
            ),
        ),
        (
            "/psql",
            ProcessResult(
                argv=(),
                returncode=0,
                stdout="not-an-int",
                stderr="",
                duration=0.0,
                cwd=None,
                environment=(),
            ),
        ),
    ],
)
def test_database_size_failure_modes(
    monkeypatch: pytest.MonkeyPatch, which: str | None, outcome: object
) -> None:
    _patch_size(
        monkeypatch,
        which=which,
        outcome=outcome if isinstance(outcome, (BaseException, ProcessResult)) else None,
    )
    assert (
        size_module.database_size_bytes(
            host=None, port=5432, user="u", password=None, database_name="db"
        )
        is None
    )


def test_database_size_rejects_missing_user_and_backslash() -> None:
    assert (
        size_module.database_size_bytes(
            host=None, port=5432, user=None, password=None, database_name="db"
        )
        is None
    )
    assert (
        size_module.database_size_bytes(
            host=None, port=5432, user="u", password=None, database_name=r"db\\name"
        )
        is None
    )
