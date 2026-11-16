"""CLI adapters for the shared GreenMail test service."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from odoo_instance_sdk.commands.context import CliContext, pass_cli_context, ready_instance
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.execution import Command, JsonValue
from odoo_instance_sdk.models.mail import MailConfigureResult, MailStatus
from odoo_instance_sdk.resources.mail import (
    mail_configure_command,
    mail_down_command,
    mail_status_command,
    mail_up_command,
)


def _endpoint_port(value: JsonValue) -> str:
    if isinstance(value, dict):
        return str(value.get("port", ""))
    return ""


def _rich_mail(document: OutputDocument) -> str:
    """Render a mail result without secrets or machine-document keys."""
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    value = document.result if isinstance(document.result, dict) else {}
    if value.get("undone") is True:
        return f"Restored mail settings for {value.get('database')}."
    if "mailbox" in value:
        database = value.get("database")
        mailbox = value.get("mailbox")
        smtp_host = value.get("smtp_host")
        credentials_path = value.get("credentials_path")
        return (
            f"Configured {database} for {mailbox} "
            f"via {smtp_host}. Credentials remain in {credentials_path}."
        )
    state = str(value.get("state", "unknown"))
    if state != "running":
        return f"GreenMail is {state}."
    bind_host = value.get("bind_host")
    return (
        f"GreenMail running on {bind_host} "
        f"SMTP {_endpoint_port(value.get('smtp'))} "
        f"IMAP {_endpoint_port(value.get('imap'))} "
        f"API {_endpoint_port(value.get('api'))}"
    )


def _payload(value: MailStatus | MailConfigureResult) -> JsonObject:
    return model_to_dict(value)


@click.group("mail", help="Run the shared user-level GreenMail test service.")
def mail_group() -> None:
    """Run the shared user-level GreenMail test service."""


@mail_group.command("up", help="Start or reuse this user's GreenMail service.")
@click.option("--dry-run", is_flag=True, default=False, help="Show the plan without starting it.")
@output_options
@pass_cli_context
def mail_up(ctx: CliContext, output_format: str | None, json_output: bool, dry_run: bool) -> None:
    """Start or reuse this user's GreenMail service."""
    mode = resolve_output_mode(output_format, json_output)

    def build_up() -> Command[MailStatus]:
        return mail_up_command()

    try:
        run_or_preview(
            build_up,
            command_name="mail.up",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: {} if value is None else _payload(value),
            rich=_rich_mail,
        )
    except Exception as exc:
        fail(mode, "mail.up", exc, dry_run=dry_run)


@mail_group.command("status", help="Show GreenMail endpoints without secrets.")
@click.option("--dry-run", is_flag=True, default=False, help="Show the status plan.")
@output_options
@pass_cli_context
def mail_status(
    ctx: CliContext, output_format: str | None, json_output: bool, dry_run: bool
) -> None:
    """Show GreenMail endpoints without secrets."""
    mode = resolve_output_mode(output_format, json_output)

    def build_status() -> Command[MailStatus]:
        return mail_status_command()

    try:
        run_or_preview(
            build_status,
            command_name="mail.status",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: {} if value is None else _payload(value),
            rich=_rich_mail,
        )
    except Exception as exc:
        fail(mode, "mail.status", exc, dry_run=dry_run)


@mail_group.command("down", help="Stop only this user's GreenMail service.")
@click.option("--dry-run", is_flag=True, default=False, help="Show the plan without stopping it.")
@output_options
@pass_cli_context
def mail_down(ctx: CliContext, output_format: str | None, json_output: bool, dry_run: bool) -> None:
    """Stop only this user's GreenMail service."""
    mode = resolve_output_mode(output_format, json_output)

    def build_down() -> Command[MailStatus]:
        return mail_down_command()

    try:
        run_or_preview(
            build_down,
            command_name="mail.down",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: {} if value is None else _payload(value),
            rich=_rich_mail,
        )
    except Exception as exc:
        fail(mode, "mail.down", exc, dry_run=dry_run)


@mail_group.command("configure", help="Point one selected database at GreenMail.")
@click.option(
    "--undo", is_flag=True, default=False, help="Restore this database's previous mail settings."
)
@click.option(
    "--dry-run", is_flag=True, default=False, help="Show the plan without changing the database."
)
@output_options
@pass_cli_context
def mail_configure(
    ctx: CliContext,
    output_format: str | None,
    json_output: bool,
    dry_run: bool,
    undo: bool,
) -> None:
    """Point one selected database at GreenMail, or restore its previous settings."""
    mode = resolve_output_mode(output_format, json_output)
    try:
        resolved = ready_instance(ctx)
        run_or_preview(
            lambda: mail_configure_command(resolved.instance, undo=undo),
            command_name="mail.configure",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: {} if value is None else _payload(value),
            rich=_rich_mail,
        )
    except Exception as exc:
        fail(mode, "mail.configure", exc, dry_run=dry_run)
