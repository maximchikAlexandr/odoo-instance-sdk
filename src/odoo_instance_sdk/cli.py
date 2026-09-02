from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.db import db_group
from odoo_instance_sdk.commands.env import env_group
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    action_command,
    command_options,
    emit_json_envelope,
    fail,
    model_to_dict,
    output_options,
    resolve_command_options,
    resolve_output_mode,
    rich_print,
    run_or_preview,
    sanitize_diagnostic,
)
from odoo_instance_sdk.commands.pg import postgres_group as _postgres_group
from odoo_instance_sdk.commands.test import (
    project_execution_result,
    resolve_module_test_selection,
    test_command,
)
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    LogfileAccessError,
    VscodeImportError,
)
from odoo_instance_sdk.internal.automation import (
    ModuleRecord,
    TranslationExportResult,
    eval_expression_command,
    exec_script_command,
    export_translations_command,
    list_modules_command,
    module_records_from_result,
    module_tests_command,
    update_modules_command,
    verify_deps_command,
)
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.project_manifest import manifest_path, write_manifest
from odoo_instance_sdk.internal.server import parse_payload
from odoo_instance_sdk.internal.vscode_generate import (
    build_launch_profile,
    launch_json,
    write_launch_json,
)
from odoo_instance_sdk.internal.vscode_import import import_vscode_launch
from odoo_instance_sdk.models import CommandResult, OdooTestSpec, PostgresClusterState, StartConfig
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig

if TYPE_CHECKING:
    from collections.abc import Callable as TypeCallback

    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.doctor import DoctorReport
    from odoo_instance_sdk.models import ClusterSnapshot
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    type CliLazyExport = (
        type[OdooClient | PostgresCluster | DoctorReport]
        | TypeCallback[[OdooClient, Path | None], DoctorReport]
        | TypeCallback[[PostgresCluster, PostgresClusterState], ClusterSnapshot]
        | TypeCallback[[ClusterSnapshot], int]
        | TypeCallback[[ClusterSnapshot], None]
    )


def __getattr__(name: str) -> CliLazyExport:
    """Resolve operation-only imports when a command callback actually needs them."""
    if name == "OdooClient":
        from odoo_instance_sdk.client import OdooClient

        globals()[name] = OdooClient
        return OdooClient
    if name in {"DoctorReport", "run_doctor"}:
        from odoo_instance_sdk.internal import doctor

        value = getattr(doctor, name)
        globals()[name] = value
        return cast("CliLazyExport", value)
    if name in {
        "cluster_snapshot",
        "emit_postgres_result",
        "print_status",
        "run_postgres_command",
        "status_exit_code",
    }:
        from odoo_instance_sdk.internal import postgres_cli

        value = getattr(postgres_cli, name)
        globals()[name] = value
        return cast("CliLazyExport", value)
    if name == "PostgresCluster":
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        globals()[name] = PostgresCluster
        return PostgresCluster
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _client_class() -> type[OdooClient]:
    return cast("type[OdooClient]", getattr(sys.modules[__name__], "OdooClient"))


def _run_doctor() -> Callable[[OdooClient, Path | None], DoctorReport]:
    return cast(
        "Callable[[OdooClient, Path | None], DoctorReport]",
        getattr(sys.modules[__name__], "run_doctor"),
    )


def _postgres_cluster(ctx: CliContext) -> PostgresCluster:
    """Compatibility wrapper for callers of the pre-module PostgreSQL seam."""
    from odoo_instance_sdk.commands.pg import _postgres_cluster as resolve_cluster

    return resolve_cluster(ctx)


def _cluster_rich(document: OutputDocument) -> str:
    """Compatibility wrapper for the moved PostgreSQL renderer."""
    from odoo_instance_sdk.commands.pg import _cluster_rich as render_cluster

    return render_cluster(document)


def _updated_modules(value: CommandResult | None) -> JsonValue:
    if value is None:
        return []
    payload = parse_payload(value.stdout)
    if not isinstance(payload, dict):
        return []
    nested = payload.get("result")
    if not isinstance(nested, dict):
        return []
    return nested.get("updated", [])


