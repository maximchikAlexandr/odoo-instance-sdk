from __future__ import annotations

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import ExecutionPlan
from odoo_instance_sdk.internal.pg import builder as builder_module
from odoo_instance_sdk.internal.pg.builder import (
    build_psql_specification,
    validate_native_psql_args,
)


def _value_forms(options: set[str]) -> list[object]:
    cases: list[object] = []
    for option in sorted(options):
        if option.startswith("--"):
            cases.extend(
                [
                    pytest.param((option, "value"), id=f"{option}-split"),
                    pytest.param((f"{option}=value",), id=f"{option}-equals"),
                ]
            )
        else:
            cases.extend(
                [
                    pytest.param((option, "value"), id=f"{option}-split"),
                    pytest.param((f"{option}value",), id=f"{option}-attached"),
                ]
            )
    return cases


def _protected_forms(options: set[str]) -> list[object]:
    cases: list[object] = []
    for option in sorted(options):
        if option.startswith("--"):
            cases.extend(
                [
                    pytest.param((option, "other"), id=f"{option}-split"),
                    pytest.param((f"{option}=other",), id=f"{option}-equals"),
                ]
            )
        else:
            cases.extend(
                [
                    pytest.param((option, "other"), id=f"{option}-split"),
                    pytest.param((f"{option}other",), id=f"{option}-attached"),
                ]
            )
    return cases


@pytest.mark.parametrize("option", sorted(builder_module._ZERO_OPTIONS))
def test_native_grammar_accepts_every_declared_zero_value_option(option: str) -> None:
    assert validate_native_psql_args((option,)) == (option,)


@pytest.mark.parametrize("tokens", _value_forms(builder_module._VALUE_OPTIONS))
def test_native_grammar_accepts_every_value_option_form(tokens: tuple[str, ...]) -> None:
    assert validate_native_psql_args(tokens) == tokens


@pytest.mark.parametrize("tokens", _protected_forms(builder_module._PROTECTED))
def test_native_grammar_rejects_every_protected_identity_form(tokens: tuple[str, ...]) -> None:
    with pytest.raises(ConfigError, match="connection identity option"):
        validate_native_psql_args(tokens)


@pytest.mark.parametrize("option", sorted(builder_module._VALUE_OPTIONS))
def test_native_grammar_rejects_missing_value_for_every_value_option(option: str) -> None:
    with pytest.raises(ConfigError, match="missing value"):
        validate_native_psql_args((option,))


@pytest.mark.parametrize(
    "option", sorted(option for option in builder_module._VALUE_OPTIONS if option.startswith("--"))
)
def test_native_grammar_rejects_empty_equals_for_every_long_value_option(option: str) -> None:
    with pytest.raises(ConfigError, match="missing value"):
        validate_native_psql_args((f"{option}=",))


@pytest.mark.parametrize(
    "operand",
    sorted(
        builder_module._PROTECTED | builder_module._ZERO_OPTIONS | builder_module._VALUE_OPTIONS
    ),
)
def test_native_grammar_rejects_every_declared_operand_after_double_dash(operand: str) -> None:
    with pytest.raises(ConfigError, match="after '--'"):
        validate_native_psql_args(("--", operand))


@pytest.mark.parametrize("operand", ("other_db", "postgresql://other/db", "host=other"))
def test_native_grammar_rejects_connection_positional_operands(operand: str) -> None:
    with pytest.raises(ConfigError, match="positional operand"):
        validate_native_psql_args((operand,))


@pytest.mark.parametrize(
    "tokens",
    [pytest.param((option,), id=option) for option in sorted(builder_module._ZERO_OPTIONS)]
    + _value_forms(builder_module._VALUE_OPTIONS),
)
def test_builder_preserves_exact_native_token_boundaries_before_spawn(
    tokens: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    spec = build_psql_specification(
        host="bound-host",
        port=5432,
        user="bound-user",
        database="bound-db",
        args=tokens,
    )
    assert spec.prepared_step.argv[-len(tokens) :] == tokens
    assert spec.public_step.argv[-len(tokens) :] == tokens


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
