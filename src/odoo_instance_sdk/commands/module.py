"""CLI registration for the Odoo module workflow."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import msgspec

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
from rich.console import Console
from rich.table import Table

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    fail,
    field_schema,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.commands.test import (
    project_execution_result,
    rich_test_result,
)
from odoo_instance_sdk.internal.automation import (
    ModuleRecord,
    module_records_from_result,
)
from odoo_instance_sdk.internal.cli_format import rich_cell
from odoo_instance_sdk.models import CommandResult, OdooTestSpec

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.test_selection import _TestSelection
    from odoo_instance_sdk.models import OdooTestResult


class _ModuleRecordResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    name: str
    state: str
    technical_name: str | None
    installed_version: str | None
    latest_version: str | None
    license: str | None
    summary: str | None


class _ModuleListResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    modules: tuple[_ModuleRecordResult, ...]


def _updated_modules(value: CommandResult | None) -> JsonValue:
    if value is None:
        return []
    from odoo_instance_sdk.internal.server import parse_payload

    payload = parse_payload(value.stdout)
    if not isinstance(payload, dict):
        return []
    nested = payload.get("result")
    return nested.get("updated", []) if isinstance(nested, dict) else []


def _module_list_result(value: CommandResult | list[ModuleRecord]) -> _ModuleListResult:
    records = value if isinstance(value, list) else module_records_from_result(value)
    return _ModuleListResult(
        modules=tuple(
            _ModuleRecordResult(
                name=record.name,
                state=record.state,
                technical_name=record.technical_name,
                installed_version=record.installed_version,
                latest_version=record.latest_version,
                license=record.license,
                summary=record.summary,
            )
            for record in records
        )
    )


def _rich_module_list(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    records = result.get("modules", [])
    if not isinstance(records, list):
        return "No modules"
    table = Table("NAME", "STATE", "VERSION")
    for record in records:
        if isinstance(record, dict):
            table.add_row(
                rich_cell(record.get("name", "")),
                rich_cell(record.get("state", "")),
                rich_cell(record.get("installed_version") or record.get("latest_version") or ""),
            )
    console = Console(record=True, color_system=None, width=180)
    console.print(table)
    return console.export_text().rstrip()


def _rich_module_update(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    modules = result.get("modules", [])
    updated = result.get("updated", [])
    values = updated if isinstance(updated, list) and updated else modules
    table = Table("Module", "Status", title="Module update")
    if isinstance(values, list) and values:
        status = "planned" if document.dry_run else "updated"
        for module in values:
            table.add_row(rich_cell(module), rich_cell(status))
    else:
        table.add_row("(none)", "no changes")
    console = Console(record=True, color_system=None, width=180)
    console.print("Updated modules:")
    console.print(table)
    return console.export_text().rstrip()


def module_group() -> None:
    """Discover, test, and upgrade Odoo modules."""


@click.group("module", help="Discover, test, and upgrade Odoo modules.")
def _module_group() -> None:
    module_group()


@_module_group.command("list", aliases=["ls"], help="List installed or available Odoo modules.")
@click.argument("modules", nargs=-1)
@click.option("--state", "state", default=None, help="Filter by state.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@field_schema(_ModuleListResult)
@pass_cli_context
def module_list(
    ctx: CliContext,
    modules: tuple[str, ...],
    state: str | None,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        runtime_context = cli_context.ready_instance(ctx)
        status, _records = run_or_preview(
            lambda: cast(
                "Callable[..., Command[object]]",
                getattr(sys.modules["odoo_instance_sdk.cli"], "list_modules_command"),
            )(runtime_context.instance, names=tuple(modules), state=state),
            command_name="module.list",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: (
                model_to_dict(
                    _module_list_result(cast("CommandResult | list[ModuleRecord]", value))
                )
                if value is not None
                else model_to_dict(_ModuleListResult(modules=()))
            ),
            rich=_rich_module_list,
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.list", e, dry_run=dry_run)
    raise click.exceptions.Exit(status)


@_module_group.command("update", help="Upgrade selected Odoo modules.")
@click.argument("modules", nargs=-1, required=True)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Plan only.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Confirm execution.")
@output_options
@pass_cli_context
def module_update(
    ctx: CliContext,
    modules: tuple[str, ...],
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        runtime_context = cli_context.ready_instance(ctx)
        runtime_context.runtime
        instance = runtime_context.instance
        selected_modules = tuple(modules)

        def build_command() -> Command[CommandResult]:
            return cast(
                "Callable[..., Command[CommandResult]]",
                getattr(sys.modules["odoo_instance_sdk.cli"], "update_modules_command"),
            )(instance, selected_modules)

        status, _outcome = run_or_preview(
            build_command,
            command_name="module.update",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: {
                "modules": list(selected_modules),
                "updated": _updated_modules(value),
            },
            confirm=(
                lambda: fail(
                    output_mode, "module.update", "module update requires --yes", dry_run=dry_run
                )
            )
            if not yes
            else None,
            preview=lambda command: {
                "modules": list(selected_modules),
                "plan": model_to_dict(command.plan),
                "dry_run": True,
            },
            rich=_rich_module_update,
            progress=True,
        )
    except SystemExit:
        raise
    except Exception as exc:
        fail(output_mode, "module.update", exc, dry_run=dry_run)
    raise click.exceptions.Exit(status)


@_module_group.command("test", help="Run tests for selected Odoo modules.")
@click.argument("modules", nargs=-1, required=True)
@click.option("--test-tags", "test_tags", required=True, help="Test tags.")
@click.option("--reload-tests", "reload_tests", is_flag=True, default=False)
@click.option("--allow-empty", "allow_empty", is_flag=True, default=False)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def module_test(
    ctx: CliContext,
    modules: tuple[str, ...],
    test_tags: str,
    reload_tests: bool,
    allow_empty: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        runtime_context = cli_context.ready_instance(ctx)
        runtime = runtime_context.runtime
        selection = cast(
            "Callable[..., tuple[_TestSelection, ...]]",
            getattr(sys.modules["odoo_instance_sdk.cli"], "resolve_module_test_selection"),
        )(runtime.root, runtime.start_config, tuple(modules), test_tags)
        spec = OdooTestSpec(
            modules=tuple(sorted(set(modules))),
            test_tags=test_tags,
            reload_tests=reload_tests,
            allow_empty=allow_empty,
        )
        status, outcome = run_or_preview(
            lambda: cast(
                "Callable[..., Command[tuple[OdooTestResult, str | None]]]",
                getattr(sys.modules["odoo_instance_sdk.cli"], "module_tests_command"),
            )(
                runtime_context.instance,
                spec,
                http_interface=runtime.http_interface,
                http_port=runtime.http_port,
            ),
            command_name="module.test",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: (
                project_execution_result(runtime, selection, spec, value[0])
                if value is not None
                else {}
            ),
            rich=lambda document: rich_test_result(cast("dict[str, JsonValue]", document.result)),
            progress=True,
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.test", e, dry_run=dry_run)
    raise click.exceptions.Exit(
        outcome[0].exit_code if outcome is not None and not dry_run else status
    )


def register_module_commands(group: click.Group) -> None:
    """Attach the module command group through the shared CLI seam."""
    group.add_command(_module_group)


__all__ = ["module_group", "register_module_commands"]
