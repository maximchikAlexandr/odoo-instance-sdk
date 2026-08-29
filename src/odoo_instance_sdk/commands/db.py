"""Database preparation commands at the public CLI boundary."""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from odoo_instance_sdk.commands.context import (
    CliContext,
    pass_cli_context,
    ready_instance,
    resolve_project_path,
)
from odoo_instance_sdk.commands.output import (
    OutputMode,
    emit,
    emit_command_plan,
    emit_json_envelope,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    rich_print,
    success_document,
)
from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    DatabaseRefreshOptions,
)


@click.group()
def db_group() -> None:
    """Prepare and reset project databases."""


@db_group.command("refresh")
@click.option("--restore", is_flag=True, default=False, help="Restore a fresh local copy.")
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
    reset_admin_password: bool,
    source_branch: str | None,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Download a project test backup, optionally restoring it locally."""
    output_mode = resolve_output_mode(output_format, json_output)
    if reset_admin_password and not restore:
        raise click.UsageError("--reset-admin-password requires --restore")
    try:
        project_path = resolve_project_path(ctx)
        client = _client_class()(config=_client_config_class()(executable="odoo"))
        from odoo_instance_sdk.execution import Command

        candidate = client.environments.refresh_database_command(
            project_path,
            options=DatabaseRefreshOptions(
                restore=restore,
                source_branch=source_branch,
                reset_admin_password=reset_admin_password,
            ),
        )
        command = candidate if isinstance(candidate, Command) else None
    except Exception as exc:
        fail(output_mode, "db.refresh", exc)

    if command is not None:
        if dry_run:
            emit_command_plan(command, command_name="db.refresh", mode=output_mode)
            return
        result = command.run()
        data = model_to_dict(result)
        emit(
            success_document(
                command="db.refresh",
                result=data,
                provenance={"project_source": ctx.project_source},
            ),
            output_mode,
            rich=lambda _document: json.dumps(data, indent=2, sort_keys=True),
        )
        return
    if dry_run:
        fail(output_mode, "db.refresh", "environment does not provide an inspectable command")
    try:
        result = client.environments.refresh_database(
            project_path,
            options=DatabaseRefreshOptions(
                restore=restore,
                source_branch=source_branch,
                reset_admin_password=reset_admin_password,
            ),
        )
    except Exception as exc:
        fail(output_mode, "db.refresh", exc)

    data = model_to_dict(result)
    if output_mode is not OutputMode.RICH:
        emit_json_envelope(
            ok=True,
            command="db.refresh",
            result=data,
            provenance={"project_source": ctx.project_source},
            mode=output_mode,
        )
        return
    rich_print(json.dumps(data, indent=2, sort_keys=True))


@db_group.command("reset-admin-password")
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
        _client, environment, instance = ready_instance(ctx)
        _validate_recorded_database_binding(instance, environment)
        from odoo_instance_sdk.execution import Command

        candidate = instance.databases.reset_admin_password_command()
        command = candidate if isinstance(candidate, Command) else None
        result = command.run() if command is not None and not dry_run else None
    except Exception as exc:
        fail(output_mode, "db.reset-admin-password", exc)

    if command is not None:
        if dry_run:
            emit_command_plan(command, command_name="db.reset-admin-password", mode=output_mode)
            return
        assert isinstance(result, AdminPasswordResetResult)
        data = model_to_dict(result)
        emit(
            success_document(
                command="db.reset-admin-password",
                result=data,
                context={"environment_id": str(environment.id)},
                provenance={"environment_source": ctx.environment_source},
            ),
            output_mode,
            rich=lambda _document: json.dumps(data, indent=2, sort_keys=True),
        )
        return
    if dry_run:
        fail(
            output_mode,
            "db.reset-admin-password",
            "database does not provide an inspectable command",
        )
    try:
        result = instance.databases.reset_admin_password()
    except Exception as exc:
        fail(output_mode, "db.reset-admin-password", exc)

    assert isinstance(result, AdminPasswordResetResult)
    data = model_to_dict(result)
    if output_mode is not OutputMode.RICH:
        emit_json_envelope(
            ok=True,
            command="db.reset-admin-password",
            result=data,
            context={"environment_id": str(environment.id)},
            provenance={"environment_source": ctx.environment_source},
            mode=output_mode,
        )
        return
    rich_print(json.dumps(data, indent=2, sort_keys=True))


def _validate_recorded_database_binding(instance: object, environment: object) -> None:
    config = getattr(instance, "config")
    configured = tuple(config.configured_database_names)
    target = getattr(environment, "target_db_name")
    source = getattr(environment, "source_db_name")
    recorded = target or source
    if recorded is None or configured != (recorded,):
        raise InstanceConfigurationError(
            "environment must bind exactly one recorded source or target database"
        )


def _client_class() -> Any:
    return getattr(sys.modules[__name__], "OdooClient")


def _client_config_class() -> Any:
    return getattr(sys.modules[__name__], "OdooClientConfig")


def __getattr__(name: str) -> Any:
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
