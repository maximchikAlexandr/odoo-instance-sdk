from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click

else:
    import rich_click as click

from odoo_instance_sdk.commands.context import (
    CliContext,
    pass_cli_context,
)
from odoo_instance_sdk.commands.context import (
    project_provenance as _project_provenance,
)
from odoo_instance_sdk.commands.env.checkout import env_group
from odoo_instance_sdk.commands.env.deps import (
    _client_class,
    _client_config_class,
)
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    emit,
    emit_json_envelope,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
    sanitize_terminal_text,
    success_document,
)
from odoo_instance_sdk.models.backup import DevelopmentEnvironment

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command, JsonValue


def _resolve_project_path(ctx: CliContext) -> Path:
    import odoo_instance_sdk.commands.env as _env_module

    return _env_module.resolve_project_path(ctx)


def _resolve_environment(
    client: OdooClient,
    selector: str | None = None,
    *,
    cwd: Path | None = None,
) -> DevelopmentEnvironment:
    import odoo_instance_sdk.commands.env as _env_module

    return _env_module.resolve_environment(client, selector, cwd=cwd)


def _require_machine_confirmation(output_mode: OutputMode, yes: bool) -> None:
    if output_mode is OutputMode.RICH or yes:
        return
    emit_json_envelope(
        ok=False,
        command="env.remove",
        error_code="confirmation_required",
        error_message="env remove requires --yes in machine output mode",
        mode=output_mode,
    )
    raise click.exceptions.Exit(1)


