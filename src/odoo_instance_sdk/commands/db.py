"""Database preparation commands at the public CLI boundary."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from rich.console import Console
from rich.text import Text

from odoo_instance_sdk.commands.context import (
    CliContext,
    pass_cli_context,
    project_provenance,
    ready_instance,
    resolve_project_path,
)
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    OutputMode,
    emit_json_envelope,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    rich_print,
    run_or_preview,
)
from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    DatabaseRefreshOptions,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.pg.drop import DatabaseDropResult
    from odoo_instance_sdk.internal.proc import StepEvent, StepObserver
    from odoo_instance_sdk.models import DatabasePreparationResult, DevelopmentEnvironment
    from odoo_instance_sdk.resources.instance import OdooInstance


class _RichLive(Protocol):
    def update(self, renderable: Text, *, refresh: bool = False) -> None: ...


class _RestoreStepObserver:
    """Rich lifecycle rendering used only by the restore command."""

    def __init__(self, *, live: _RichLive | None = None, show_command_output: bool = False) -> None:
        self._live = live
        self._show_command_output = show_command_output
        self._lines: list[str] = []

    def __call__(self, event: StepEvent) -> None:
        if event.kind in {"stdout", "stderr"} and not self._show_command_output:
            return
        stream = event.kind in {"stdout", "stderr"}
        if stream:
            suffix = f": {event.chunk or ''}"
        elif event.error:
            suffix = f": {event.error}"
        elif event.returncode is not None:
            suffix = f" (exit {event.returncode})"
        else:
            suffix = ""
        from odoo_instance_sdk.internal.proc.redaction import redacted_projection

        line = cast(
            "str",
            redacted_projection(
                f"[{event.step_id}] {event.kind}{suffix}",
                field="error" if event.error else (event.kind if stream else "event"),
            ),
        )
        rendered_lines = line.splitlines() or [line]
        if stream and len(rendered_lines) > 1:
            prefix = f"[{event.step_id}] {event.kind}: "
            rendered_lines = [rendered_lines[0], *(prefix + item for item in rendered_lines[1:])]
        self._lines.extend(rendered_lines)
        if self._live is not None:
            self._live.update(Text("\n".join(self._lines)), refresh=True)
        else:
            for rendered_line in rendered_lines:
                rich_print(rendered_line)


@contextmanager
def _restore_step_observer(*, show_command_output: bool = False) -> Iterator[_RestoreStepObserver]:
    """Own TTY ``Live`` only for Rich restore execution."""
    console = Console()
    if not console.is_terminal:
        yield _RestoreStepObserver(show_command_output=show_command_output)
        return
    from rich.live import Live

    with Live("", console=console, transient=True) as live:
        yield _RestoreStepObserver(live=live, show_command_output=show_command_output)


@click.group(help="Prepare and reset project databases.")
def db_group() -> None:
    """Prepare and reset project databases."""


@db_group.command("refresh", help="Download and optionally restore a project test backup.")
@click.option("--restore", is_flag=True, default=False, help="Restore a fresh local copy.")
@click.option(
    "--show-command-output",
    is_flag=True,
    default=False,
    help="Show sanitized subprocess output (Rich only).",
)
@click.option(
    "--reset-admin-password",
    "reset_admin_password",
    is_flag=True,
    default=False,
    help="Reset base.user_admin after restoring.",
)
@click.option("--source-branch", default=None, help="Source Git branch provenance.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def db_refresh(
    ctx: CliContext,
    restore: bool,
    show_command_output: bool,
    reset_admin_password: bool,
    source_branch: str | None,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Download a project test backup, optionally restoring it locally."""
    output_mode = resolve_output_mode(output_format, json_output)
    if show_command_output and not restore:
        raise click.UsageError("--show-command-output requires --restore")
    if show_command_output and output_mode is not OutputMode.RICH:
        raise click.UsageError("--show-command-output is only available with Rich output")
    if reset_admin_password and not restore:
        raise click.UsageError("--reset-admin-password requires --restore")
    try:
        project_path = resolve_project_path(ctx)
        client = _client_class()(config=_client_config_class()(executable="odoo"))
        command = client.environments.refresh_database_command(
            project_path,
            options=DatabaseRefreshOptions(
                restore=restore,
                source_branch=source_branch,
                reset_admin_password=reset_admin_password,
            ),
        )
    except Exception as exc:
        fail(output_mode, "db.refresh", exc)

    runner = run_or_preview

    def run(observer: StepObserver | None = None) -> tuple[int, DatabasePreparationResult | None]:
        return runner(
            lambda: command,
            command_name="db.refresh",
            mode=output_mode,
            dry_run=dry_run,
            result=cast(
                "Callable[[DatabasePreparationResult | None], dict[str, JsonValue]]", model_to_dict
            ),
            provenance={"project_source": project_provenance(ctx)},
            rich=lambda document: json.dumps(document.result, indent=2, sort_keys=True),
            observer=observer,
            observe_output=show_command_output,
        )

    try:
        if restore and output_mode is OutputMode.RICH and not dry_run:
            with _restore_step_observer(show_command_output=show_command_output) as observer:
                status, _result = run(observer)
        else:
            status, _result = run()
    except Exception as exc:
        fail(output_mode, "db.refresh", exc)
    raise click.exceptions.Exit(status)