def _module_list_result(value: CommandResult | list[ModuleRecord]) -> JsonObject:
    records = value if isinstance(value, list) else module_records_from_result(value)
    return {"modules": [record.to_dict() for record in records]}


@click.group()
@click.version_option(package_name="odoo-instance-sdk")
@click.option(
    "--project",
    "project",
    type=click.Path(exists=False),
    default=None,
    help="Explicit project path.",
)
@click.option("--env", "env_selector", default=None, help="Environment selector (UUID or name).")
@click.pass_context
def cli(ctx: click.Context, project: str | None, env_selector: str | None) -> None:
    ctx.obj = CliContext(project=project, env=env_selector)


cli.add_command(env_group, name="env")
cli.add_command(test_command, name="test")
cli.add_command(db_group, name="db")
cli.add_command(_postgres_group, name="postgres")


class _RunCommand(click.Command):
    def get_short_help_str(self, limit: int = 45) -> str:
        del limit
        return ""

    def format_help_text(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        del ctx
        if self.help is not None:
            formatter.write_paragraph()
            formatter.write_text(self.help)

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        raw_args = tuple(args)
        parsed_args = super().parse_args(ctx, args)
        odoo_args = tuple(ctx.params.get("odoo_args", ()))
        if odoo_args:
            try:
                delimiter = raw_args.index("--")
            except ValueError as exc:
                raise click.UsageError(
                    "Native Odoo arguments must follow a literal `--` delimiter.", ctx
                ) from exc
            if raw_args[delimiter + 1 :] != odoo_args:
                raise click.UsageError(
                    "Native Odoo arguments must follow a literal `--` delimiter.", ctx
                )
        return parsed_args


@cli.command()
@click.option("--odoo-bin", "odoo_bin", type=click.Path(), default=None, help="Path to odoo-bin.")
@click.option("--python", "python", default=None, help="Python interpreter or uv selector.")
@click.option(
    "--config", "source_config", type=click.Path(), default=None, help="Source odoo.conf path."
)
@click.option(
    "--database", "default_source_database", default=None, help="Default source database name."
)
@click.option(
    "--http-port", "preferred_http_port", type=int, default=None, help="Preferred HTTP port."
)
@click.option("--requirements", "requirements", multiple=True, help="Requirements files.")
@click.option("--run-arg", "run_args", multiple=True, help="Default run args.")
@click.option("--runtime-cwd", "runtime_cwd", type=click.Path(), default=None, help="Runtime cwd.")
@click.option(
    "--from-vscode",
    "from_vscode",
    type=click.Path(exists=False),
    default=None,
    help="Import from VS Code launch.json.",
)
@click.option("--launch-name", "launch_name", default=None, help="VS Code launch profile name.")
@click.option(
    "--postgres",
    "postgres_mode",
    type=click.Choice(["external", "compose"], case_sensitive=False),
    default="external",
    help="PostgreSQL cluster mode (external: reuse source cluster; compose: SDK-owned).",
)
@click.option(
    "--postgres-image",
    "postgres_image",
    default=None,
    help="Compose only; required with --no-input.",
)
@click.option(
    "--postgres-port",
    "postgres_port",
    type=int,
    default=None,
    help="Compose only; omitted = allocate free loopback port.",
)
@click.option(
    "--postgres-user",
    "postgres_user",
    default=None,
    help="Compose only; default: source db_user or 'odoo'.",
)
@click.option("--no-input", "no_input", is_flag=True, default=False, help="Forbid prompts.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Do not write.")
@output_options
@click.option(
    "--project", "project_path", type=click.Path(exists=False), default=None, help="Project path."
)
def init(
    odoo_bin: str | None,
    python: str | None,
    source_config: str | None,
    default_source_database: str | None,
    preferred_http_port: int | None,
    requirements: tuple[str, ...],
    run_args: tuple[str, ...],
    runtime_cwd: str | None,
    from_vscode: str | None,
    launch_name: str | None,
    postgres_mode: str,
    postgres_image: str | None,
    postgres_port: int | None,
    postgres_user: str | None,
    no_input: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
    project_path: str | None,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    resolved_project = Path(project_path) if project_path is not None else Path.cwd()
    provenance: dict[str, list[str]] = {"option": [], "vscode": [], "discovery": [], "default": []}

    option_state = _OptionState(
        odoo_bin=Path(odoo_bin) if odoo_bin else None,
        python=python,
        source_config=Path(source_config) if source_config else None,
        default_source_database=default_source_database,
        preferred_http_port=preferred_http_port,
        requirements=tuple(requirements),
        default_run_args=tuple(run_args),
        runtime_cwd=Path(runtime_cwd) if runtime_cwd else None,
    )
    _record_option_provenance(option_state, provenance)

    if from_vscode is not None:
        vscode_cfg = _import_vscode(from_vscode, launch_name, no_input, output_mode)
        if vscode_cfg is None:
            return
        _merge_vscode(option_state, vscode_cfg, provenance)

    _resolve_odoo_bin(option_state, no_input, output_mode, dry_run, provenance)

    postgres_cfg, postgres_allocated = _resolve_postgres_state(
        postgres_mode=postgres_mode,
        postgres_image=postgres_image,
        postgres_port=postgres_port,
        postgres_user=postgres_user,
        source_config=option_state.source_config,
        no_input=no_input,
        output_mode=output_mode,
        project_path=resolved_project,
    )
    if postgres_cfg is not None:
        provenance["option"].append("postgres")

    config = ProjectConfig(
        repository_root=resolved_project.resolve(),
        odoo_bin=option_state.odoo_bin,
        python=option_state.python,
        source_config=option_state.source_config,
        default_source_database=option_state.default_source_database,
        preferred_http_port=option_state.preferred_http_port,
        requirements=option_state.requirements,
        default_run_args=option_state.default_run_args,
        runtime_cwd=option_state.runtime_cwd,
        postgres=postgres_cfg,
    )

    existing = manifest_path(resolved_project)
    if existing.is_file() and _handle_existing_manifest(
        existing, resolved_project, config, no_input, output_mode
    ):
        return

    status, _ = run_or_preview(
        lambda: action_command(
            "init",
            lambda: (
                write_manifest(resolved_project, config),
                _manifest_dict(config, postgres_allocated=postgres_allocated),
            )[1],
            description="Write project manifest",
            mutating=True,
        ),
        command_name="init",
        mode=output_mode,
        dry_run=dry_run,
        result=lambda value: cast("dict[str, JsonValue]", value),
        provenance=cast("dict[str, JsonValue]", provenance),
        preview=lambda command: {
            **_manifest_dict(config, postgres_allocated=postgres_allocated),
            "plan": model_to_dict(command.plan),
        },
        rich=lambda _document: (
            f"Dry run — no files written.\n{config.to_manifest()}"
            if dry_run
            else f"Wrote {existing}"
        ),
    )
    sys.exit(status)


def _resolve_postgres_state(
    *,
    postgres_mode: str,
    postgres_image: str | None,
    postgres_port: int | None,
    postgres_user: str | None,
    source_config: Path | None,
    no_input: bool,
    output_mode: OutputMode,
    project_path: Path,
) -> tuple[PostgresProjectConfig | None, bool]:
    mode = "compose" if postgres_mode.lower() == "compose" else "external"
    if mode == "external":
        return None, False

    if postgres_image is None:
        if no_input or output_mode is not OutputMode.RICH:
            fail(output_mode, "init", "Missing required option --postgres-image for compose mode")
        postgres_image = click.prompt("PostgreSQL image (e.g. pgvector/pgvector:pg16)")

    allocated = False
    if postgres_port is None:
        postgres_port = find_free_port(
            "postgres", _open_catalog_optional(), exclude_project=project_path
        )
        allocated = True

    if postgres_user is None:
        postgres_user = _default_postgres_user(source_config)

    cfg = PostgresProjectConfig(
        mode="compose",
        image=postgres_image,
        port=postgres_port,
        user=postgres_user,
    )
    return cfg, allocated


def _open_catalog_optional() -> BackupCatalog | None:
    """Open the catalog read-only; return None if missing/unreadable."""
    from odoo_instance_sdk.internal.paths import get_catalog_path
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog_path = get_catalog_path()
    if not catalog_path.is_file():
        return None
    try:
        return BackupCatalog(db_path=catalog_path)
    except Exception:
        return None


def _default_postgres_user(source_config: Path | None) -> str:
    if source_config is not None and source_config.is_file():
        try:
            start_cfg = StartConfig.from_odoo_config(source_config)
            if start_cfg.db_user:
                return start_cfg.db_user
        except Exception:
            pass
    return "odoo"


@dataclass(slots=True)
class _OptionState:
    odoo_bin: Path | None = None
    python: str | Path | None = None
    source_config: Path | None = None
    default_source_database: str | None = None
    preferred_http_port: int | None = None
    requirements: tuple[str, ...] = ()
    default_run_args: tuple[str, ...] = ()
    runtime_cwd: Path | None = None


def _record_option_provenance(state: _OptionState, provenance: dict[str, list[str]]) -> None:
    if state.odoo_bin is not None:
        provenance["option"].append("odoo_bin")
    if state.python is not None:
        provenance["option"].append("python")
    if state.source_config is not None:
        provenance["option"].append("source_config")
    if state.default_source_database is not None:
        provenance["option"].append("default_source_database")
    if state.preferred_http_port is not None:
        provenance["option"].append("preferred_http_port")
    if state.requirements:
        provenance["option"].append("requirements")
    if state.default_run_args:
        provenance["option"].append("default_run_args")
    if state.runtime_cwd is not None:
        provenance["option"].append("runtime_cwd")


def _import_vscode(
    from_vscode: str, launch_name: str | None, no_input: bool, output_mode: OutputMode
) -> ProjectConfig | None:
    try:
        result = import_vscode_launch(from_vscode, launch_name=launch_name, no_input=no_input)
    except VscodeImportError as e:
        fail(output_mode, "init", str(e))
    return result.config


def _merge_vscode(
    state: _OptionState, vscode_cfg: ProjectConfig, provenance: dict[str, list[str]]
) -> None:
    provenance["vscode"].append("imported")
    if state.odoo_bin is None and vscode_cfg.odoo_bin is not None:
        state.odoo_bin = vscode_cfg.odoo_bin
    if state.python is None and vscode_cfg.python is not None:
        state.python = vscode_cfg.python
    if state.source_config is None and vscode_cfg.source_config is not None:
        state.source_config = vscode_cfg.source_config
    if state.default_source_database is None and vscode_cfg.default_source_database is not None:
        state.default_source_database = vscode_cfg.default_source_database
    if state.preferred_http_port is None and vscode_cfg.preferred_http_port is not None:
        state.preferred_http_port = vscode_cfg.preferred_http_port
    if not state.default_run_args and vscode_cfg.default_run_args:
        state.default_run_args = vscode_cfg.default_run_args
    if state.runtime_cwd is None and vscode_cfg.runtime_cwd is not None:
        state.runtime_cwd = vscode_cfg.runtime_cwd


def _handle_existing_manifest(
    existing: Path,
    resolved_project: Path,
    config: ProjectConfig,
    no_input: bool,
    output_mode: OutputMode,
) -> bool:
    try:
        existing_cfg = ProjectConfig.load(resolved_project)
    except Exception as e:
        fail(output_mode, "init", f"Existing manifest unreadable: {e}")
    # Comparison excludes ``postgres_allocated`` (dry-run-only flag); both
    # sides default to False here.
    if _manifest_dict(existing_cfg) == _manifest_dict(config):
        if output_mode is not OutputMode.RICH:
            emit_json_envelope(
                ok=True,
                command="init",
                result=_manifest_dict(config),
                provenance={},
                dry_run=True,
                mode=output_mode,
            )
        else:
            rich_print("Manifest already up to date; no-op.")
        return True
    if no_input or output_mode is not OutputMode.RICH:
        fail(output_mode, "init", "manifest exists and differs; remove it first or adjust options")
    if not click.confirm("Manifest exists and differs; overwrite?", default=False):
        rich_print("Aborted.")
        return True
    return False


def _manifest_dict(
    config: ProjectConfig, *, postgres_allocated: bool = False
) -> dict[str, JsonValue]:
    postgres: dict[str, JsonValue] | None = None
    if config.postgres is not None:
        postgres = {
            "mode": config.postgres.mode,
            "image": config.postgres.image,
            "port": config.postgres.port,
            "user": config.postgres.user,
            "allocated_port": postgres_allocated,
        }
    test_instance: dict[str, JsonValue] | None = None
    if config.test_instance is not None:
        test_instance = {
            "base_url": config.test_instance.base_url,
            "database": config.test_instance.database,
            "git_branch": config.test_instance.git_branch,
        }
    return {
        "odoo_bin": str(config.odoo_bin) if config.odoo_bin else None,
        "python": str(config.python) if config.python else None,
        "source_config": str(config.source_config) if config.source_config else None,
        "default_source_database": config.default_source_database,
        "default_base_ref": config.default_base_ref,
        "refresh_after_hours": config.refresh_after_hours,
        "test_instance": test_instance,
        "preferred_http_port": config.preferred_http_port,
        "requirements": list(config.requirements),
        "default_run_args": list(config.default_run_args),
        "runtime_cwd": str(config.runtime_cwd) if config.runtime_cwd else None,
        "postgres": postgres,
    }


@cli.command()
@output_options
@pass_cli_context
def doctor(ctx: CliContext, output_format: str | None, json_output: bool) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        project_path = cli_context.resolve_project_path(ctx)
        client = _client_class()(config=OdooClientConfig(executable="odoo"))
        report = _run_doctor()(client, project_path if project_path != Path.cwd() else None)
    except Exception as e:
        fail(output_mode, "doctor", str(e))
    if json_output:
        emit_json_envelope(
            ok=report.ok,
            command="doctor",
            context=cast("JsonObject", report.context),
            result={
                "checks": [
                    {
                        "name": c.name,
                        "status": c.status,
                        "detail": sanitize_diagnostic(c.detail),
                        "environment_id": c.environment_id,
                        "environment_name": c.environment_name,
                    }
                    for c in report.checks
                ]
            },
            error_code="doctor_failed" if not report.ok else None,
            error_message="doctor reported failed checks" if not report.ok else None,
            mode=output_mode,
        )
    else:
        _print_doctor(report)
    sys.exit(0 if report.ok else 1)


def _print_doctor(report: DoctorReport) -> None:
    current_env: str | None = None
    for c in report.checks:
        if c.environment_id and c.environment_id != current_env:
            current_env = c.environment_id
            rich_print("")
            rich_print(f"[{current_env}] {c.environment_name or ''}")
        marker = {"ok": "OK", "warn": "WARN", "error": "ERROR", "info": "INFO"}.get(
            c.status, c.status
        )
        rich_print(f"  {marker:<5} {c.name}: {sanitize_diagnostic(c.detail)}")


@cli.command(
    cls=_RunCommand,
    help="Native Odoo arguments must follow a literal `--` delimiter.",
)
@click.argument("odoo_args", nargs=-1, type=click.UNPROCESSED)
@command_options
@pass_cli_context
def run(
    ctx: CliContext,
    odoo_args: tuple[str, ...],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_command_options(output_format, json_output, dry_run, command="run")
    try:
        client, env_obj, instance = cli_context.ready_instance(ctx)
        if not cli_context._check_port_free(env_obj):
            fail(
                output_mode,
                "run",
                f"port-conflict: {env_obj.http_interface}:{env_obj.http_port} is occupied "
                "(ownership unknown)",
            )
        command = instance.run_foreground_command(args=odoo_args)
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "run", e)
    if not dry_run:
        client.environments.record_use(env_obj)
    try:
        _status, value = run_or_preview(
            lambda: command,
            command_name="run",
            mode=output_mode,
            dry_run=dry_run,
            emit_normal=False,
        )
    except KeyboardInterrupt:
        value = 130
    except Exception as e:
        fail(output_mode, "run", e)
    if not dry_run:
        sys.exit(int(value or 0))
    return


@cli.command()
@click.option("-n", "--tail", type=click.IntRange(min=1), default=100, show_default=True)
@click.option("-f", "--follow", is_flag=True, default=False)
@pass_cli_context
def logs(ctx: CliContext, tail: int, follow: bool) -> None:
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)
        for line in instance.iter_logs(tail=tail, follow=follow):
            sys.stdout.write(line)
            sys.stdout.flush()
    except KeyboardInterrupt:
        sys.exit(130)
    except LogfileAccessError as e:
        fail(False, "logs", str(e))
    except InstanceConfigurationError as e:
        fail(False, "logs", str(e))
    except Exception as e:
        fail(False, "logs", str(e))


@cli.command()
@click.argument("odoo_args", nargs=-1, type=click.UNPROCESSED)
@command_options
@pass_cli_context
def shell(
    ctx: CliContext,
    odoo_args: tuple[str, ...],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_command_options(output_format, json_output, dry_run, command="shell")
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)
        command = instance.shell_command(args=list(odoo_args))
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "shell", e)
    try:
        _status, value = run_or_preview(
            lambda: command,
            command_name="shell",
            mode=output_mode,
            dry_run=dry_run,
            emit_normal=False,
        )
    except KeyboardInterrupt:
        value = 130
    except Exception as e:
        fail(output_mode, "shell", e)
    if not dry_run:
        sys.exit(int(value or 0))
    return