@env_group.command(
    "rm", aliases=["remove"], help="Remove one or more isolated development environments."
)
@click.argument("environments", nargs=-1, required=False)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Show plan only.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Skip confirmation.")
@output_options
@pass_cli_context
def env_remove(
    ctx: CliContext,
    environments: tuple[str, ...],
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    client = _client_class()(config=_client_config_class()(executable="odoo"))
    if not environments or len(environments) == 1:
        selector: str | None = environments[0] if environments else None
        env_obj = _resolve_single_env(
            ctx, client, selector, output_mode=output_mode, dry_run=dry_run
        )
        try:
            command = client.environments.remove_command(env_obj)

            def confirm_remove() -> None:
                _require_machine_confirmation(output_mode, yes)
                if not yes and not click.confirm(
                    sanitize_terminal_text(f"Remove environment {env_obj.name} ({env_obj.id})?"),
                    default=False,
                ):
                    emit(
                        success_document(command="env.remove", result={"aborted": True}),
                        output_mode,
                        rich=lambda _document: "Aborted.",
                    )
                    raise click.exceptions.Exit(0)  # noqa: TRY301

            status, _removed = run_or_preview(
                lambda: command,
                command_name="env.remove",
                mode=output_mode,
                dry_run=dry_run,
                confirm=confirm_remove,
                result=lambda _value: _env_dict(client.environments.get(str(env_obj.id))),
                context={
                    "environment_id": str(env_obj.id),
                    "worktree_path": env_obj.worktree_path,
                },
                provenance={
                    "project_source": _project_provenance(ctx),
                    "environment_source": "explicit" if selector else "cwd",
                },
                rich=lambda _document: f"Removed environment {env_obj.name} ({env_obj.id})",
            )
            if dry_run:
                return
        except click.exceptions.Exit:
            raise
        except Exception as e:
            fail(output_mode, "env.remove", e, dry_run=dry_run)
        sys.exit(status)
        return
    _env_remove_multi(
        ctx,
        client,
        environments,
        dry_run=dry_run,
        yes=yes,
        output_mode=output_mode,
    )


def _resolve_single_env(
    ctx: CliContext,
    client: OdooClient,
    selector: str | None,
    *,
    output_mode: OutputMode,
    dry_run: bool,
) -> DevelopmentEnvironment:
    if selector is None:
        if ctx.env is not None:
            fail(
                output_mode,
                "env.remove",
                "root --env is not accepted by env remove; pass ENVIRONMENT or cd into its worktree",
                dry_run=dry_run,
                usage=True,
            )
        try:
            return _resolve_environment(client, None)
        except Exception as e:
            fail(output_mode, "env.remove", str(e), dry_run=dry_run)
    try:
        _resolve_project_path(ctx)
        return client.environments.get(selector)
    except Exception as e:
        fail(output_mode, "env.remove", str(e), dry_run=dry_run)


def _env_remove_multi(
    ctx: CliContext,
    client: OdooClient,
    environments: tuple[str, ...],
    *,
    dry_run: bool,
    yes: bool,
    output_mode: OutputMode,
) -> None:
    from collections.abc import Sequence

    from odoo_instance_sdk.commands.multi_target import (
        confirm_all_targets,
        run_multi_target_deletion,
    )
    from odoo_instance_sdk.internal.sanitize import sanitize_last_error

    targets = _resolve_env_targets(client, environments, output_mode=output_mode, dry_run=dry_run)
    if targets is None:
        return

    def build_plan(item: tuple[str, DevelopmentEnvironment]) -> dict[str, JsonValue]:
        _selector, env_obj = item
        command = client.environments.remove_command(env_obj)
        return _plan_to_json(command)

    def execute_target(
        item: tuple[str, DevelopmentEnvironment],
    ) -> tuple[bool, dict[str, JsonValue], str | None]:
        _selector, env_obj = item
        try:
            refreshed = client.environments.get(str(env_obj.id))
            command = client.environments.remove_command(refreshed)
            command.run()
        except Exception as exc:
            return False, {}, sanitize_last_error(str(exc))
        return True, _env_dict(client.environments.get(str(env_obj.id))), None

    def confirm_prompt(items: Sequence[tuple[str, DevelopmentEnvironment]]) -> None:
        confirm_all_targets(
            items,
            target_label=lambda item: f"environment {item[1].name} ({item[1].id})",
            prompt_message=lambda _items: f"Remove {len(items)} environment(s)?",
        )

    try:
        run_multi_target_deletion(
            tuple(targets),
            command="env.remove",
            mode=output_mode,
            dry_run=dry_run,
            yes=yes,
            target_id=lambda item: str(item[1].id),
            target_label=lambda item: f"environment {item[1].name} ({item[1].id})",
            build_plan=build_plan,
            execute_target=execute_target,
            rich_summary=_env_multi_rich,
            provenance={
                "project_source": _project_provenance(ctx),
                "environment_source": "explicit",
            },
            confirm_prompt=confirm_prompt,
        )
    except click.exceptions.Exit:
        raise
    except Exception as e:
        fail(output_mode, "env.remove", e, dry_run=dry_run)


def _resolve_env_targets(
    client: OdooClient,
    environments: tuple[str, ...],
    *,
    output_mode: OutputMode,
    dry_run: bool,
) -> list[tuple[str, DevelopmentEnvironment]] | None:
    seen: set[str] = set()
    ordered: list[str] = []
    for selector in environments:
        if selector in seen:
            fail(
                output_mode,
                "env.remove",
                f"duplicate environment selector {selector!r}",
                dry_run=dry_run,
            )
            return None
        seen.add(selector)
        ordered.append(selector)
    targets: list[tuple[str, DevelopmentEnvironment]] = []
    for selector in ordered:
        try:
            env_obj = client.environments.get(selector)
        except Exception as exc:
            fail(
                output_mode,
                "env.remove",
                f"{selector}: {exc}",
                dry_run=dry_run,
            )
            return None
        targets.append((selector, env_obj))
    return targets


@env_group.command("sync", help="Synchronize an environment's Python dependencies.")
@click.argument("environment", required=False)
@click.option("--upgrade", "upgrade", is_flag=True, default=False)
@click.option(
    "--hash-lock",
    "hash_lock",
    type=click.Path(),
    default=None,
    help="Audited requirements lock for owned hash-locked synchronization.",
)
@click.option(
    "--hash-lock-sha256",
    "hash_lock_sha256",
    default=None,
    help="Expected SHA-256 digest of --hash-lock.",
)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Show plan only.")
@output_options
@pass_cli_context
def env_sync(
    ctx: CliContext,
    environment: str | None,
    upgrade: bool,
    hash_lock: str | None,
    hash_lock_sha256: str | None,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    client = _client_class()(config=_client_config_class()(executable="odoo"))
    if environment is None:
        if ctx.env is not None:
            fail(
                output_mode,
                "env.sync",
                "root --env is not accepted by env sync; pass ENVIRONMENT or cd into its worktree",
                dry_run=dry_run,
                usage=True,
            )
        try:
            environment = str(_resolve_environment(client, None).id)
        except Exception as e:
            fail(output_mode, "env.sync", str(e), dry_run=dry_run)
    try:
        _resolve_project_path(ctx)
        command = client.environments.sync_python_command(
            environment,
            upgrade=upgrade,
            hash_lock=hash_lock,
            hash_lock_sha256=hash_lock_sha256,
        )
    except Exception as e:
        fail(output_mode, "env.sync", str(e), dry_run=dry_run)
    try:
        status, _result = run_or_preview(
            lambda: command,
            command_name="env.sync",
            mode=output_mode,
            dry_run=dry_run,
            result=_env_dict,
            rich=lambda document: (
                f"Synced environment {document.result.get('name')} "
                f"({document.result.get('id')}) state={document.result.get('state')}"
                if isinstance(document.result, dict)
                else ""
            ),
            progress=True,
        )
    except Exception as exc:
        fail(output_mode, "env.sync", exc, dry_run=dry_run)
    raise click.exceptions.Exit(status)


def _env_dict(e: DevelopmentEnvironment | None) -> dict[str, JsonValue]:
    if e is None:
        return {}
    env = e
    return {
        "id": str(env.id),
        "name": env.name,
        "state": str(env.state),
        "branch": env.branch,
        "db_mode": str(env.db_mode),
        "http_port": env.http_port,
        "worktree_path": env.worktree_path,
    }


def _plan_to_json(command: Command[None]) -> JsonObject:
    return model_to_dict(command.plan)


def _env_multi_rich(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    targets = result.get("targets")
    if not isinstance(targets, list):
        return "Removed environments."
    lines: list[str] = []
    for entry in targets:
        if not isinstance(entry, dict):
            continue
        target = entry.get("target", "unknown")
        if document.dry_run:
            lines.append(f"Plan for environment {target}")
        elif entry.get("ok"):
            res = entry.get("result")
            name = target
            if isinstance(res, dict):
                name = res.get("name", target)
            lines.append(f"Removed environment {name} ({target}).")
        else:
            err = entry.get("error", "failed")
            lines.append(f"Failed to remove environment {target}: {err}")
    return "\n".join(lines).rstrip()
