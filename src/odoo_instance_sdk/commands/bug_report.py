"""CLI adapters for the bug-report init/submit workflow."""

from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from rich.console import Console
from rich.table import Table

from odoo_instance_sdk.bug_report import (
    bug_report_init_command,
    bug_report_submit_command,
    bug_report_submit_preview,
)
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.exceptions import OdooInstanceSdkError
from odoo_instance_sdk.models.bug_report import BugReportSubmitResult


def _error_code(error: BaseException) -> str | None:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else None


def _error_details(error: BaseException) -> JsonObject | None:
    details = getattr(error, "details", None)
    return cast("JsonObject", details) if isinstance(details, dict) else None


def _rich_init(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    value = document.result if isinstance(document.result, dict) else {}
    table = Table("Field", "Value", title="Bug report init")
    for key, item in value.items():
        rendered = (
            json.dumps(item, ensure_ascii=False, default=str)
            if isinstance(item, (dict, list))
            else str(item)
        )
        table.add_row(str(key), rendered)
    console = Console(record=True, color_system=None, width=180, file=StringIO())
    console.print(table)
    return console.export_text().rstrip()


def _rich_submit(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    value = document.result if isinstance(document.result, dict) else {}
    table = Table("Field", "Value", title="Bug report submit")
    for key, item in value.items():
        rendered = (
            json.dumps(item, ensure_ascii=False, default=str)
            if isinstance(item, (dict, list))
            else str(item)
        )
        table.add_row(str(key), rendered)
    console = Console(record=True, color_system=None, width=180, file=StringIO())
    console.print(table)
    return console.export_text().rstrip()


@click.group("bug-report", help="Create and publish reproducible bug reports.")
def bug_report_group() -> None:
    pass


@bug_report_group.command("init", help="Create a local bug-report draft.")
@click.option("--title", "title", required=True, help="Report title (one line).")
@click.option(
    "--kind",
    "kind",
    type=click.Choice(["bug", "enhancement", "tech-debt"], case_sensitive=True),
    default="bug",
    help="Report kind (metadata only, not a GitHub label).",
)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Inspect without writing.")
@output_options
def bug_report_init(
    title: str,
    kind: str,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        status, _ = run_or_preview(
            lambda: bug_report_init_command(title=title, kind=kind),  # type: ignore[arg-type]
            command_name="bug-report.init",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: model_to_dict(value) if value is not None else {},
            rich=_rich_init,
        )
    except SystemExit:
        raise
    except OdooInstanceSdkError as error:
        fail(
            mode,
            "bug-report.init",
            error,
            dry_run=dry_run,
            error_code=_error_code(error),
            details=_error_details(error),
        )
    except Exception as error:
        fail(mode, "bug-report.init", error, dry_run=dry_run)
    raise click.exceptions.Exit(status)


@bug_report_group.command("submit", help="Validate and publish a bug-report draft.")
@click.argument("report_id")
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Inspect without sending.")
@output_options
def bug_report_submit(
    report_id: str,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        status, value = run_or_preview(
            lambda: bug_report_submit_command(report_id),
            command_name="bug-report.submit",
            mode=mode,
            dry_run=dry_run,
            result=lambda result: model_to_dict(result) if result is not None else {},
            preview=lambda _command: model_to_dict(bug_report_submit_preview(report_id)),
            rich=_rich_submit,
        )
        if isinstance(value, BugReportSubmitResult) and not value.report_valid and not dry_run:
            status = 1
    except SystemExit:
        raise
    except OdooInstanceSdkError as error:
        fail(
            mode,
            "bug-report.submit",
            error,
            dry_run=dry_run,
            error_code=_error_code(error),
            details=_error_details(error),
        )
    except Exception as error:
        fail(mode, "bug-report.submit", error, dry_run=dry_run)
    raise click.exceptions.Exit(status)


__all__ = ["bug_report_group"]