@cli.command("eval")
@click.argument("expression")
@click.option(
    "--commit", "commit", is_flag=True, default=False, help="Commit after eval (best-effort)."
)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def eval_cmd(
    ctx: CliContext,
    expression: str,
    commit: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)

        def checked_result(value: CommandResult | None) -> dict[str, JsonValue]:
            if value is None:
                return {}
            returncode = value.returncode
            if returncode != 0:
                raise RuntimeError(  # noqa: TRY301
                    f"shell exited {returncode}: {value.stderr.strip()}"
                )
            payload = parse_payload(value.stdout)
            payload = payload or {}
            return {"result": payload.get("result"), "commit": commit}

        def build_command() -> Command[CommandResult]:
            return eval_expression_command(instance, expression, commit=commit)

        status, _outcome = run_or_preview(
            build_command,
            command_name="eval",
            mode=output_mode,
            dry_run=dry_run,
            result=checked_result,
            rich=lambda document: json.dumps(document.result, default=str, indent=2),
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "eval", e)
    sys.exit(status)


@cli.command("exec")
@click.argument("script")
@click.argument("script_args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--commit", "commit", is_flag=True, default=False, help="Commit after exec (best-effort)."
)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def exec_cmd(
    ctx: CliContext,
    script: str,
    script_args: tuple[str, ...],
    commit: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    if script == "-":
        source = sys.stdin.read()
    else:
        p = Path(script)
        if not p.is_file():
            fail(output_mode, "exec", f"script not found: {script}")
        try:
            source = p.read_text(encoding="utf-8")
        except OSError as e:
            fail(output_mode, "exec", f"cannot read script: {e}")
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)

        def checked_result(value: CommandResult | None) -> dict[str, JsonValue]:
            if value is None:
                return {}
            returncode = value.returncode
            if returncode != 0:
                raise RuntimeError(  # noqa: TRY301
                    f"shell exited {returncode}: {value.stderr.strip()}"
                )
            return {
                "returncode": returncode,
                "stdout": value.stdout,
                "stderr": sanitize_diagnostic(value.stderr) if value.stderr else "",
                "commit": commit,
            }

        def build_command() -> Command[CommandResult]:
            return exec_script_command(instance, source, argv=tuple(script_args), commit=commit)

        status, _outcome = run_or_preview(
            build_command,
            command_name="exec",
            mode=output_mode,
            dry_run=dry_run,
            result=checked_result,
            rich=lambda document: (
                str(document.result.get("stdout", "")) if isinstance(document.result, dict) else ""
            ),
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "exec", e)
    sys.exit(status)


