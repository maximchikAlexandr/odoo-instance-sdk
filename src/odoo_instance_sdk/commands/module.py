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
    emit,
    fail,
    field_schema,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
    success_document,
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
from odoo_instance_sdk.internal.test_selection import _ChangedSelectionError
from odoo_instance_sdk.models import (
    CommandResult,
    ModuleUpdatePlan,
    OdooTestSpec,
)

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


def _rich_module_info(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    value = document.result.get("module", {}) if isinstance(document.result, dict) else {}
    table = Table("Field", "Value", title="Odoo module")
    if isinstance(value, dict):
        table.add_row("Name", rich_cell(value.get("name", "")))
        table.add_row("Path", rich_cell(value.get("path", "")))
        dependencies = value.get("depends", [])
        dependencies_text = (
            ", ".join(str(item) for item in dependencies) if isinstance(dependencies, list) else ""
        )
        table.add_row("Dependencies", rich_cell(dependencies_text))
        shadowed = value.get("shadowed_paths")
        if isinstance(shadowed, list) and shadowed:
            table.add_row("Shadowed", rich_cell(", ".join(str(item) for item in shadowed)))
    console = Console(record=True, color_system=None, width=180)
    console.print(table)
    return console.export_text().rstrip()


def _rich_module_order(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    modules = document.result.get("modules", []) if isinstance(document.result, dict) else []
    return "Install order: " + (
        ", ".join(str(item) for item in modules) if isinstance(modules, list) else ""
    )


def _rich_module_where(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    table = Table("Field", "Value", title="Odoo module location")
    for field in ("name", "path", "manifest_path"):
        table.add_row(field, rich_cell(result.get(field, "")))
    console = Console(record=True, color_system=None, width=180)
    console.print(table)
    return console.export_text().rstrip()


def _rich_module_deps(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    value = document.result.get("module", {}) if isinstance(document.result, dict) else {}
    module = value.get("module", {}) if isinstance(value, dict) else {}
    dependencies = value.get("dependencies", []) if isinstance(value, dict) else []
    module_name = module.get("name", "") if isinstance(module, dict) else ""
    table = Table("Dependency", "Status", "Path", title=f"Dependencies of {module_name}")
    if isinstance(dependencies, list) and dependencies:
        for dependency in dependencies:
            if isinstance(dependency, dict):
                table.add_row(
                    rich_cell(dependency.get("name", "")),
                    "missing" if dependency.get("missing") else "available",
                    rich_cell(dependency.get("path") or ""),
                )
    else:
        table.add_row("(none)", "none", "")
    console = Console(record=True, color_system=None, width=180)
    console.print(table)
    return console.export_text().rstrip()


def _module_update_payload(
    plan: ModuleUpdatePlan,
    value: CommandResult | None,
    *,
    dry_run: bool,
) -> dict[str, JsonValue]:
    from odoo_instance_sdk.internal.server import parse_payload

    updated: list[str] = []
    if value is not None:
        payload = parse_payload(value.stdout)
        raw = payload.get("result") if isinstance(payload, dict) else None
        candidates = raw.get("updated") if isinstance(raw, dict) else None
        if isinstance(candidates, list):
            updated = [str(item) for item in candidates if isinstance(item, str)]
    result: dict[str, JsonValue] = {
        "modules": cast("JsonValue", list(plan.modules)),
        "updated": cast("JsonValue", updated),
        "not_installed": cast("JsonValue", list(plan.not_installed)),
        "changed_files": cast("JsonValue", list(plan.changed_files)),
        "ignored_paths": cast("JsonValue", list(plan.ignored_paths)),
        "unmapped_paths": cast("JsonValue", list(plan.unmapped_paths)),
    }
    for field in (
        "base_source",
        "requested_base",
        "resolved_base",
        "merge_base",
        "head",
    ):
        result[field] = cast("JsonValue", getattr(plan, field))
    if dry_run:
        result["dry_run"] = True
    return result


def _checked_module_update_payload(
    plan: ModuleUpdatePlan, value: CommandResult | None
) -> dict[str, JsonValue]:
    if value is not None and value.returncode != 0:
        from odoo_instance_sdk.resources.module import (
            _module_update_failure,
            classify_module_update_error,
        )

        conflict = classify_module_update_error(value)
        if conflict is not None:
            raise conflict
        raise _module_update_failure(value)
    return _module_update_payload(plan, value, dry_run=False)


def module_group() -> None:
    """Discover, test, and upgrade Odoo modules."""


@click.group("module", help="Discover, test, and upgrade Odoo modules.")
def _module_group() -> None:
    module_group()


@_module_group.command("info", help="Show one safely discovered Odoo module.")
@click.argument("module", required=False)
@output_options
@pass_cli_context
def module_info(
    ctx: CliContext,
    module: str | None,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        instance = cli_context.ready_instance(ctx).instance
        value = instance.modules.info(module)
        emit(
            success_document(command="module.info", result={"module": model_to_dict(value)}),
            output_mode,
            rich=_rich_module_info,
        )
    except SystemExit:
        raise
    except Exception as exc:
        fail(output_mode, "module.info", exc, dry_run=False)
    raise click.exceptions.Exit(0)


@_module_group.command("where", help="Show the resolved filesystem path for an Odoo module.")
@click.argument("module", required=False)
@output_options
@pass_cli_context
def module_where(
    ctx: CliContext,
    module: str | None,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        instance = cli_context.ready_instance(ctx).instance
        value = instance.modules.info(module)
        payload: dict[str, JsonValue] = {
            "name": value.name,
            "path": str(value.path),
            "manifest_path": str(value.manifest_path),
        }
        emit(
            success_document(command="module.where", result=payload),
            output_mode,
            rich=_rich_module_where,
        )
    except SystemExit:
        raise
    except Exception as exc:
        fail(output_mode, "module.where", exc, dry_run=False)
    raise click.exceptions.Exit(0)


@_module_group.command("deps", help="Show direct Odoo module dependencies.")
@click.argument("module", required=False)
@output_options
@pass_cli_context
def module_deps(
    ctx: CliContext,
    module: str | None,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        instance = cli_context.ready_instance(ctx).instance
        value = instance.modules.deps(module)
        emit(
            success_document(command="module.deps", result={"module": model_to_dict(value)}),
            output_mode,
            rich=_rich_module_deps,
        )
    except SystemExit:
        raise
    except Exception as exc:
        fail(output_mode, "module.deps", exc, dry_run=False)
    raise click.exceptions.Exit(0)


@_module_group.command("install-order", help="Plan a stable dependency install order.")
@click.argument("modules", nargs=-1, required=True)
@output_options
@pass_cli_context
def module_install_order(
    ctx: CliContext,
    modules: tuple[str, ...],
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        instance = cli_context.ready_instance(ctx).instance
        value = instance.modules.install_order(modules)
        emit(
            success_document(command="module.install-order", result=model_to_dict(value)),
            output_mode,
            rich=_rich_module_order,
        )
    except SystemExit:
        raise
    except Exception as exc:
        fail(output_mode, "module.install-order", exc, dry_run=False)
    raise click.exceptions.Exit(0)


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
@click.argument("modules", nargs=-1, required=False)
@click.option("--changed", is_flag=True, default=False, help="Select changed addon modules.")
@click.option("--base", default=None, help="Git baseline used with --changed.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Plan only.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Confirm execution.")
@output_options
@pass_cli_context
def module_update(  # noqa: C901
    ctx: CliContext,
    modules: tuple[str, ...],
    changed: bool,
    base: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    if changed and modules:
        raise click.UsageError("MODULE cannot be combined with --changed")
    if base is not None and not changed:
        raise click.UsageError("--base requires --changed")
    if not changed and not modules:
        raise click.UsageError("module update requires MODULE or --changed")
    try:
        runtime_context = cli_context.ready_instance(ctx)
        runtime = runtime_context.runtime
        instance = runtime_context.instance
        if changed:
            selection = instance.modules.changed_plan(runtime, base=base)
            if selection.unmapped_paths:
                fail(
                    output_mode,
                    "module.update",
                    "changed paths are not mapped to safe addon modules: "
                    + ", ".join(selection.unmapped_paths),
                    dry_run=dry_run,
                    error_code="module_changed_path_unmapped",
                )
            if selection.not_installed and not dry_run:
                fail(
                    output_mode,
                    "module.update",
                    "modules not installed: " + ", ".join(selection.not_installed),
                    dry_run=dry_run,
                )
            if not selection.modules:
                payload = _module_update_payload(selection, None, dry_run=dry_run)
                payload["reason"] = (
                    "no_installed_modules" if selection.not_installed else "no_addon_changes"
                )
                emit(
                    success_document(command="module.update", result=payload, dry_run=dry_run),
                    output_mode,
                    rich=_rich_module_update,
                )
                raise click.exceptions.Exit(0)  # noqa: TRY301
        else:
            selected_modules = tuple(modules)
            selection = ModuleUpdatePlan(modules=selected_modules)

        from odoo_instance_sdk.resources.module import ModuleResource

        resource = getattr(instance, "modules", None)
        if not changed and isinstance(resource, ModuleResource):
            selection = resource.plan_update(selection.modules, selection=selection)
            if selection.not_installed and not dry_run:
                fail(
                    output_mode,
                    "module.update",
                    "modules not installed: " + ", ".join(selection.not_installed),
                    dry_run=dry_run,
                )

        def build_command() -> Command[CommandResult]:
            resource = getattr(instance, "modules", None)
            if isinstance(resource, ModuleResource):
                return resource.update_command(selection.modules, selection=selection)
            # Keep the extracted callback seam usable for lightweight test and
            # compatibility instances that predate OdooInstance.modules.
            return cast(
                "Callable[..., Command[CommandResult]]",
                getattr(sys.modules["odoo_instance_sdk.cli"], "update_modules_command"),
            )(instance, selection.modules)

        status, _outcome = run_or_preview(
            build_command,
            command_name="module.update",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: _checked_module_update_payload(selection, value),
            confirm=(
                lambda: fail(
                    output_mode, "module.update", "module update requires --yes", dry_run=dry_run
                )
            )
            if not yes
            else None,
            preview=lambda command: {
                **_module_update_payload(selection, None, dry_run=True),
                "plan": model_to_dict(command.plan),
            },
            rich=_rich_module_update,
            progress=True,
        )
    except SystemExit:
        raise
    except Exception as exc:
        if isinstance(exc, _ChangedSelectionError):
            selected = exc.plan
            details = _module_update_payload(
                ModuleUpdatePlan(
                    modules=tuple(selected.modules),
                    base_source=selected.base_source,
                    requested_base=selected.requested_base,
                    resolved_base=selected.resolved_base,
                    merge_base=selected.merge_base,
                    head=selected.head,
                    changed_files=selected.changed_files,
                    ignored_paths=selected.ignored_paths,
                    unmapped_paths=selected.unmapped_paths,
                ),
                None,
                dry_run=dry_run,
            )
            fail(
                output_mode,
                "module.update",
                exc,
                dry_run=dry_run,
                error_code="module_changed_selection_failed",
                details=details,
            )
        if getattr(exc, "code", None) == "module_operation_in_progress":
            fail(
                output_mode,
                "module.update",
                exc,
                dry_run=dry_run,
                error_code="module_operation_in_progress",
            )
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
