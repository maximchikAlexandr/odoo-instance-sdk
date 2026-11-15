"""CLI leaves for structured Odoo logs, cursors, and marks."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import TYPE_CHECKING, NoReturn, TypeVar

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    command_options,
    emit,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
    success_document,
)
from odoo_instance_sdk.exceptions import LogQueryError
from odoo_instance_sdk.models.logs import (
    LogEvent,
    LogMark,
    LogMarkDeletion,
    LogMarkList,
    LogPage,
)

_MarkValue = TypeVar("_MarkValue", LogMark, LogMarkList)

_SEARCH_FORMATS = ("rich", "json", "toon", "jsonl")


def register_log_commands(parent: click.Group) -> None:  # noqa: C901
    """Replace the logs placeholder with the structured log command group."""

    @parent.group(
        "logs",
        invoke_without_command=True,
        help="Read or follow retained Odoo logs.",
    )
    @click.option("-n", "--tail", type=click.IntRange(min=1), default=100, show_default=True)
    @click.option("-f", "--follow", is_flag=True, default=False)
    @click.option("--run", default=None, help="current, last, or one owned run id.")
    @pass_cli_context
    def logs(ctx: CliContext, tail: int, follow: bool, run: str | None) -> None:
        """Tail raw text when no structured subcommand is selected."""
        if click.get_current_context().invoked_subcommand:
            return
        try:
            instance = cli_context.ready_instance(ctx).instance
            lines = (
                instance.iter_run_logs(run=run, tail=tail, follow=follow)
                if instance.structured_odoo_logs() is True
                else instance.iter_logs(tail=tail, follow=follow)
            )
            for line in lines:
                sys.stdout.write(line)
                sys.stdout.flush()
        except KeyboardInterrupt:
            sys.exit(130)
        except Exception as error:
            fail(OutputMode.RICH, "logs", error, dry_run=False)

    @logs.command("search", help="Return one bounded page of structured Odoo events.")
    @click.option("--text", default=None)
    @click.option("--level", "levels", multiple=True)
    @click.option("--logger", "logger_prefix", default=None)
    @click.option("--database", default=None)
    @click.option("--since", type=click.DateTime())
    @click.option("--until", "until_time", type=click.DateTime())
    @click.option("--after-cursor", default=None)
    @click.option("--after-mark", default=None)
    @click.option("--until-cursor", default=None)
    @click.option("--limit", type=click.IntRange(min=1), default=None)
    @click.option("--follow", is_flag=True, default=False)
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(_SEARCH_FORMATS, case_sensitive=True),
        default=None,
        help="Output format (default: rich).",
    )
    @pass_cli_context
    def search(
        ctx: CliContext,
        text: str | None,
        levels: tuple[str, ...],
        logger_prefix: str | None,
        database: str | None,
        since: datetime | None,
        until_time: datetime | None,
        after_cursor: str | None,
        after_mark: str | None,
        until_cursor: str | None,
        limit: int | None,
        follow: bool,
        output_format: str | None,
    ) -> None:
        """Emit one structured page, or JSONL when following new records."""
        if after_cursor and after_mark:
            raise click.UsageError("use either --after-cursor or --after-mark")
        if follow and output_format != "jsonl":
            raise click.UsageError("follow requires --format jsonl")
        mode = _search_mode(output_format)
        try:
            instance = cli_context.ready_instance(ctx).instance
            after = after_cursor or (
                instance.resolve_log_mark(after_mark) if after_mark is not None else None
            )
            selected = _selected_run()
            if follow:
                present_log_follow(
                    instance.iter_log_events(
                        run=selected,
                        text=text,
                        levels=levels,
                        logger_prefix=logger_prefix,
                        database=database,
                        since=since,
                        until=until_time,
                        after=after,
                        follow=True,
                    ),
                    cursor=after,
                )
                return
            present_log_page(
                instance.search_log_events(
                    run=selected,
                    text=text,
                    levels=levels,
                    logger_prefix=logger_prefix,
                    database=database,
                    since=since,
                    until=until_time,
                    after=after,
                    until_cursor=until_cursor,
                    limit=limit,
                ),
                output_format or "rich",
            )
        except click.UsageError:
            raise
        except Exception as error:
            _fail_query("logs.search", error, mode)

    @logs.command("cursor", help="Print the high-water cursor for one run.")
    @output_options
    @pass_cli_context
    def cursor(
        ctx: CliContext,
        output_format: str | None,
        json_output: bool,
    ) -> None:
        """Print a raw token in rich mode and a document otherwise."""
        mode = resolve_output_mode(output_format, json_output)
        try:
            value = cli_context.ready_instance(ctx).instance.log_cursor(run=_selected_run())
        except Exception as error:
            _fail_query("logs.cursor", error, mode)
        if mode is OutputMode.RICH:
            click.echo(value.cursor)
            return
        emit(
            success_document(command="logs.cursor", result=model_to_dict(value)),
            mode,
        )

    @logs.group("mark", help="Create, show, list, and delete log marks.")
    def mark() -> None:
        """Create, show, list, and delete log marks."""

    @mark.command("create", help="Store a named cursor at the current boundary.")
    @click.argument("name")
    @click.option("--replace", is_flag=True, default=False)
    @command_options
    @pass_cli_context
    def mark_create(
        ctx: CliContext,
        name: str,
        replace: bool,
        dry_run: bool,
        output_format: str | None,
        json_output: bool,
    ) -> None:
        """Preview or write one mark through the captured command."""
        mode = resolve_output_mode(output_format, json_output)
        instance = cli_context.ready_instance(ctx).instance
        selected = _selected_run()
        try:
            run_or_preview(
                lambda: instance.create_log_mark_command(name, run=selected, replace=replace),
                command_name="logs.mark.create",
                mode=mode,
                dry_run=dry_run,
                result=_mark_document,
            )
        except Exception as error:
            _fail_query("logs.mark.create", error, mode, dry_run=dry_run)

    @mark.command("show", help="Show one stored mark.")
    @click.argument("name")
    @output_options
    @pass_cli_context
    def mark_show(
        ctx: CliContext,
        name: str,
        output_format: str | None,
        json_output: bool,
    ) -> None:
        """Show one mark without a plan."""
        _emit_mark(
            "logs.mark.show",
            _show_mark(ctx, name),
            output_format,
            json_output,
        )

    @mark.command("list", help="List marks for the selected environment.")
    @output_options
    @pass_cli_context
    def mark_list(
        ctx: CliContext,
        output_format: str | None,
        json_output: bool,
    ) -> None:
        """List marks without a plan."""
        _emit_mark(
            "logs.mark.list",
            _list_marks(ctx),
            output_format,
            json_output,
        )

    @mark.command("delete", help="Delete one stored mark.")
    @click.argument("name")
    @command_options
    @pass_cli_context
    def mark_delete(
        ctx: CliContext,
        name: str,
        dry_run: bool,
        output_format: str | None,
        json_output: bool,
    ) -> None:
        """Preview or delete one mark through the captured command."""
        mode = resolve_output_mode(output_format, json_output)
        instance = cli_context.ready_instance(ctx).instance
        try:
            run_or_preview(
                lambda: instance.delete_log_mark_command(name),
                command_name="logs.mark.delete",
                mode=mode,
                dry_run=dry_run,
                result=_deletion_document,
            )
        except Exception as error:
            _fail_query("logs.mark.delete", error, mode, dry_run=dry_run)


def present_log_page(page: LogPage, output_format: str) -> None:
    """Emit one search page as JSONL or as a bounded document."""
    if output_format == "jsonl":
        _jsonl(
            {
                "type": "checkpoint",
                "cursor": page.window_end_cursor,
                "complete": page.complete,
            }
        )
        for event in page.events:
            _jsonl({"type": "event", **model_to_dict(event)})
        return
    emit(
        success_document(command="logs.search", result=model_to_dict(page)),
        _search_mode(output_format),
        rich=_rich_page,
    )


def present_log_follow(events: Iterator[LogEvent], *, cursor: str | None) -> None:
    """Write JSONL events and checkpoints, including one after Ctrl+C."""
    last = cursor
    count = 0
    _jsonl({"type": "checkpoint", "cursor": last, "complete": False})
    try:
        for event in events:
            _jsonl({"type": "event", **model_to_dict(event)})
            last = event.cursor
            count += 1
            if count % 20 == 0:
                _jsonl({"type": "checkpoint", "cursor": last, "complete": False})
    except KeyboardInterrupt:
        _jsonl({"type": "checkpoint", "cursor": last, "complete": False})
        raise SystemExit(130) from None


def _mark_document(value: LogMark | None) -> JsonObject:
    """Project one created mark into the CLI envelope."""
    if value is None:
        return {}
    return model_to_dict(value)


def _deletion_document(value: LogMarkDeletion | None) -> JsonObject:
    """Project one deleted mark into the CLI envelope."""
    if value is None:
        return {}
    return model_to_dict(value)


def _show_mark(ctx: CliContext, name: str) -> Callable[[], LogMark]:
    """Return a loader for one named mark."""

    def load() -> LogMark:
        return cli_context.ready_instance(ctx).instance.show_log_mark(name)

    return load


def _list_marks(ctx: CliContext) -> Callable[[], LogMarkList]:
    """Return a loader for the environment mark list."""

    def load() -> LogMarkList:
        return cli_context.ready_instance(ctx).instance.list_log_marks()

    return load


def _emit_mark(
    command: str,
    operation: Callable[[], _MarkValue],
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        value = operation()
    except Exception as error:
        _fail_query(command, error, mode)
    emit(success_document(command=command, result=model_to_dict(value)), mode)


def _fail_query(
    command: str,
    error: BaseException,
    mode: OutputMode,
    *,
    dry_run: bool = False,
) -> NoReturn:
    fail(
        mode,
        command,
        error,
        dry_run=dry_run,
        error_code=error.code if isinstance(error, LogQueryError) else None,
    )


def _search_mode(output_format: str | None) -> OutputMode:
    if output_format in (None, "rich", "jsonl"):
        return OutputMode.RICH
    return OutputMode(output_format)


def _selected_run() -> str | None:
    parent = click.get_current_context().parent
    if parent is None:
        return None
    run = parent.params.get("run")
    return run if isinstance(run, str) else None


def _jsonl(payload: JsonObject) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _rich_page(document: OutputDocument) -> str:
    result = document.result
    if not isinstance(result, dict):
        return ""
    events = result.get("events")
    if not isinstance(events, list) or not events:
        return "no events"
    lines: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        level = event.get("level")
        message = event.get("message")
        rendered_level = level if isinstance(level, str) and level else "-"
        rendered_message = message if isinstance(message, str) else ""
        lines.append(f"{rendered_level} {rendered_message}")
    return "\n".join(lines)
