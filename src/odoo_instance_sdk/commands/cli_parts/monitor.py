"""Composition-boundary registration for the observability monitor."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from odoo_instance_sdk.commands.output import (
    JsonObject,
    command_options,
    fail,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.execution import Command
from odoo_instance_sdk.resources.monitor import (
    monitor_logs_command,
    monitor_status_command,
    restart_monitor_command,
    serve_monitor,
    stop_monitor_command,
)

if TYPE_CHECKING:
    import click
else:
    import rich_click as click


_F = TypeVar("_F")


def _monitor_options(func: _F) -> _F:
    """Apply the shared monitor start options once."""
    wrapped = func
    for option in (
        click.option(
            "--settings",
            type=click.Path(path_type=Path),
            default=None,
            help="Publication settings file (requires --external).",
        ),
        click.option(
            "--external",
            "external_mode",
            is_flag=True,
            default=False,
            help="Publish the monitor through the configured Caddy panel route.",
        ),
        click.option(
            "--detach", is_flag=True, default=False, help="Return after the monitor is ready."
        ),
        click.option("--no-open", is_flag=True, default=False, help="Do not open a browser."),
        click.option(
            "--port",
            type=int,
            default=None,
            help="Exact port (else auto-select 8069 or 8100-8120).",
        ),
        click.option(
            "--host",
            default="127.0.0.1",
            help="Loopback bind address (127.0.0.1, localhost, or ::1).",
        ),
        click.option(
            "--headless", is_flag=True, default=False, help="Serve API only, no UI/browser."
        ),
    ):
        wrapped = cast("Callable[[_F], _F]", option)(wrapped)
    return wrapped


def register_monitor_command(group: click.Group) -> None:
    """Register monitor start, stop, restart, status, and logs."""

    @group.group("monitor", invoke_without_command=True)
    @_monitor_options
    @click.pass_context
    def monitor_group(
        ctx: click.Context,
        headless: bool,
        host: str,
        port: int | None,
        no_open: bool,
        detach: bool,
        external_mode: bool,
        settings: Path | None,
    ) -> None:
        """Start the observability monitor (FastAPI + React UI)."""
        if ctx.invoked_subcommand is None:
            _start_monitor(
                headless=headless,
                host=host,
                port=port,
                no_open=no_open,
                detach=detach,
                external_mode=external_mode,
                settings=settings,
            )

    @monitor_group.command("start")
    @_monitor_options
    def monitor_start(
        headless: bool,
        host: str,
        port: int | None,
        no_open: bool,
        detach: bool,
        external_mode: bool,
        settings: Path | None,
    ) -> None:
        """Start the observability monitor (FastAPI + React UI)."""
        _start_monitor(
            headless=headless,
            host=host,
            port=port,
            no_open=no_open,
            detach=detach,
            external_mode=external_mode,
            settings=settings,
        )

    _register_monitor_controls(monitor_group)


def _register_monitor_controls(monitor_group: click.Group) -> None:
    """Register the addressable monitor control commands."""

    @monitor_group.command("stop")
    @command_options
    def monitor_stop(dry_run: bool, output_format: str | None, json_output: bool) -> None:
        """Stop the supervised monitor without stopping Odoo."""
        _run_monitor("monitor.stop", _stop_build, dry_run, output_format, json_output)

    @monitor_group.command("restart")
    @command_options
    def monitor_restart(dry_run: bool, output_format: str | None, json_output: bool) -> None:
        """Restart the supervised monitor without rereading its command."""
        _run_monitor("monitor.restart", _restart_build, dry_run, output_format, json_output)

    @monitor_group.command("status")
    @command_options
    def monitor_status(dry_run: bool, output_format: str | None, json_output: bool) -> None:
        """Show Supervisor status for the monitor."""
        _run_monitor("monitor.status", _status_build, dry_run, output_format, json_output)

    @monitor_group.command("logs")
    @command_options
    def monitor_logs(dry_run: bool, output_format: str | None, json_output: bool) -> None:
        """Show the captured monitor log."""
        _run_monitor("monitor.logs", _logs_build, dry_run, output_format, json_output)


def _stop_build() -> Command[dict[str, str | int | None]]:
    return stop_monitor_command()


def _restart_build() -> Command[dict[str, str | int | None]]:
    return restart_monitor_command()


def _status_build() -> Command[dict[str, str | int | None]]:
    return monitor_status_command()


def _logs_build() -> Command[dict[str, str | int | None]]:
    return monitor_logs_command()


def _start_monitor(
    *,
    headless: bool,
    host: str,
    port: int | None,
    no_open: bool,
    detach: bool,
    external_mode: bool,
    settings: Path | None,
) -> None:
    """Start through the public monitor snapshot."""
    if settings is not None and not external_mode:
        raise click.UsageError("--settings requires --external")
    serve_monitor(
        host=host,
        port=port,
        headless=headless,
        no_open=no_open,
        settings=settings if external_mode else None,
        detach=detach,
        browser_url=None,
        external=external_mode,
    )


_ResultT = TypeVar("_ResultT")


def _run_monitor(
    command_name: str,
    build: Callable[[], Command[_ResultT]],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)

    def projected(value: _ResultT | None) -> JsonObject:
        if isinstance(value, dict):
            return cast("JsonObject", value)
        return {}

    try:
        run_or_preview(
            build,
            command_name=command_name,
            mode=output_mode,
            dry_run=dry_run,
            result=projected,
            rich=lambda _document: command_name,
        )
    except SystemExit:
        raise
    except Exception as error:
        fail(output_mode, command_name, error, dry_run=dry_run)


__all__ = ["register_monitor_command"]