@cli.group("module")
def module_group() -> None:
    pass


@module_group.command("list")
@click.argument("modules", nargs=-1)
@click.option("--state", "state", default=None, help="Filter by state.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
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
        _client, _env, instance = cli_context.ready_instance(ctx)
        status, _records = run_or_preview(
            lambda: list_modules_command(instance, names=tuple(modules), state=state),
            command_name="module.list",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: (
                _module_list_result(cast("CommandResult | list[ModuleRecord]", value))
                if value is not None
                else {"modules": []}
            ),
            rich=lambda document: "\n".join(
                ["NAME                            STATE           VERSION"]
                + [
                    f"{record['name']:<30} {record['state']:<15} "
                    f"{record.get('installed_version') or record.get('latest_version') or ''}"
                    for record in cast(
                        "list[dict[str, JsonValue]]",
                        document.result.get("modules", [])
                        if isinstance(document.result, dict)
                        else [],
                    )
                ]
            ),
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.list", e)
    sys.exit(status)


@module_group.command("update")
@click.argument("modules", nargs=-1, required=True)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Plan only.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Confirm execution.")
@output_options
@pass_cli_context
def module_update(
    ctx: CliContext,
    modules: tuple[str, ...],
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        _client, env_obj, instance = cli_context.ready_instance(ctx)
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.update", str(e))
    try:
        selected_modules = tuple(modules)

        def build_command() -> Command[CommandResult]:
            return update_modules_command(instance, selected_modules, env_id=str(env_obj.id))

        status, _outcome = run_or_preview(
            build_command,
            command_name="module.update",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: {
                "modules": list(selected_modules),
                "updated": _updated_modules(value),
            },
            confirm=(
                (lambda: fail(output_mode, "module.update", "module update requires --yes"))
                if not yes
                else None
            ),
            preview=lambda command: {
                "modules": list(selected_modules),
                "plan": model_to_dict(command.plan),
                "dry_run": True,
            },
            rich=lambda document: "\n".join(
                [
                    "Dry run — modules to update:" if dry_run else "Updated modules:",
                    *[
                        f"  {module}"
                        for module in (
                            selected_modules
                            if dry_run
                            else cast(
                                "list[str]",
                                document.result.get("updated", [])
                                if isinstance(document.result, dict)
                                else [],
                            )
                        )
                    ],
                ]
            ),
        )
    except Exception as exc:
        fail(output_mode, "module.update", exc)
    sys.exit(status)


@module_group.command("test")
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
        _client, env_obj, instance = cli_context.ready_instance(ctx)
        start_config = instance.config.start_config
        if start_config is None:
            raise RuntimeError(  # noqa: TRY301
                "selected environment has no generated Odoo config"
            )
        selection = resolve_module_test_selection(
            env_obj.worktree_path,
            start_config,
            tuple(modules),
            test_tags,
        )
        spec = OdooTestSpec(
            modules=tuple(sorted(set(modules))),
            test_tags=test_tags,
            reload_tests=reload_tests,
            allow_empty=allow_empty,
        )
        status, outcome = run_or_preview(
            lambda: module_tests_command(
                instance,
                spec,
                http_interface=env_obj.http_interface,
                http_port=env_obj.http_port,
            ),
            command_name="module.test",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: (
                project_execution_result(env_obj, selection, spec, value[0])
                if value is not None
                else {}
            ),
            rich=lambda document: json.dumps(document.result, default=str, indent=2),
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.test", e)
    sys.exit(outcome[0].exit_code if outcome is not None and not dry_run else status)


@cli.group("translations")
def translations_group() -> None:
    pass


@translations_group.command("export")
@click.option("--module", "modules", multiple=True, required=True, help="Module name.")
@click.option("--language", "languages", multiple=True, required=True, help="Language code.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def translations_export(
    ctx: CliContext,
    modules: tuple[str, ...],
    languages: tuple[str, ...],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        _client, env_obj, instance = cli_context.ready_instance(ctx)
        status, _results = run_or_preview(
            lambda: export_translations_command(
                instance,
                tuple(modules),
                tuple(languages),
                worktree_root=Path(env_obj.worktree_path),
            ),
            command_name="translations.export",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: {
                "exports": [
                    {
                        "module": item.module,
                        "requested_lang": item.requested_lang,
                        "actual_filename": item.actual_filename,
                        "path": str(item.path),
                        "bytes_written": item.bytes_written,
                    }
                    for item in cast("list[TranslationExportResult]", value or [])
                ]
            },
            rich=lambda document: "\n".join(
                f"{item['module']} {item['requested_lang']} -> {item['actual_filename']} "
                f"({item['bytes_written']} bytes at {item['path']})"
                for item in cast(
                    "list[dict[str, JsonValue]]",
                    document.result.get("exports", []) if isinstance(document.result, dict) else [],
                )
            ),
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "translations.export", e)
    sys.exit(status)


@cli.group("deps")
def deps_group() -> None:
    pass


@deps_group.command("verify")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def deps_verify(
    ctx: CliContext, dry_run: bool, output_format: str | None, json_output: bool
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        _client, env_obj, _instance = cli_context.ready_instance(ctx)
        recorded_python = Path(env_obj.python_environment_path)
        if recorded_python.is_dir():
            recorded_python = recorded_python / "bin" / "python"
        status, _result = run_or_preview(
            lambda: verify_deps_command(
                recorded_python=recorded_python,
                worktree_root=Path(env_obj.worktree_path),
            ),
            command_name="deps.verify",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: {
                "distributions": cast("list[JsonValue]", list(value.distributions))
                if value is not None
                else [],
                "missing_imports": cast("list[JsonValue]", list(value.missing_imports))
                if value is not None
                else [],
                "pip_check_ok": value.pip_check_ok if value is not None else True,
                "pip_check_output": value.pip_check_output if value is not None else "",
            },
            rich=lambda document: (
                "pip check: ok"
                if isinstance(document.result, dict) and document.result.get("pip_check_ok")
                else "pip check: issues"
            ),
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "deps.verify", e)
    sys.exit(1 if _result is not None and getattr(_result, "missing_imports", []) else status)


@cli.group("vscode")
def vscode_group() -> None:
    pass


@vscode_group.command("generate")
@click.option(
    "--write", "write_file", is_flag=True, default=False, help="Write .vscode/launch.json."
)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def vscode_generate(
    ctx: CliContext,
    write_file: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        client, env_obj, _instance = cli_context.ready_instance(ctx)

        def operation() -> dict[str, JsonValue]:
            profile = build_launch_profile(client, env_obj)
            if write_file:
                project_path = cli_context.resolve_project_path(ctx)
                written = write_launch_json(project_path, launch_json(profile))
                return {"profile": profile, "written": str(written)}
            return {"profile": profile}

        status, _result = run_or_preview(
            lambda: action_command(
                "vscode.generate",
                operation,
                description="Generate VS Code launch configuration",
                mutating=write_file,
            ),
            command_name="vscode.generate",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: cast("dict[str, JsonValue]", value or {}),
            rich=lambda document: (
                f"Wrote {document.result['written']}"
                if isinstance(document.result, dict) and "written" in document.result
                else launch_json(cast("dict[str, JsonValue]", document.result.get("profile", {})))
                if isinstance(document.result, dict)
                else ""
            ),
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "vscode.generate", e)
    sys.exit(status)


postgres_group = _postgres_group


@cli.command("monitor")
@click.option("--headless", is_flag=True, default=False, help="Serve API only, no UI/browser.")
@click.option(
    "--host", default="127.0.0.1", help="Loopback bind address (127.0.0.1, localhost, or ::1)."
)
@click.option(
    "--port", type=int, default=None, help="Exact port (else auto-select 8069 or 8100-8120)."
)
@click.option("--no-open", is_flag=True, default=False, help="Do not open a browser.")
@click.pass_context
def monitor_cmd(
    ctx: click.Context, headless: bool, host: str, port: int | None, no_open: bool
) -> None:
    """Start the observability monitor (FastAPI + React UI)."""
    from odoo_instance_sdk.internal.serve import run_server

    # run_server raises SystemExit with an actionable hint if the dashboard
    # extra (fastapi/uvicorn) is missing; that propagates as exit 1.
    run_server(host=host, port=port, headless=headless, no_open=no_open)


def _resolve_odoo_bin(
    option_state: _OptionState,
    no_input: bool,
    output_mode: OutputMode,
    dry_run: bool,
    provenance: dict[str, list[str]],
) -> None:
    if option_state.odoo_bin is None:
        if no_input or output_mode is not OutputMode.RICH or dry_run:
            fail(output_mode, "init", "Missing required option --odoo-bin")
        option_state.odoo_bin = Path(click.prompt("Path to odoo-bin"))
        provenance["discovery"].append("odoo_bin")
    if not option_state.odoo_bin:
        fail(output_mode, "init", "odoo_bin is required")


if __name__ == "__main__":
    cli()
