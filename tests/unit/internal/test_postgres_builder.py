from __future__ import annotations

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import ExecutionPlan
from odoo_instance_sdk.internal.pg.builder import (
    build_psql_specification,
    validate_native_psql_args,
)


@pytest.mark.parametrize(
    "tokens",
    [
        ("-a", "--echo-errors", "-e", "--echo-hidden", "-H", "--html"),
        ("-l", "-n", "-q", "-s", "-S", "-t", "-x", "-X"),
        ("-w", "-W", "-z", "-0", "-1", "--csv"),
        ("-c", "SELECT 1", "-fquery.sql", "--field-separator", "|"),
        ("-Llog", "--output=out", "-Pborder=2", "--record-separator", "::"),
        ("-T", "class=compact", "-vON_ERROR_STOP=1", "--variable=x=1"),
    ],
)
def test_native_grammar_preserves_every_allowed_token(tokens: tuple[str, ...]) -> None:
    assert validate_native_psql_args(tokens) == tokens


@pytest.mark.parametrize(
    "tokens",
    [
        ("-d", "other"),
        ("-dother",),
        ("--dbname=other",),
        ("-h", "other"),
        ("--username=other",),
        ("-p5433",),
        ("--unknown",),
        ("-c",),
        ("--command=",),
        ("database",),
        ("--", "database"),
    ],
)
def test_native_grammar_rejects_identity_unknown_missing_and_positional(
    tokens: tuple[str, ...],
) -> None:
    with pytest.raises(ConfigError, match="invalid native psql arguments"):
        validate_native_psql_args(tokens)


def test_builder_pairs_one_private_step_with_one_safe_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    monkeypatch.setenv("PGHOST", "ambient-host")
    monkeypatch.setenv("PGOPTIONS", "-c statement_timeout=999999")
    spec = build_psql_specification(
        host="bound-host",
        port=5432,
        user="bound-user",
        database="bound-db",
        password="private-password",
        args=("-c", "SELECT 1"),
        timeout=2.5,
    )

    private = spec.prepared_step
    public = spec.public_step
    assert spec.process_step is public
    assert private.argv == (
        "psql",
        "-X",
        "-h",
        "bound-host",
        "-p",
        "5432",
        "-U",
        "bound-user",
        "-d",
        "bound-db",
        "-c",
        "SELECT 1",
    )
    private_env = dict(private.environment_snapshot)
    assert private_env["PGPASSWORD"] == "private-password"
    assert private_env["PGOPTIONS"] == "-c statement_timeout=2500"
    assert "PGHOST" not in private_env
    assert "private-password" not in repr(spec)
    assert "private-password" not in repr(public)
    assert "private-password" not in ExecutionPlan(steps=(public,)).with_fingerprint().fingerprint
    assert public.environment_overrides == (("PGPASSWORD", "<redacted>"),)


def test_builder_foreground_changes_only_shared_tty_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    captured = build_psql_specification(
        host=None,
        port=5432,
        user="odoo",
        database="postgres",
        mode="captured",
    )
    foreground = build_psql_specification(
        host=None,
        port=5432,
        user="odoo",
        database="postgres",
        mode="foreground",
    )
    assert captured.prepared_step.argv == foreground.prepared_step.argv
    assert captured.prepared_step != foreground.prepared_step
    assert not captured.public_step.interactive
    assert foreground.public_step.interactive
    assert foreground.prepared_step.inherit_stdio
