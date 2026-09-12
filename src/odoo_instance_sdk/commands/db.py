"""Database preparation commands at the public CLI boundary."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    import click
    import msgspec
else:
    import rich_click as click

from rich.console import Console
from rich.table import Table

from odoo_instance_sdk.commands.context import (
    CliContext,
    pass_cli_context,
    project_provenance,
    ready_instance,
    resolve_environment,
    resolve_project_path,
)
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    OutputMode,
    _InspectableCommand,
    _rich_plan_projection,
    emit,
    emit_json_envelope,
    fail,
    failure_document,
    field_schema,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
    run_rich_bounded,
)
from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.cli_format import human_bytes as _human_bytes
from odoo_instance_sdk.internal.cli_format import rich_cell
from odoo_instance_sdk.internal.pg.inventory import DatabaseInventoryResult
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    DatabaseRefreshOptions,
)

_RestoreResult = TypeVar("_RestoreResult")

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.pg.drop import DatabaseDropResult
    from odoo_instance_sdk.internal.proc import PrivateJsonValue, RunContext, StepObserver
    from odoo_instance_sdk.models import DatabasePreparationResult, DevelopmentEnvironment
    from odoo_instance_sdk.resources.instance import AuxiliaryRestoreSession, OdooInstance


def _run_rich_restore(
    run: Callable[[StepObserver], tuple[int, DatabasePreparationResult | None]],
    *,
    show_command_output: bool,
) -> tuple[int, DatabasePreparationResult | None]:
    """Compatibility seam backed by the shared bounded Rich runner."""
    return run_rich_bounded(
        run,
        show_command_output=show_command_output,
        console=Console(),
        include_elapsed=False,
    )


def _validate_replace_context(client: OdooClient, environment: DevelopmentEnvironment) -> None:
    """Reject non-COPY or live contexts before constructing a mutation command."""
    from odoo_instance_sdk.models import DevelopmentEnvironment as _DevelopmentEnvironment

    if not isinstance(environment, _DevelopmentEnvironment):
        return
    if environment.removed_at is not None or environment.state.value == "removed":
        raise InstanceConfigurationError("replacement requires a non-removed environment")
    if environment.db_mode.value != "copy":
        raise InstanceConfigurationError("replacement requires a COPY environment")
    if environment.state.value not in {"ready", "cleanup_failed"}:
        raise InstanceConfigurationError("replacement requires a ready or retryable environment")
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = client.get_catalog()
    if isinstance(catalog, BackupCatalog) and catalog.get_environment_runtime(str(environment.id)):
        raise InstanceConfigurationError("replacement requires a stopped environment runtime")


def _attach_auxiliary_restore_runtime(
    command: _InspectableCommand[_RestoreResult],
    session: AuxiliaryRestoreSession,
) -> _InspectableCommand[_RestoreResult]:
    """Add the stopped-project helper to the existing restore ledger."""
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import prepared_command
    from odoo_instance_sdk.resources.instance import (
        AuxiliaryRestoreSession,
        activate_auxiliary_restore_session,
        reset_auxiliary_restore_session,
    )

    if not isinstance(command, Command) or not isinstance(session, AuxiliaryRestoreSession):
        return command
    prepared = command._prepared()
    auxiliary_start_steps = (
        session.start_step,
        session.ready_action,
    )
    local_restore_index = next(
        (
            index
            for index, step in enumerate(prepared.steps)
            if step.step_id == "database.prepare.local-restore"
        ),
        len(prepared.steps),
    )
    prepared_steps = (
        *prepared.steps[:local_restore_index],
        *auxiliary_start_steps,
        *prepared.steps[local_restore_index:],
        session.cleanup_action,
    )

    def execute(context: RunContext[PrivateJsonValue]) -> _RestoreResult:
        token = activate_auxiliary_restore_session(session)
        try:
            return cast("_RestoreResult", prepared.callback(context))
        finally:
            try:
                session.cleanup(context)
            finally:
                reset_auxiliary_restore_session(token)

    plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in prepared_steps),
        observations=command.plan.observations,
        warnings=command.plan.warnings,
    ).with_fingerprint()
    return cast(
        "_InspectableCommand[_RestoreResult]",
        Command.from_prepared(
            plan,
            prepared_command(
                execute,
                prepared_steps,
                executor=prepared.executor,
                private_projection=prepared.private_projection,
            ),
        ),
    )


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
        command: _InspectableCommand[DatabasePreparationResult] = (
            client.environments.refresh_database_command(
                project_path,
                options=DatabaseRefreshOptions(
                    restore=restore,
                    source_branch=source_branch,
                    reset_admin_password=reset_admin_password,
                ),
            )
        )
        if restore and (project_path / ".odcli" / "project.toml").is_file():
            from odoo_instance_sdk.project import ProjectConfig
            from odoo_instance_sdk.resources.instance import auxiliary_restore_session

            auxiliary_instance = client.instance.from_project(ProjectConfig.load(project_path))
            command = _attach_auxiliary_restore_runtime(
                command,
                auxiliary_restore_session(auxiliary_instance),
            )
    except Exception as exc:
        fail(output_mode, "db.refresh", exc, dry_run=dry_run)

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
            rich=_rich_refresh,
            observer=observer,
            observe_output=show_command_output,
            progress=True,
        )

    try:
        status, _result = run()
    except Exception as exc:
        fail(output_mode, "db.refresh", exc, dry_run=dry_run)
    raise click.exceptions.Exit(status)


@db_group.command("list", aliases=["ls"], help="List databases from the bound PostgreSQL cluster.")
@click.option("--tracked", is_flag=True, default=False, help="Show only proven restore identities.")
@output_options
@field_schema(DatabaseInventoryResult)
@pass_cli_context
def db_list(
    ctx: CliContext,
    tracked: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Read the project PostgreSQL inventory without Odoo reconciliation."""
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        from odoo_instance_sdk.commands.pg import _database_instance
        from odoo_instance_sdk.internal.pg.inventory import build_database_inventory_command

        project_root = resolve_project_path(ctx)
        _environment, instance = _database_instance(ctx)
        command = build_database_inventory_command(instance, project_root, tracked=tracked)
        status, _result = run_or_preview(
            lambda: command,
            command_name="db.list",
            mode=output_mode,
            dry_run=False,
            result=cast(
                "Callable[[DatabaseInventoryResult | None], dict[str, JsonValue]]", model_to_dict
            ),
            context={"tracked": tracked},
            provenance={"project_source": project_provenance(ctx)},
            rich=_list_rich,
        )
    except Exception as exc:
        fail(output_mode, "db.list", exc, dry_run=False)
    raise click.exceptions.Exit(status)


