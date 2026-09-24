from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, MutableMapping
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
from odoo_instance_sdk.commands.backup import (  # noqa: I001 -- keep command registration aliases grouped; remove when Ruff supports grouped aliases.
    backup_group,
    configure_catalog_path_provider,
)
from odoo_instance_sdk.commands.bug_report import bug_report_group
from odoo_instance_sdk.commands.context import CliContext
from odoo_instance_sdk.commands.db import db_group
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    run_or_preview,
)
from odoo_instance_sdk.commands.cli_parts.init_registration import register_init_command
from odoo_instance_sdk.commands.pg import (
    postgres_group as _postgres_group,
    psql as _psql,
    register_database_commands,
)
from odoo_instance_sdk.commands.resource import (
    configure_catalog_path_provider as configure_resource_catalog_path_provider,
    resource_group,
)
from odoo_instance_sdk.internal.server import parse_payload
from odoo_instance_sdk.models import CommandResult

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.resources.postgres import PostgresCluster


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


def _bounded_redacted_tail(stderr: str) -> tuple[str, bool]:
    """Return the last ``_TIMEOUT_TAIL_BYTES`` redacted bytes of ``stderr``.

    A long startup log can push the real traceback past the first-N window the
    legacy ``sanitize_last_error`` projection used.  Taking the bounded *tail*
    instead keeps the most recent diagnostic (the traceback) visible while a
    leading prefix is dropped.  Redaction (secrets, env, paths, terminal
    escapes) is applied directly without the legacy whitespace squash or the
    2000-char re-truncation, so the trailing traceback survives.  Truncation
    is reported explicitly so callers can surface it in the stable error
    message.
    """
    from odoo_instance_sdk.internal.proc.run import _TIMEOUT_TAIL_BYTES
    from odoo_instance_sdk.internal.sanitize import (
        _ENV_VAR_RE,
        _PATH_LIKE_RE,
        _SECRET_PATTERNS,
        sanitize_terminal_text,
    )

    encoded = stderr.encode("utf-8", errors="replace")
    truncated = len(encoded) > _TIMEOUT_TAIL_BYTES
    if truncated:
        tail_bytes = encoded[-_TIMEOUT_TAIL_BYTES:]
        tail = tail_bytes.decode("utf-8", errors="replace")
    else:
        tail = stderr
    text = tail
    for pat in _SECRET_PATTERNS:
        text = pat.sub("<redacted>", text)
    text = _ENV_VAR_RE.sub("<env>", text)
    text = _PATH_LIKE_RE.sub("<path>", text)
    text = sanitize_terminal_text(text, preserve_newlines=True)
    return (text, truncated)


def _traceback_summary(tail: str) -> str | None:
    """Extract the final exception line from a bounded redacted stderr tail.

    A Python traceback ends with ``ExceptionType: message``.  Surfacing that
    single line keeps the stable error message concise (so it survives the
    shared diagnostic bound) while still naming the exception type and root
    cause, which is what the contract requires.  Returns ``None`` when no
    recognizable exception line is present.
    """
    import re as _re

    matches = _re.findall(r"(?m)^([A-Za-z_][\w.]*:[^\n]+)$", tail)
    return matches[-1].strip() if matches else None


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
    stderr_tail, truncated = _bounded_redacted_tail(value.stderr)
    message = f"shell exited {value.returncode}"
    summary = _traceback_summary(stderr_tail)
    if summary:
        message += f": {summary}"
    elif stderr_tail:
        message += f": {stderr_tail}"
    if truncated:
        message += " (stderr tail truncated to last 8192 bytes)"
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


def _load_update_command() -> click.Command:
    from odoo_instance_sdk.commands.update import update_command_cli

    return update_command_cli


def _load_test_command() -> click.Command:
    from odoo_instance_sdk.commands.test import test_command

    return test_command


def _lazy_command(*, name: str, help: str, loader: Callable[[], click.Command]) -> click.Command:
    return _LazyCommand(name=name, help=help, loader=loader)


_HEX_COMMIT = re.compile(r"^[0-9a-f]+$")


def _installed_version_with_vcs(package_name: str = "odoo-instance-sdk") -> str:
    """Return the package version, appending the short VCS commit when present.

    Reads optional PEP 610 ``direct_url.json`` via :mod:`importlib.metadata`.
    Falls back to the package version alone when metadata is missing, malformed,
    or has no hex ``vcs_info.commit_id`` of length >= 7. No Git, checkout, or
    network.
    """
    try:
        dist = distribution(package_name)
    except PackageNotFoundError:
        return "unknown"
    version = dist.version
    short_commit = _vcs_short_commit_from_distribution(dist)
    return f"{version} ({short_commit})" if short_commit else version


def _vcs_short_commit_from_distribution(dist: Distribution) -> str | None:
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    vcs_info = payload.get("vcs_info") if isinstance(payload, dict) else None
    if not isinstance(vcs_info, dict):
        return None
    commit_id = vcs_info.get("commit_id")
    if not isinstance(commit_id, str) or len(commit_id) < 7:
        return None
    return commit_id[:7] if _HEX_COMMIT.match(commit_id) else None


def _version_callback(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(f"odcli, version {_installed_version_with_vcs()}", color=ctx.color)
    ctx.exit()


class _OdcliCliGroup(click.RichGroup):  # type: ignore[misc,valid-type]
    """Root CLI group that blocks normal commands during unfinished updates."""

    def invoke(self, ctx: click.Context) -> None:
        from odoo_instance_sdk.internal.self_update import (
            assert_update_not_blocking,
            is_maintenance_mode,
        )

        if not is_maintenance_mode():
            subcommand = ctx.invoked_subcommand
            if subcommand is not None and subcommand != "update":
                assert_update_not_blocking(subcommand)
        super().invoke(ctx)


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
                {"name": "Maintenance", "commands": ["resource", "update"]},
                {"name": "Bug reports", "commands": ["bug-report"]},
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
@click.group(cls=_OdcliCliGroup)
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_version_callback,
    help="Show the version and exit.",
)
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
cli.add_command(
    _lazy_command(
        name="update",
        help="Self-upgrade an OdCLI uv-tool install.",
        loader=_load_update_command,
    ),
    name="update",
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
cli.add_command(bug_report_group, name="bug-report")

register_init_command(cli)


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
