from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

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
from odoo_instance_sdk.commands.pg import (
    postgres_group as _postgres_group,
)
from odoo_instance_sdk.commands.pg import (
    psql as _psql,
)
from odoo_instance_sdk.commands.pg import (
    register_database_commands,
)
from odoo_instance_sdk.commands.test import (
    project_execution_result,
    resolve_module_test_selection,
    rich_test_result,
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
from odoo_instance_sdk.internal.database_preparation import _planned_project_identity
from odoo_instance_sdk.internal.paths import get_catalog_path
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.project_manifest import manifest_path, write_manifest
from odoo_instance_sdk.internal.server import parse_payload
from odoo_instance_sdk.internal.vscode_generate import (
    build_launch_profile,
    launch_json,
    write_launch_json,
)
from odoo_instance_sdk.internal.vscode_import import import_vscode_launch
from odoo_instance_sdk.models import (
    CommandResult,
    OdooTestSpec,
    PostgresClusterState,
    StartConfig,
)
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


class _ShellCommandFailure(RuntimeError):
    """Carry a classified shell failure into the shared CLI envelope."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        details: JsonObject | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.details = details


def _shell_payload(value: CommandResult, nonce: str) -> dict[str, JsonValue]:
    """Project the framed shell payload without exposing startup logs."""
    from odoo_instance_sdk.internal.proc.redaction import redacted_projection

    payload = parse_payload(value.stdout, nonce=nonce)
    payload = payload or {}
    user_stdout = payload.get("user_stdout", "")
    if not isinstance(user_stdout, str):
        user_stdout = ""
    truncated = payload.get("truncated") is True
    if len(user_stdout) > 32768:
        user_stdout = user_stdout[:32768]
        truncated = True
    return {
        "result": redacted_projection(payload.get("result"), field="result"),
        "user_stdout": redacted_projection(user_stdout, field="user_stdout"),
        "user_error": redacted_projection(payload.get("user_error"), field="error"),
        "truncated": truncated,
    }


def _framed_shell_error(value: CommandResult, nonce: str) -> dict[str, JsonValue] | None:
    """Return details only for a complete, valid framed user-code error."""
    payload = parse_payload(value.stdout, nonce=nonce)
    if payload is None:
        return None
    user_error = payload.get("user_error")
    if (
        "result" not in payload
        or payload["result"] is not None
        or not isinstance(payload.get("user_stdout"), str)
        or not isinstance(payload.get("truncated"), bool)
        or not isinstance(user_error, dict)
        or not isinstance(user_error.get("type"), str)
        or not isinstance(user_error.get("message"), str)
    ):
        return None
    details = _shell_payload(value, nonce)
    if not isinstance(details["user_error"], dict):
        return None
    return details


def _shell_failure(value: CommandResult, command: str, nonce: str) -> _ShellCommandFailure:
    """Classify a non-zero shell result as user-code or startup failure."""
    details = _framed_shell_error(value, nonce)
    if details is not None:
        error = details["user_error"]
        assert isinstance(error, dict)
        error_type = error.get("type", "UserCodeError")
        error_message = error.get("message", "user code failed")
        return _ShellCommandFailure(
            f"{command}_user_code_failed",
            f"{error_type}: {error_message}",
            details=details,
        )
    stderr = value.stderr.strip()
    message = f"shell exited {value.returncode}"
    if stderr:
        message += f": {stderr}"
    return _ShellCommandFailure(f"{command}_startup_failed", message)


def _run_shell_command(
    *,
    command_name: str,
    build_command: Callable[[], Command[CommandResult]],
    mode: OutputMode,
    dry_run: bool,
    project_result: Callable[[CommandResult, dict[str, JsonValue]], JsonObject],
    commit: bool,
) -> int:
    """Run one captured shell leaf with nonce-bound framing and shared output."""
    command: Command[CommandResult] | None = None

    def build() -> Command[CommandResult]:
        nonlocal command
        command = build_command()
        return command

    def checked_result(value: CommandResult | None) -> JsonObject:
        if value is None:
            raise _ShellCommandFailure(
                f"{command_name}_startup_failed",
                f"{command_name} did not return a command result",
            )
        assert command is not None
        nonce = command._private_wrapper_nonce()
        if nonce is None:
            raise _ShellCommandFailure(
                f"{command_name}_startup_failed",
                f"{command_name} wrapper did not provide a nonce-bound frame",
            )
        if parse_payload(value.stdout, nonce=nonce) is None:
            raise _shell_failure(value, command_name, nonce)
        if value.returncode != 0:
            raise _shell_failure(value, command_name, nonce)
        return {**project_result(value, _shell_payload(value, nonce)), "commit": commit}

    status, _ = run_or_preview(
        build,
        command_name=command_name,
        mode=mode,
        dry_run=dry_run,
        result=checked_result,
        rich=_rich_shell_projection,
    )
    return status


def _rich_shell_projection(document: OutputDocument) -> str:
    """Render eval/exec result, user output, and errors as separate sections."""
    if document.ok:
        details = document.result
    elif document.error is not None:
        details = document.error.details
    else:
        details = None
    if not isinstance(details, dict):
        if document.error is not None:
            return document.error.message
        return json.dumps(document.result, ensure_ascii=False, default=str, indent=2)
    result = details.get("result")
    output = details.get("user_stdout", "")
    error = details.get("user_error")
    truncated = details.get("truncated") is True
    lines = [f"Result: {json.dumps(result, ensure_ascii=False, default=str)}"]
    if isinstance(output, str) and output:
        lines.extend(["Output:", output])
    if truncated:
        lines.append("Output: <truncated>")
    if isinstance(error, dict):
        error_type = error.get("type", "Error")
        message = error.get("message", "operation failed")
        lines.append(f"Error: {error_type}: {message}")
        source = error.get("source")
        if isinstance(source, dict) and source.get("text"):
            lines.append(f"Source: {source.get('text')}")
    return "\n".join(lines)


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


@click.rich_config(  # type: ignore[operator]
    {
        "commands_before_options": True,
        "command_groups": {
            "cli": [
                {"name": "Project", "commands": ["init", "doctor"]},
                {"name": "Runtime", "commands": ["run", "shell", "logs", "monitor"]},
                {"name": "Data", "commands": ["env", "db", "postgres", "psql"]},
                {
                    "name": "Development",
                    "commands": [
                        "test",
                        "module",
                        "translations",
                        "deps",
                        "vscode",
                        "eval",
                        "exec",
                    ],
                },
            ]
        },
    }
)
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
    """Manage local Odoo projects, environments, databases, and tooling."""
    ctx.obj = CliContext(project=project, env=env_selector)


cli.add_command(env_group, name="env")
cli.add_command(test_command, name="test")
cli.add_command(db_group, name="db")
cli.add_command(_postgres_group, name="postgres")
register_database_commands(db_group)
cli.add_command(_psql, name="psql")


class _RunCommand(click.RichCommand):  # type: ignore[misc,valid-type]
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        raw_args = tuple(args)
        parsed_args = cast("list[str]", super().parse_args(ctx, args))
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


@cli.command(help="Create or update the project manifest.")
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
        existing, resolved_project, config, no_input, output_mode, dry_run=dry_run
    ):
        return

    status, _ = run_or_preview(
        lambda: action_command(
            "init",
            lambda: _write_initialized_project(
                resolved_project, config, postgres_allocated=postgres_allocated
            ),
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


def _write_initialized_project(
    project_path: Path, config: ProjectConfig, *, postgres_allocated: bool
) -> dict[str, JsonValue]:
    """Write init artifacts, then register the canonical project transactionally."""
    write_manifest(project_path, config)
    _register_initialized_project(project_path)
    return _manifest_dict(config, postgres_allocated=postgres_allocated)


def _register_initialized_project(project_path: Path) -> None:
    """Idempotently register a project after its valid manifest is available."""
    root, common, identity = _planned_project_identity(project_path)
    project_id = f"project_{identity}"
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = BackupCatalog(db_path=get_catalog_path())
    try:
        catalog._register_project(project_id, root, common)
    finally:
        catalog.close()


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
    *,
    dry_run: bool,
) -> bool:
    try:
        existing_cfg = ProjectConfig.load(resolved_project)
    except Exception as e:
        fail(output_mode, "init", f"Existing manifest unreadable: {e}")
    # Comparison excludes ``postgres_allocated`` (dry-run-only flag); both
    # sides default to False here.
    if _manifest_dict(existing_cfg) == _manifest_dict(config):
        if not dry_run:
            _register_initialized_project(resolved_project)
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


@cli.command(help="Diagnose project, runtime, and PostgreSQL.")
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
    short_help="Start resolved Odoo in the foreground.",
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
        runtime_context = cli_context.ready_instance(ctx)
        # Preview must retain the captured plan even when this read-only
        # precondition fails; normal execution keeps the early diagnostic
        # compatibility path in addition to the command-boundary recheck.
        if not dry_run and not runtime_context.check_port_free():
            http_interface, http_port = runtime_context.instance_address()
            fail(
                output_mode,
                "run",
                f"port-conflict: {http_interface}:{http_port} is occupied (ownership unknown)",
            )
        command = runtime_context.instance.run_foreground_command(args=odoo_args)
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "run", e)
    if not dry_run and runtime_context.is_environment:
        runtime_context.client.environments.record_use(runtime_context.require_environment())
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


@cli.command(help="Read or follow retained Odoo logs.")
@click.option("-n", "--tail", type=click.IntRange(min=1), default=100, show_default=True)
@click.option("-f", "--follow", is_flag=True, default=False)
@pass_cli_context
def logs(ctx: CliContext, tail: int, follow: bool) -> None:
    try:
        runtime_context = cli_context.ready_instance(ctx)
        for line in runtime_context.instance.iter_logs(tail=tail, follow=follow):
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


@cli.command(help="Open an interactive Odoo shell.")
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
        runtime_context = cli_context.ready_instance(ctx)
        command = runtime_context.instance.shell_command(args=list(odoo_args))
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


@cli.command("eval", help="Evaluate a Python expression in Odoo.")
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
        runtime_context = cli_context.ready_instance(ctx)
        instance = runtime_context.instance
        status = _run_shell_command(
            command_name="eval",
            build_command=lambda: eval_expression_command(instance, expression, commit=commit),
            mode=output_mode,
            dry_run=dry_run,
            project_result=lambda _value, payload: {**payload, "returncode": 0},
            commit=commit,
        )
    except SystemExit:
        raise
    except _ShellCommandFailure as e:
        fail(output_mode, "eval", e, error_code=e.error_code, details=e.details)
    except Exception as e:
        fail(output_mode, "eval", e)
    sys.exit(status)


@cli.command("exec", help="Execute a Python script in Odoo.")
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
        runtime_context = cli_context.ready_instance(ctx)
        instance = runtime_context.instance
        status = _run_shell_command(
            command_name="exec",
            build_command=lambda: exec_script_command(
                instance, source, argv=tuple(script_args), commit=commit
            ),
            mode=output_mode,
            dry_run=dry_run,
            project_result=lambda value, payload: {
                "returncode": 0,
                "stdout": payload["user_stdout"],
                "stderr": sanitize_diagnostic(value.stderr) if value.stderr else "",
                **payload,
            },
            commit=commit,
        )
    except SystemExit:
        raise
    except _ShellCommandFailure as e:
        fail(output_mode, "exec", e, error_code=e.error_code, details=e.details)
    except Exception as e:
        fail(output_mode, "exec", e)
    sys.exit(status)


@cli.group("module", help="Discover, test, and upgrade Odoo modules.")
def module_group() -> None:
    pass


@module_group.command("list", help="List installed or available Odoo modules.")
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
        runtime_context = cli_context.ready_instance(ctx)
        instance = runtime_context.instance
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


@module_group.command("update", help="Upgrade selected Odoo modules.")
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
        runtime_context = cli_context.ready_instance(ctx)
        runtime_context.runtime
        instance = runtime_context.instance
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.update", str(e))
    try:
        selected_modules = tuple(modules)

        def build_command() -> Command[CommandResult]:
            return update_modules_command(instance, selected_modules)

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


@module_group.command("test", help="Run tests for selected Odoo modules.")
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
        runtime_context = cli_context.ready_instance(ctx)
        runtime = runtime_context.runtime
        instance = runtime_context.instance
        selection = resolve_module_test_selection(
            runtime.root,
            runtime.start_config,
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
                http_interface=runtime.http_interface,
                http_port=runtime.http_port,
            ),
            command_name="module.test",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: (
                project_execution_result(runtime, selection, spec, value[0])
                if value is not None
                else {}
            ),
            rich=lambda document: rich_test_result(cast("dict[str, JsonValue]", document.result)),
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.test", e)
    sys.exit(outcome[0].exit_code if outcome is not None and not dry_run else status)


@cli.group("translations", help="Export Odoo module translations.")
def translations_group() -> None:
    pass


@translations_group.command("export", help="Export selected module translations.")
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
        runtime_context = cli_context.ready_instance(ctx)
        instance = runtime_context.instance
        status, _results = run_or_preview(
            lambda: export_translations_command(
                instance,
                tuple(modules),
                tuple(languages),
                worktree_root=runtime_context.worktree_path(),
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


@cli.group("deps", help="Verify Python and add-on dependencies.")
def deps_group() -> None:
    pass


@deps_group.command("verify", help="Check Python and add-on dependencies.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def deps_verify(
    ctx: CliContext, dry_run: bool, output_format: str | None, json_output: bool
) -> None:
    from odoo_instance_sdk.internal.project_runtime import is_uv_python_selector

    output_mode = resolve_output_mode(output_format, json_output)
    try:
        runtime_context = cli_context.ready_instance(ctx)
        project_python = (
            runtime_context.source.python
            if isinstance(runtime_context.source, ProjectConfig)
            else None
        )
        recorded_python = (
            cast("str", project_python)
            if is_uv_python_selector(project_python)
            else runtime_context.python_path()
        )
        status, _result = run_or_preview(
            lambda: verify_deps_command(
                recorded_python=recorded_python,
                worktree_root=runtime_context.worktree_path(),
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


@cli.group("vscode", help="Generate VS Code launch configuration.")
def vscode_group() -> None:
    pass


@vscode_group.command("generate", help="Generate a VS Code debugpy launch profile.")
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
        runtime_context = cli_context.ready_instance(ctx)
        runtime = runtime_context.runtime

        def operation() -> dict[str, JsonValue]:
            profile = build_launch_profile(runtime)
            if write_file:
                project_path = runtime.repository_root
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