@db_group.command("restore", help="Restore one retained backup into a new database.")
@click.argument("backup_uuid")
@click.option("--target", "target_database", default=None, help="Exact new database name.")
@click.option(
    "--reset-admin-password",
    "reset_admin_password",
    is_flag=True,
    default=False,
    help="Reset base.user_admin after restoring.",
)
@click.option("--yes", is_flag=True, default=False, help="Skip interactive confirmation.")
@click.option(
    "--replace",
    "replace_environment",
    is_flag=True,
    default=False,
    help="Replace the selected stopped COPY environment in place.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def db_restore(  # noqa: C901
    ctx: CliContext,
    backup_uuid: str,
    target_database: str | None,
    reset_admin_password: bool,
    yes: bool,
    replace_environment: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Restore one exact catalogue backup without downloading it again."""
    output_mode = resolve_output_mode(output_format, json_output)
    if replace_environment and target_database is not None:
        raise click.UsageError("--replace cannot be combined with --target")
    if not dry_run and not yes and output_mode is not OutputMode.RICH:
        emit_json_envelope(
            ok=False,
            command="db.restore",
            error_code="confirmation_required",
            error_message="db restore requires --yes in machine output mode",
            mode=output_mode,
        )
        raise click.exceptions.Exit(1)

    try:
        backup_id = uuid.UUID(backup_uuid)
    except (ValueError, TypeError, AttributeError) as exc:
        fail(
            output_mode,
            "db.restore",
            "backup identifier must be a complete UUID",
            dry_run=dry_run,
        )
        raise AssertionError from exc

    try:
        client = _client_class()(config=_client_config_class()(executable="odoo"))
        command: _InspectableCommand[msgspec.Struct]
        if replace_environment:
            from odoo_instance_sdk.internal.database_replacement import (
                build_copy_replacement_command,
            )

            environment = resolve_environment(client, ctx.env, cwd=Path.cwd())
            _validate_replace_context(client, environment)
            command = cast(
                "_InspectableCommand[msgspec.Struct]",
                build_copy_replacement_command(
                    client,
                    environment,
                    backup_id,
                    reset_admin_password=reset_admin_password,
                ),
            )
        else:
            from odoo_instance_sdk.internal.database_preparation import _CatalogueRestoreSource

            project_path = resolve_project_path(ctx)
            command = cast(
                "_InspectableCommand[msgspec.Struct]",
                client.environments.refresh_database_command(
                    project_path,
                    options=DatabaseRefreshOptions(
                        restore=True,
                        reset_admin_password=reset_admin_password,
                    ),
                    restore_source=_CatalogueRestoreSource(backup_id),
                    target_database=target_database,
                ),
            )
            from odoo_instance_sdk.project import ProjectConfig
            from odoo_instance_sdk.resources.instance import auxiliary_restore_session

            if (project_path / ".odcli" / "project.toml").is_file():
                auxiliary_instance = client.instance.from_project(ProjectConfig.load(project_path))
                command = _attach_auxiliary_restore_runtime(
                    command,
                    auxiliary_restore_session(auxiliary_instance),
                )
    except Exception as exc:
        fail(output_mode, "db.restore", exc, dry_run=dry_run)

    def confirm() -> None:
        click.confirm(
            f"Restore backup {backup_id}"
            + (f" into {target_database!r}" if target_database else "")
            + "?",
            default=False,
            abort=True,
        )

    def interrupted(error: KeyboardInterrupt) -> None:
        from odoo_instance_sdk.internal.database_preparation import (
            DatabasePreparationFailureContext,
        )
        from odoo_instance_sdk.internal.database_replacement import CopyReplacementFailureContext

        context = getattr(error, "failure_context", None)
        safe_context: dict[str, JsonValue] = (
            model_to_dict(context)
            if isinstance(
                context, (DatabasePreparationFailureContext, CopyReplacementFailureContext)
            )
            else cast(
                "dict[str, JsonValue]",
                {
                    "backup_id": str(backup_id),
                    "retained_database": target_database,
                    "database_confirmed": False,
                    "default_switch_confirmed": False,
                },
            )
        )
        emit(
            failure_document(
                command="db.restore",
                context=safe_context,
                dry_run=dry_run,
                error_code="db_restore_interrupted",
                error_message="database restore interrupted",
            ),
            output_mode,
        )

    try:
        status, _result = run_or_preview(
            lambda: command,
            command_name="db.restore",
            mode=output_mode,
            dry_run=dry_run,
            result=cast("Callable[[msgspec.Struct | None], dict[str, JsonValue]]", model_to_dict),
            context={
                "backup_id": str(backup_id),
                "target_database": target_database,
                "replace": replace_environment,
            },
            provenance={"project_source": project_provenance(ctx)},
            confirm=None if yes or dry_run else confirm,
            rich=_restore_rich,
            progress=True,
            on_interrupt=interrupted,
        )
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        fail(output_mode, "db.restore", exc, dry_run=dry_run)
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
        fail(output_mode, "db.reset-admin-password", exc, dry_run=dry_run)

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
            rich=_rich_admin_reset,
        )
    except Exception as exc:
        fail(output_mode, "db.reset-admin-password", exc, dry_run=dry_run)
    if not dry_run:
        assert isinstance(result, AdminPasswordResetResult)
    raise click.exceptions.Exit(status)


@db_group.command(
    "drop",
    aliases=["rm"],
    help="Safely drop one database from the project PostgreSQL cluster.",
)
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
        fail(output_mode, "db.drop", exc, dry_run=dry_run)
    raise click.exceptions.Exit(status)


def _drop_rich(document: OutputDocument) -> str:
    payload = document.result if isinstance(document.result, dict) else {}
    if "observations" in payload:
        return _rich_plan_projection(document)
    return f"Dropped database {payload['database']} on {payload['cluster']}"


def _rich_refresh(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    payload = document.result if isinstance(document.result, dict) else {}
    if "steps" in payload:
        return _rich_plan_projection(document)
    table = Table("Field", "Value", title="Database refresh")
    for field in (
        "mode",
        "restored_database",
        "source_git_branch",
        "branch_origin",
        "admin_password_reset",
        "default_switched",
        "previous_default",
        "effective_default",
    ):
        value = payload.get(field)
        if value is not None:
            table.add_row(field.replace("_", " ").title(), rich_cell(value))
    retained = payload.get("retained_artifacts")
    if isinstance(retained, list) and retained:
        table.add_row("Retained artifacts", rich_cell(", ".join(str(item) for item in retained)))
    output = StringIO()
    console = Console(file=output, color_system=None, width=180)
    console.print(table)
    return output.getvalue().rstrip()


def _rich_admin_reset(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    payload = document.result if isinstance(document.result, dict) else {}
    if "steps" in payload:
        return _rich_plan_projection(document)
    table = Table("Field", "Value", title="Administrator password reset")
    for field in ("database", "completed", "xml_id", "environment_id"):
        value = payload.get(field)
        if value is not None:
            table.add_row(field.replace("_", " ").title(), rich_cell(value))
    output = StringIO()
    console = Console(file=output, color_system=None, width=180)
    console.print(table)
    return output.getvalue().rstrip()


def _restore_rich(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    payload = document.result if isinstance(document.result, dict) else {}
    if "steps" in payload:
        return _rich_plan_projection(document)
    database = payload.get("restored_database", "")
    backup = payload.get("backup")
    backup_id = backup.get("id") if isinstance(backup, dict) else backup
    table = Table("Field", "Value", title="Database restore")
    table.add_row("Database", rich_cell(database))
    if backup_id:
        table.add_row("Backup", rich_cell(backup_id))
    output = StringIO()
    console = Console(file=output, color_system=None, width=180)
    console.print(table)
    return output.getvalue().rstrip()


def _list_rich(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    payload = document.result if isinstance(document.result, dict) else {}
    rows = payload.get("databases", [])
    if not isinstance(rows, list) or not rows:
        return "No databases"
    table = Table("Database", "Size", "Sessions", "Default", "Origin")
    for row in rows:
        if not isinstance(row, dict):
            continue
        size = row.get("logical_size_bytes")
        table.add_row(
            rich_cell(row.get("name", "")),
            rich_cell(
                _human_bytes(size) if isinstance(size, int) and not isinstance(size, bool) else "—"
            ),
            rich_cell(row.get("active_sessions", 0)),
            rich_cell(str(row.get("is_default", False)).lower()),
            rich_cell(row.get("origin", "unknown")),
        )
    output = StringIO()
    console = Console(file=output, color_system=None, width=180)
    console.print(rich_cell(f"Cluster: {payload.get('cluster', '—')}"))
    console.print(table)
    return output.getvalue().rstrip()


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