@db_group.command(
    "reset-admin-password", help="Reset the administrator on the ready environment database."
)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def db_reset_admin_password(
    ctx: CliContext,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Reset the administrator on the exact ready environment binding."""
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        runtime_context = ready_instance(ctx)
        instance = runtime_context.instance
        environment = runtime_context.require_environment()
        _validate_recorded_database_binding(instance, environment)
        command = instance.databases.reset_admin_password_command()
    except Exception as exc:
        fail(output_mode, "db.reset-admin-password", exc)

    try:
        status, result = run_or_preview(
            lambda: command,
            command_name="db.reset-admin-password",
            mode=output_mode,
            dry_run=dry_run,
            result=cast(
                "Callable[[AdminPasswordResetResult | None], dict[str, JsonValue]]", model_to_dict
            ),
            context={"environment_id": str(environment.id)},
            provenance=cast("dict[str, JsonValue]", runtime_context.output_provenance),
            rich=lambda document: json.dumps(document.result, indent=2, sort_keys=True),
        )
    except Exception as exc:
        fail(output_mode, "db.reset-admin-password", exc)
    if not dry_run:
        assert isinstance(result, AdminPasswordResetResult)
    raise click.exceptions.Exit(status)


@db_group.command("drop", help="Safely drop one database from the project PostgreSQL cluster.")
@click.argument("database")
@click.option(
    "--force-default", is_flag=True, default=False, help="Allow dropping the project default."
)
@click.option(
    "--force-connections",
    is_flag=True,
    default=False,
    help="Terminate active sessions attached to the exact target.",
)
@click.option("--yes", is_flag=True, default=False, help="Skip interactive confirmation.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def db_drop(
    ctx: CliContext,
    database: str,
    force_default: bool,
    force_connections: bool,
    yes: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Safely drop one exact database from the resolved project cluster."""
    output_mode = resolve_output_mode(output_format, json_output)
    if not dry_run and not yes and output_mode is not OutputMode.RICH:
        emit_json_envelope(
            ok=False,
            command="db.drop",
            error_code="confirmation_required",
            error_message="db drop requires --yes in machine output mode",
            mode=output_mode,
        )
        raise click.exceptions.Exit(1)
    try:
        from odoo_instance_sdk.commands.pg import _database_instance
        from odoo_instance_sdk.internal.pg.drop import build_database_drop_command

        project_root = resolve_project_path(ctx)
        _environment, instance = _database_instance(ctx)
        command = build_database_drop_command(
            instance,
            project_root,
            database,
            force_default=force_default,
            force_connections=force_connections,
        )
        cluster = getattr(instance, "_postgres_cluster", None)
        cluster_endpoint = getattr(cluster, "endpoint", "bound cluster")

        def confirm() -> None:
            click.confirm(
                f"Drop database {database!r} on cluster {cluster_endpoint}?",
                default=False,
                abort=True,
            )

        status, _result = run_or_preview(
            lambda: command,
            command_name="db.drop",
            mode=output_mode,
            dry_run=dry_run,
            result=cast(
                "Callable[[DatabaseDropResult | None], dict[str, JsonValue]]", model_to_dict
            ),
            context={"database": database, "cluster": str(cluster_endpoint)},
            provenance={"project_source": project_provenance(ctx)},
            confirm=None if yes or dry_run else confirm,
            rich=_drop_rich,
        )
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        fail(output_mode, "db.drop", exc)
    raise click.exceptions.Exit(status)


def _drop_rich(document: OutputDocument) -> str:
    payload = document.result if isinstance(document.result, dict) else {}
    if "database" not in payload:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    database = payload.get("database", "database")
    cluster = payload.get("cluster", "bound cluster")
    if payload.get("dropped") is False:
        return f"Database {database} was not dropped on {cluster}"
    return f"Dropped database {database} on {cluster}"


def _validate_recorded_database_binding(
    instance: OdooInstance, environment: DevelopmentEnvironment
) -> None:
    config = getattr(instance, "config")
    configured = tuple(config.configured_database_names)
    target = getattr(environment, "target_db_name")
    source = getattr(environment, "source_db_name")
    recorded = target or source
    if recorded is None or configured != (recorded,):
        raise InstanceConfigurationError(
            "environment must bind exactly one recorded source or target database"
        )


def _client_class() -> type[OdooClient]:
    return cast("type[OdooClient]", getattr(sys.modules[__name__], "OdooClient"))


def _client_config_class() -> type[OdooClientConfig]:
    return cast("type[OdooClientConfig]", getattr(sys.modules[__name__], "OdooClientConfig"))


def __getattr__(name: str) -> type[OdooClient | OdooClientConfig]:
    """Keep operation dependencies out of command discovery while preserving patch points."""
    if name == "OdooClient":
        from odoo_instance_sdk.client import OdooClient

        globals()[name] = OdooClient
        return OdooClient
    if name == "OdooClientConfig":
        from odoo_instance_sdk.config import OdooClientConfig

        globals()[name] = OdooClientConfig
        return OdooClientConfig
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["db_group"]
