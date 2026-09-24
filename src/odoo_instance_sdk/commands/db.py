"""Database preparation commands at the public CLI boundary."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable, Sequence
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
    import msgspec
else:
    import rich_click as click

from rich.console import Console  # noqa: I001 -- keep database output formatter aliases grouped; remove when Ruff supports grouped aliases.
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
    JsonObject,
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
from odoo_instance_sdk.internal.cli_format import human_bytes as _human_bytes, rich_cell
from odoo_instance_sdk.internal.pg.inventory import DatabaseInventoryResult
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    DatabaseRefreshOptions,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.commands.output import _InspectableCommand
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.pg.drop import DatabaseDropResult
    from odoo_instance_sdk.internal.proc import StepObserver
    from odoo_instance_sdk.models import DatabasePreparationResult, DevelopmentEnvironment
    from odoo_instance_sdk.resources.instance import OdooInstance


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
            from odoo_instance_sdk.resources.instance.auxiliary_restore import (
                _attach_auxiliary_restore_runtime,
            )

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

        project_root = resolve_project_path(ctx)
        _environment, instance = _database_instance(ctx)
        command = instance.databases.list_inventory_command(project_root, tracked=tracked)
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
            environment = resolve_environment(client, ctx.env, cwd=Path.cwd())
            _validate_replace_context(client, environment)
            command = cast(
                "_InspectableCommand[msgspec.Struct]",
                client.environments.replace_copy_database_command(
                    environment,
                    backup_id,
                    reset_admin_password=reset_admin_password,
                ),
            )
        else:
            from odoo_instance_sdk.internal.dbprep.source import _CatalogueRestoreSource

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
            from odoo_instance_sdk.resources.instance.auxiliary_restore import (
                _attach_auxiliary_restore_runtime,
            )

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
        from odoo_instance_sdk.internal.dbprep.source import (
            DatabasePreparationFailureContext,
        )
        from odoo_instance_sdk.internal.dbreplace.planning import CopyReplacementFailureContext

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
    help="Safely drop one or more databases from the project PostgreSQL cluster.",
)
@click.argument("databases", nargs=-1, required=False)
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
    databases: tuple[str, ...],
    force_default: bool,
    force_connections: bool,
    yes: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Safely drop one or more exact databases from the resolved project cluster."""
    output_mode = resolve_output_mode(output_format, json_output)
    if not databases:
        raise click.UsageError("db drop requires at least one DATABASE name")
    if len(databases) == 1:
        database = databases[0]
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
        return
    _db_drop_multi(
        ctx,
        databases,
        force_default=force_default,
        force_connections=force_connections,
        yes=yes,
        dry_run=dry_run,
        output_mode=output_mode,
    )


def _db_drop_multi(
    ctx: CliContext,
    databases: tuple[str, ...],
    *,
    force_default: bool,
    force_connections: bool,
    yes: bool,
    dry_run: bool,
    output_mode: OutputMode,
) -> None:
    from odoo_instance_sdk.commands.multi_target import run_multi_target_deletion
    from odoo_instance_sdk.internal.sanitize import sanitize_last_error

    targets = _dedup_databases(databases)
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
        cluster = getattr(instance, "_postgres_cluster", None)
        cluster_endpoint = getattr(cluster, "endpoint", "bound cluster")
        plans: list[tuple[str, Command[DatabaseDropResult]]] = []
        for database in targets:
            try:
                command = build_database_drop_command(
                    instance,
                    project_root,
                    database,
                    force_default=force_default,
                    force_connections=force_connections,
                )
            except Exception as exc:
                fail(
                    output_mode,
                    "db.drop",
                    f"{database}: {exc}",
                    dry_run=dry_run,
                )
                return
            plans.append((database, command))

        def build_plan(item: tuple[str, Command[DatabaseDropResult]]) -> JsonObject:
            _name, command = item
            return model_to_dict(command.plan)

        def execute_target(
            item: tuple[str, Command[DatabaseDropResult]],
        ) -> tuple[bool, JsonObject, str | None]:
            name, _command = item
            try:
                rebuilt = build_database_drop_command(
                    instance,
                    project_root,
                    name,
                    force_default=force_default,
                    force_connections=force_connections,
                )
                result = rebuilt.run()
            except Exception as exc:
                return False, {}, sanitize_last_error(str(exc))
            return True, model_to_dict(result), None

        def confirm_prompt(items: Sequence[tuple[str, Command[DatabaseDropResult]]]) -> None:
            click.confirm(
                f"Drop {len(items)} database(s) on cluster {cluster_endpoint}?",
                default=False,
                abort=True,
            )

        run_multi_target_deletion(
            tuple(plans),
            command="db.drop",
            mode=output_mode,
            dry_run=dry_run,
            yes=yes,
            target_id=lambda item: item[0],
            target_label=lambda item: f"database {item[0]!r} on {cluster_endpoint}",
            build_plan=build_plan,
            execute_target=execute_target,
            rich_summary=_drop_multi_rich,
            provenance={"project_source": project_provenance(ctx)},
            confirm_prompt=confirm_prompt,
        )
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        fail(output_mode, "db.drop", exc, dry_run=dry_run)


def _dedup_databases(databases: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for database in databases:
        if database in seen:
            raise click.UsageError(f"duplicate database name {database!r}")
        seen.add(database)
        ordered.append(database)
    return tuple(ordered)


def _drop_multi_rich(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    targets = result.get("targets")
    if not isinstance(targets, list):
        return "Dropped databases."
    lines: list[str] = []
    for entry in targets:
        if not isinstance(entry, dict):
            continue
        target = entry.get("target", "unknown")
        if document.dry_run:
            plan = entry.get("plan")
            if isinstance(plan, dict):
                lines.append(_rich_plan_projection(plan, command=document.command))
            else:
                lines.append(f"Plan for {target}")
        elif entry.get("ok"):
            res = entry.get("result")
            if isinstance(res, dict):
                lines.append(
                    f"Dropped database {res.get('database', target)} on {res.get('cluster', 'cluster')}"
                )
            else:
                lines.append(f"Dropped database {target}.")
        else:
            err = entry.get("error", "failed")
            lines.append(f"Failed to drop database {target}: {err}")
    return "\n".join(lines).rstrip()


def _drop_rich(document: OutputDocument) -> str:
    payload = document.result if isinstance(document.result, dict) else {}
    if "observations" in payload:
        return _rich_plan_projection(
            document.result, command=document.command, warnings=document.warnings
        )
    return f"Dropped database {payload['database']} on {payload['cluster']}"


def _rich_refresh(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    payload = document.result if isinstance(document.result, dict) else {}
    if "steps" in payload:
        return _rich_plan_projection(
            document.result, command=document.command, warnings=document.warnings
        )
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
        return _rich_plan_projection(
            document.result, command=document.command, warnings=document.warnings
        )
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
        return _rich_plan_projection(
            document.result, command=document.command, warnings=document.warnings
        )
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
