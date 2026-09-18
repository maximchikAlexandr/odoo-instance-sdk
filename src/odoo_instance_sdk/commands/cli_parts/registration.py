from __future__ import annotations

import json
import sys
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
from odoo_instance_sdk.commands.backup import (  # noqa: I001
    backup_group,
    configure_catalog_path_provider,
)
from odoo_instance_sdk.commands.context import CliContext
from odoo_instance_sdk.commands.db import db_group
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.commands.pg import (
    postgres_group as _postgres_group,
    psql as _psql,
    register_database_commands,
)
from odoo_instance_sdk.commands.resource import (
    configure_catalog_path_provider as configure_resource_catalog_path_provider,
    resource_group,
)
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    VscodeImportError,
)
from odoo_instance_sdk.internal.generated_config import project_generated_config_path
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.project_init import (
    manifest_dict as _manifest_dict,
    validate_generated_config_target as _validate_generated_config_target,
)
from odoo_instance_sdk.internal.project_manifest import manifest_path
from odoo_instance_sdk.internal.server import parse_payload
from odoo_instance_sdk.internal.vscode_import import import_vscode_launch
from odoo_instance_sdk.models import (
    CommandResult,
    StartConfig,
)
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


type _ClickCallback = (
    Callable[[CliContext, bool, bool, float, str | None, bool], None]
    | Callable[
        [
            CliContext,
            str | None,
            str | None,
            bool,
            bool,
            bool,
            str | None,
            bool,
            str | None,
            bool,
        ],
        None,
    ]
)


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


