from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
import odoo_instance_sdk.commands.env.list as _env_list_commands  # noqa: F401
from odoo_instance_sdk.commands.backup import (
    backup_group,
    configure_catalog_path_provider,
)
from odoo_instance_sdk.commands.context import CliContext
from odoo_instance_sdk.commands.db import db_group
from odoo_instance_sdk.commands.env import env_group
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    action_command,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
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
from odoo_instance_sdk.commands.ps import ps_command
from odoo_instance_sdk.commands.resource import (
    configure_catalog_path_provider as configure_resource_catalog_path_provider,
)
from odoo_instance_sdk.commands.resource import (
    resource_group,
)
from odoo_instance_sdk.commands.test import (
    resolve_module_test_selection,  # noqa: F401 - extracted module callback seam
    test_command,
)
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    VscodeImportError,
)
from odoo_instance_sdk.internal.database_preparation import _planned_project_identity
from odoo_instance_sdk.internal.generated_config import (
    generate_config,
    project_generated_config_path,
    render_config,
)
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.project_manifest import manifest_path, write_manifest
from odoo_instance_sdk.internal.project_runtime import resolve_project_http_port
from odoo_instance_sdk.internal.server import parse_payload
from odoo_instance_sdk.internal.vscode_import import import_vscode_launch
from odoo_instance_sdk.models import (
    CommandResult,
    PostgresClusterState,
    StartConfig,
)
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig

if TYPE_CHECKING:
    from collections.abc import Callable as TypeCallback

    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.commands.context import ResolvedContext
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

    class _DoctorRunner(Protocol):
        def __call__(
            self,
            client: OdooClient,
            project_path: Path | None,
            *,
            resolved_context: ResolvedContext | None = None,
        ) -> DoctorReport: ...


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
    if name in {
        "export_translations_command",
        "list_modules_command",
        "update_modules_command",
    }:
        from odoo_instance_sdk.internal import automation

        value = getattr(automation, name)
        globals()[name] = value
        return cast("CliLazyExport", value)
    if name == "module_tests_command":
        from odoo_instance_sdk.resources.testing import module_tests_command

        globals()[name] = module_tests_command
        return cast("CliLazyExport", module_tests_command)
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


def _client_class() -> type[OdooClient]:
    return cast("type[OdooClient]", getattr(sys.modules[__name__], "OdooClient"))


