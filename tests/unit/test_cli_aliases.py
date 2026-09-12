from __future__ import annotations

import json
from dataclasses import dataclass

import click
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES


@dataclass(frozen=True)
class AliasCase:
    group: str
    canonical: str
    compatible: str
    public_path: tuple[str, ...]


ALIASES = (
    AliasCase("env", "create", "checkout", ("env", "create")),
    AliasCase("env", "ls", "list", ("env", "ls")),
    AliasCase("env", "rm", "remove", ("env", "rm")),
    AliasCase("backup", "ls", "list", ("backup", "ls")),
    AliasCase("backup", "inspect", "show", ("backup", "inspect")),
    AliasCase("backup", "rm", "delete", ("backup", "rm")),
    AliasCase("db", "ls", "list", ("db", "ls")),
    AliasCase("db", "rm", "drop", ("db", "rm")),
    AliasCase("postgres", "ps", "status", ("postgres", "ps")),
    AliasCase("resource", "ls", "list", ("resource", "ls")),
    AliasCase("module", "ls", "list", ("module", "ls")),
)


def _group(name: str) -> click.Group:
    command = cli.commands[name]
    assert isinstance(command, click.Group)
    return command


def _leaf_args(case: AliasCase, spelling: str) -> list[str]:
    public_case = next(item for item in PUBLIC_LEAF_CASES if item.path == case.public_path)
    args = list(public_case.args)
    args[args.index(case.canonical)] = spelling
    return args


def _help_without_invocation(output: str) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in output.splitlines()
        if line.strip() and not line.lstrip().startswith("Usage:")
    )


def _decode_machine_output(output: str, output_format: str) -> object:
    if output_format == "json":
        return json.loads(output)
    from toon import DecodeOptions, decode

    return decode(output, DecodeOptions(indent=2, strict=True))


def _without_dynamic_timestamps(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "<dynamic>"
            if key in {"generated_at", "sampled_at"}
            else _without_dynamic_timestamps(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_without_dynamic_timestamps(item) for item in value]
    return value


@pytest.mark.unit
@pytest.mark.parametrize("case", ALIASES, ids=lambda case: f"{case.group}-{case.canonical}")
def test_aliases_share_click_object_callback_and_public_contract(case: AliasCase) -> None:
    group = _group(case.group)
    context = click.Context(group)
    canonical = group.get_command(context, case.canonical)
    compatible = group.get_command(context, case.compatible)
    assert canonical is not None
    assert compatible is canonical
    assert compatible.callback is canonical.callback

    runner = CliRunner()
    compatible_help = runner.invoke(cli, [case.group, case.compatible, "--help"])
    canonical_help = runner.invoke(cli, [case.group, case.canonical, "--help"])
    assert compatible_help.exit_code == canonical_help.exit_code == 0
    assert _help_without_invocation(compatible_help.output) == _help_without_invocation(
        canonical_help.output
    )


@pytest.mark.unit
def test_alias_help_is_deduplicated_and_lists_alternate_spellings() -> None:
    runner = CliRunner()
    for group_name in {case.group for case in ALIASES}:
        result = runner.invoke(cli, [group_name, "--help"])
        assert result.exit_code == 0, result.output
        group_cases = [case for case in ALIASES if case.group == group_name]
        for case in group_cases:
            command_rows = [
                line
                for line in result.output.splitlines()
                if "│" in line and case.compatible in line and case.canonical in line
            ]
            assert len(command_rows) == 1


@pytest.mark.unit
@pytest.mark.parametrize("case", ALIASES, ids=lambda case: f"{case.group}-{case.canonical}")
@pytest.mark.parametrize("output_format", ["rich", "json", "toon"])
def test_aliases_preserve_output_and_machine_command_id(
    case: AliasCase, output_format: str
) -> None:
    runner = CliRunner()
    compatible_args = _leaf_args(case, case.compatible)
    canonical_args = _leaf_args(case, case.canonical)
    compatible_args.extend(["--format", output_format])
    canonical_args.extend(["--format", output_format])
    compatible = runner.invoke(cli, compatible_args)
    canonical = runner.invoke(cli, canonical_args)
    assert compatible.exit_code == canonical.exit_code
    assert compatible.stderr == canonical.stderr
    if output_format in {"json", "toon"}:
        compatible_document = _decode_machine_output(compatible.stdout, output_format)
        canonical_document = _decode_machine_output(canonical.stdout, output_format)
        assert _without_dynamic_timestamps(compatible_document) == _without_dynamic_timestamps(
            canonical_document
        )
    else:
        assert compatible.stdout == canonical.stdout


@pytest.mark.unit
@pytest.mark.parametrize(
    "args",
    [
        ("env", "rm", "env-1"),
        ("backup", "rm", "00000000-0000-0000-0000-000000000007"),
        ("db", "rm", "demo"),
    ],
)
def test_destructive_aliases_keep_machine_confirmation_gate(args: tuple[str, ...]) -> None:
    runner = CliRunner()
    alias_result = runner.invoke(cli, [*args, "--format", "json"])
    compatible = next(
        case.compatible for case in ALIASES if case.group == args[0] and case.canonical == args[1]
    )
    compatible_result = runner.invoke(cli, [args[0], compatible, *args[2:], "--format", "json"])
    assert alias_result.exit_code == compatible_result.exit_code == 1
    assert (
        json.loads(alias_result.stdout)["command"]
        == json.loads(compatible_result.stdout)["command"]
    )
    if args[0] != "env":
        assert json.loads(alias_result.stdout)["error"]["code"] == "confirmation_required"