def _shell_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Project the framed shell payload without exposing startup logs."""
    from odoo_instance_sdk.internal.proc.redaction import redacted_projection

    user_stdout = payload.get("user_stdout", "")
    if not isinstance(user_stdout, str):
        user_stdout = ""
    truncated = payload.get("truncated") is True
    if len(user_stdout) > 32768:
        user_stdout = user_stdout[:32768]
        truncated = True
    projected: dict[str, JsonValue] = {
        "result": redacted_projection(payload.get("result"), field="result"),
        "user_stdout": redacted_projection(user_stdout, field="user_stdout"),
        "user_error": redacted_projection(payload.get("user_error"), field="error"),
        "truncated": truncated,
    }
    if "transaction" in payload:
        projected["transaction"] = redacted_projection(
            payload.get("transaction"), field="transaction"
        )
    if "finalization_error" in payload:
        projected["finalization_error"] = redacted_projection(
            payload.get("finalization_error"), field="finalization_error"
        )
    return projected


def _valid_shell_error(value: JsonValue) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("type"), str)
        and isinstance(value.get("message"), str)
    )


def _framed_shell_error(payload: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
    """Return details only for a complete, valid framed shell failure."""
    if payload is None:
        return None
    user_error = payload.get("user_error")
    finalization_error = payload.get("finalization_error")
    has_user_error = _valid_shell_error(user_error)
    has_finalization_error = _valid_shell_error(finalization_error)
    if (
        "result" not in payload
        or not isinstance(payload.get("user_stdout"), str)
        or not isinstance(payload.get("truncated"), bool)
        or (user_error is not None and not _valid_shell_error(user_error))
        or (finalization_error is not None and not _valid_shell_error(finalization_error))
        or (not has_user_error and not has_finalization_error)
        # A user-code failure never exposes a partially assigned result.  A
        # finalization failure follows a successful body, so its result is
        # retained as useful diagnostic context.
        or (has_user_error and payload["result"] is not None)
    ):
        return None
    return _shell_payload(payload)


def _shell_failure(
    value: CommandResult,
    command: str,
    payload: dict[str, JsonValue] | None,
) -> _ShellCommandFailure:
    """Classify a non-zero shell result as user or finalization failure."""
    details = _framed_shell_error(payload)
    if details is not None:
        user_error = details.get("user_error")
        finalization_error = details.get("finalization_error")
        if isinstance(user_error, dict):
            error_type = user_error.get("type", "UserCodeError")
            error_message = user_error.get("message", "user code failed")
            error_code = f"{command}_user_code_failed"
        elif isinstance(finalization_error, dict):
            error_type = finalization_error.get("type", "TransactionFinalizationError")
            error_message = finalization_error.get("message", "transaction finalization failed")
            error_code = f"{command}_transaction_finalization_failed"
        else:
            return _ShellCommandFailure(
                f"{command}_startup_failed", f"shell exited {value.returncode}"
            )
        return _ShellCommandFailure(
            error_code,
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
    command = build_command()

    def checked_result(value: CommandResult | None) -> JsonObject:
        if value is None:
            raise _ShellCommandFailure(
                f"{command_name}_startup_failed",
                f"{command_name} did not return a command result",
            )
        nonce = command._private_wrapper_nonce()
        if nonce is None:
            raise _ShellCommandFailure(
                f"{command_name}_startup_failed",
                f"{command_name} wrapper did not provide a nonce-bound frame",
            )
        payload = parse_payload(value.stdout, nonce=nonce)
        if value.returncode != 0:
            raise _shell_failure(value, command_name, payload)
        if payload is None:
            raise _shell_failure(value, command_name, None)
        return {**project_result(value, _shell_payload(payload)), "commit": commit}

    status, _ = run_or_preview(
        lambda: command,
        command_name=command_name,
        mode=mode,
        dry_run=dry_run,
        result=checked_result,
        rich=_rich_shell_projection,
        progress=True,
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
    finalization_error = details.get("finalization_error")
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
    elif isinstance(finalization_error, dict):
        error_type = finalization_error.get("type", "TransactionFinalizationError")
        message = finalization_error.get("message", "transaction finalization failed")
        lines.append(f"Finalization error: {error_type}: {message}")
    return "\n".join(lines)


def _postgres_cluster(ctx: CliContext) -> PostgresCluster:
    """Compatibility wrapper for callers of the pre-module PostgreSQL seam."""
    from odoo_instance_sdk.commands.pg import _postgres_cluster as resolve_cluster

    return resolve_cluster(ctx)


def _cluster_rich(document: OutputDocument) -> str:
    """Compatibility wrapper for the moved PostgreSQL renderer."""
    from odoo_instance_sdk.commands.pg import _cluster_rich as render_cluster

    return render_cluster(document)


class _LazyGroup(click.RichGroup):  # type: ignore[misc,valid-type]
    """Load a command group only after metadata-only CLI startup."""

    def __init__(self, *, name: str, help: str, loader: Callable[[], click.Group]) -> None:
        self._initializing = True
        self._loaded_group: click.Group | None = None
        self._loader = loader
        self._lazy_commands: MutableMapping[str, click.Command] = {}
        super().__init__(name=name, help=help)
        self._initializing = False

    @property
    def commands(self) -> MutableMapping[str, click.Command]:
        if self._initializing:
            return self._lazy_commands
        if self._loaded_group is None:
            self._loaded_group = self._loader()
            self._lazy_commands = self._loaded_group.commands
        return self._lazy_commands

    @commands.setter
    def commands(self, value: MutableMapping[str, click.Command]) -> None:
        self._lazy_commands = value

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(self.commands)

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        command = self.commands.get(name)
        if command is not None:
            return command
        return next(
            (
                candidate
                for candidate in self.commands.values()
                if name in cast("list[str]", getattr(candidate, "aliases", []))
            ),
            None,
        )


class _LazyCommand(click.RichCommand):  # type: ignore[misc,valid-type]
    """Load a concrete command only when the command is selected."""

    def __init__(self, *, name: str, help: str, loader: Callable[[], click.Command]) -> None:
        self._lazy_callback: _ClickCallback | None = None
        self._loader = loader
        super().__init__(name=name, help=help)

    @property
    def callback(self) -> _ClickCallback | None:
        if "odoo_instance_sdk.commands.cli_parts.callbacks" not in sys.modules:
            return None
        if self._lazy_callback is None:
            self._lazy_callback = cast("_ClickCallback | None", self._loader().callback)
        return self._lazy_callback

    @callback.setter
    def callback(self, value: _ClickCallback | None) -> None:
        self._lazy_callback = value

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        command = self._loader()
        self.params = command.params
        self.callback = command.callback
        return command.parse_args(ctx, args)

    def invoke(self, ctx: click.Context) -> None:
        self._loader().invoke(ctx)

    def get_help(self, ctx: click.Context) -> str:
        return self._loader().get_help(ctx)


def _load_env_group() -> click.Group:
    import odoo_instance_sdk.commands.env.list as _env_list_commands  # noqa: F401
    from odoo_instance_sdk.commands.env import env_group

    return env_group


def _load_git_group() -> click.Group:
    from odoo_instance_sdk.commands.git import git_group

    return git_group


def _load_ps_command() -> click.Command:
    from odoo_instance_sdk.commands.ps import ps_command

    return ps_command


def _load_test_command() -> click.Command:
    from odoo_instance_sdk.commands.test import test_command

    return test_command


def _lazy_command(*, name: str, help: str, loader: Callable[[], click.Command]) -> click.Command:
    return _LazyCommand(name=name, help=help, loader=loader)


@click.rich_config(  # type: ignore[operator]
    {
        "commands_before_options": True,
        "color_system": None,
        "force_terminal": False,
        "command_groups": {
            "cli": [
                {"name": "Project", "commands": ["init", "doctor"]},
                {"name": "Runtime", "commands": ["run", "shell", "logs", "monitor"]},
                {"name": "Data", "commands": ["env", "backup", "db", "postgres", "psql"]},
                {"name": "Maintenance", "commands": ["resource"]},
                {
                    "name": "Development",
                    "commands": [
                        "test",
                        "module",
                        "git",
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


cli.add_command(
    _LazyGroup(
        name="env",
        help="Manage isolated development environments.",
        loader=_load_env_group,
    ),
    name="env",
)
cli.add_command(
    _lazy_command(name="test", help="Select and run Odoo tests.", loader=_load_test_command),
    name="test",
)
cli.add_command(db_group, name="db")
cli.add_command(backup_group, name="backup")
cli.add_command(_postgres_group, name="postgres")
register_database_commands(db_group)
cli.add_command(_psql, name="psql")
cli.add_command(resource_group, name="resource")
cli.add_command(
    _lazy_command(
        name="ps",
        help="Show one read-only process and resource inventory from a single snapshot.",
        loader=_load_ps_command,
    ),
    name="ps",
)

_rich_command = cast("Callable[..., click.Command]", click.RichCommand)
_rich_group = cast("Callable[..., click.Group]", click.RichGroup)

for _name, _help in {
    "stop": "Stop the selected environment's proven-owned runtime.",
    "run": "Start resolved Odoo in the foreground or detached.",
    "logs": "Read or follow retained Odoo logs.",
    "shell": "Open an interactive Odoo shell.",
    "monitor": "Start the observability monitor (FastAPI + React UI).",
    "eval": "Evaluate a Python expression in Odoo.",
    "exec": "Execute a Python script in Odoo.",
}.items():
    cli.add_command(_rich_command(name=_name, help=_help), name=_name)
for _name, _help in {
    "deps": "Verify Python and add-on dependencies.",
    "vscode": "Generate VS Code launch configuration.",
    "module": "Discover, test, and upgrade Odoo modules.",
    "translations": "Export Odoo module translations.",
}.items():
    cli.add_command(_rich_group(name=_name, help=_help), name=_name)

_callbacks_loaded = False
_original_get_command = cli.get_command


def _ensure_callbacks_loaded() -> None:
    if _callbacks_loaded:
        return
    import odoo_instance_sdk.commands.cli_parts.callbacks as _callbacks

    del _callbacks
    globals()["_callbacks_loaded"] = True


def _lazy_get_command(ctx: click.Context, name: str) -> click.Command | None:
    if not ctx.resilient_parsing and (ctx.parent is not None or ctx.params or ctx._protected_args):
        _ensure_callbacks_loaded()
    return cast("click.Command | None", _original_get_command(ctx, name))


cli.get_command = _lazy_get_command


cli.add_command(
    _LazyGroup(
        name="git",
        help="Generate and safely synchronize Odoo Git workflows.",
        loader=_load_git_group,
    ),
    name="git",
)


def _cli_catalog_path(*, ensure_exists: bool = True) -> Path:
    from odoo_instance_sdk.internal.paths import get_catalog_path

    return get_catalog_path(ensure_exists=ensure_exists)


def _backup_catalog_path() -> Path:
    return _cli_catalog_path()


def _resource_catalog_path(*, ensure_exists: bool) -> Path:
    return _cli_catalog_path(ensure_exists=ensure_exists)


configure_catalog_path_provider(_backup_catalog_path)
configure_resource_catalog_path_provider(lambda: _resource_catalog_path(ensure_exists=False))


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
@click.option("--yes", "yes", is_flag=True, default=False, help="Confirm manifest replacement.")
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
    yes: bool,
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
        vscode_cfg = _import_vscode(from_vscode, launch_name, no_input, output_mode, dry_run)
        if vscode_cfg is None:
            return
        _merge_vscode(option_state, vscode_cfg, provenance)

    from odoo_instance_sdk.commands.cli_parts.callbacks import _resolve_odoo_bin

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
        dry_run=dry_run,
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
        ticket_link_enabled=False,
    )

    if config.postgres is not None and config.postgres.mode == "compose":
        try:
            _validate_generated_config_target(
                project_generated_config_path(resolved_project), project_root=resolved_project
            )
        except InstanceConfigurationError as exc:
            fail(output_mode, "init", str(exc), dry_run=dry_run)

    from odoo_instance_sdk.commands.cli_parts.callbacks import _handle_existing_manifest

    existing = manifest_path(resolved_project)
    if existing.is_file() and _handle_existing_manifest(
        existing, resolved_project, config, no_input, yes, output_mode, dry_run=dry_run
    ):
        return
    from odoo_instance_sdk.project_init import init_project_command

    status, _ = run_or_preview(
        lambda: init_project_command(
            resolved_project, config, postgres_allocated=postgres_allocated
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
    dry_run: bool,
) -> tuple[PostgresProjectConfig | None, bool]:
    mode = "compose" if postgres_mode.lower() == "compose" else "external"
    if mode == "external":
        return None, False

    if postgres_image is None:
        if no_input or output_mode is not OutputMode.RICH:
            fail(
                output_mode,
                "init",
                "Missing required option --postgres-image for compose mode",
                dry_run=dry_run,
            )
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
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog_path = _cli_catalog_path()
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
    from_vscode: str,
    launch_name: str | None,
    no_input: bool,
    output_mode: OutputMode,
    dry_run: bool,
) -> ProjectConfig | None:
    try:
        result = import_vscode_launch(from_vscode, launch_name=launch_name, no_input=no_input)
    except VscodeImportError as e:
        fail(output_mode, "init", str(e), dry_run=dry_run)
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