def _run_doctor() -> _DoctorRunner:
    return cast(
        "_DoctorRunner",
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


cli.add_command(env_group, name="env")
cli.add_command(test_command, name="test")
cli.add_command(db_group, name="db")
cli.add_command(backup_group, name="backup")
cli.add_command(_postgres_group, name="postgres")
register_database_commands(db_group)
cli.add_command(_psql, name="psql")
cli.add_command(resource_group, name="resource")
cli.add_command(ps_command, name="ps")

_callbacks_loaded = False
_original_get_command = cli.get_command


def _ensure_callbacks_loaded() -> None:
    if _callbacks_loaded:
        return
    import odoo_instance_sdk.commands.cli_parts.callbacks_a as _callbacks_a

    del _callbacks_a
    globals()["_callbacks_loaded"] = True


def _lazy_get_command(ctx: click.Context, name: str) -> click.Command | None:
    if not ctx.resilient_parsing:
        _ensure_callbacks_loaded()
    return cast("click.Command | None", _original_get_command(ctx, name))


cli.get_command = _lazy_get_command


class _LazyGitGroup(click.RichGroup):  # type: ignore[misc,valid-type]
    """Expose Git help at the root without importing the Git execution stack."""

    def __init__(self) -> None:
        self._git_initializing = True
        self._git_loaded = False
        self._git_commands: MutableMapping[str, click.Command] = {}
        super().__init__(name="git", help="Generate and safely synchronize Odoo Git workflows.")
        self._git_initializing = False

    @property
    def commands(self) -> MutableMapping[str, click.Command]:
        if self._git_initializing:
            return self._git_commands
        if not self._git_loaded:
            self._git_commands = self._loaded().commands
            self._git_loaded = True
        return self._git_commands

    @commands.setter
    def commands(self, value: MutableMapping[str, click.Command]) -> None:
        self._git_commands = value

    @staticmethod
    def _loaded() -> click.Group:
        from odoo_instance_sdk.commands.git import git_group

        return git_group

    def list_commands(self, ctx: click.Context) -> list[str]:
        return self._loaded().list_commands(ctx)

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        return self._loaded().get_command(ctx, name)


cli.add_command(_LazyGitGroup(), name="git")


def _cli_catalog_path(*, ensure_exists: bool = True) -> Path:
    import odoo_instance_sdk.cli as _cli_shim

    get_catalog_path = cast("Callable[..., Path]", _cli_shim.get_catalog_path)
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

    from odoo_instance_sdk.commands.cli_parts.callbacks_a import _resolve_odoo_bin

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
            _validate_generated_config_target(project_generated_config_path(resolved_project))
        except InstanceConfigurationError as exc:
            fail(output_mode, "init", str(exc), dry_run=dry_run)

    from odoo_instance_sdk.commands.cli_parts.callbacks_a import (
        _handle_existing_manifest,
        _manifest_dict,
    )

    existing = manifest_path(resolved_project)
    if existing.is_file() and _handle_existing_manifest(
        existing, resolved_project, config, no_input, yes, output_mode, dry_run=dry_run
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
    from odoo_instance_sdk.commands.cli_parts.callbacks_a import _manifest_dict

    write_manifest(project_path, config)
    if config.postgres is not None and config.postgres.mode == "compose":
        _write_project_generated_config(project_path, config)
    _register_initialized_project(project_path)
    return _manifest_dict(config, postgres_allocated=postgres_allocated)


def _write_project_generated_config(project_path: Path, config: ProjectConfig) -> None:
    """Bind a Compose project config to its existing private cluster secret."""
    root = project_path.resolve()
    source = config.source_config
    source_path = (
        (root / source).resolve() if source is not None and not source.is_absolute() else source
    )
    if source_path is None:
        candidate = root / "odoo.conf"
        source_path = candidate if candidate.is_file() else None
    elif not source_path.is_file():
        raise InstanceConfigurationError("local source config is missing")

    from odoo_instance_sdk.internal.postgres_compose import ensure_password_file
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    cluster = PostgresCluster.from_project(root)
    password = ensure_password_file(cluster.password_file)
    source_start = (
        StartConfig.from_odoo_config(source_path) if source_path is not None else StartConfig()
    )
    postgres = config.postgres
    assert postgres is not None
    generate_config(
        source_path,
        project_generated_config_path(root),
        repo_root=root,
        worktree=root,
        http_interface=source_start.http_interface,
        http_port=resolve_project_http_port(config.preferred_http_port, source_start.http_port),
        db_name=config.default_source_database or source_start.db_name or "",
        db_host=cluster.endpoint_host,
        db_port=cluster.endpoint_port,
        db_user=postgres.user or "odoo",
        db_password=password,
    )


def _validate_generated_config_target(path: Path) -> None:
    """Reject unsafe targets before any generated-config or secret write."""
    try:
        target = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink() or not path.is_file():
        raise InstanceConfigurationError(
            f"generated config target must be a regular file, not a symlink or directory: {path}"
        )
    if target.st_uid != os.getuid():
        raise InstanceConfigurationError(
            f"generated config is not owned by the current user: {path}"
        )
    from odoo_instance_sdk.internal.git_worktree import GitError, is_tracked_path

    try:
        if is_tracked_path(path):
            raise InstanceConfigurationError(
                "project-owned runtime config is tracked; refusing secret write: .odcli/odoo.conf"
            )
    except GitError as exc:
        raise InstanceConfigurationError(
            "unable to verify project-owned runtime config tracking; refusing secret write"
        ) from exc


def _generated_config_needs_repair(project_path: Path, config: ProjectConfig) -> bool:
    """Compare generated bytes to current inputs without creating anything."""
    if config.postgres is None or config.postgres.mode != "compose":
        return False
    destination = project_generated_config_path(project_path)
    try:
        if destination.is_symlink() or not destination.is_file():
            return True
        if destination.stat().st_mode & 0o777 != 0o600:
            return True
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        cluster = PostgresCluster.from_project(project_path)
        if not cluster.password_file.is_file():
            return True
        password = cluster.password_file.read_text(encoding="utf-8").strip()
        source = config.source_config
        source_path = (
            (project_path / source).resolve()
            if source is not None and not source.is_absolute()
            else source
        )
        if source_path is None:
            candidate = project_path / "odoo.conf"
            source_path = candidate if candidate.is_file() else None
        if source_path is not None and not source_path.is_file():
            return True
        source_start = (
            StartConfig.from_odoo_config(source_path) if source_path is not None else StartConfig()
        )
        expected = render_config(
            source_path,
            destination,
            repo_root=project_path,
            worktree=project_path,
            http_interface=source_start.http_interface,
            http_port=resolve_project_http_port(config.preferred_http_port, source_start.http_port),
            db_name=config.default_source_database or source_start.db_name or "",
            db_host=cluster.endpoint_host,
            db_port=cluster.endpoint_port,
            db_user=config.postgres.user or "odoo",
            db_password=password,
        )
        return destination.read_text(encoding="utf-8") != expected
    except (OSError, UnicodeError, InstanceConfigurationError, ValueError):
        return True


def _register_initialized_project(project_path: Path) -> None:
    """Idempotently register a project after its valid manifest is available."""
    root, common, identity = _planned_project_identity(project_path)
    project_id = f"project_{identity}"
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = BackupCatalog(db_path=_cli_catalog_path())
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
