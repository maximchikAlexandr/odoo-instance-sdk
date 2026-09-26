from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    import click

    from odoo_instance_sdk.commands.output import OutputDocument
    from odoo_instance_sdk.execution import Command, JsonValue
else:
    import rich_click as click

from odoo_instance_sdk.commands.output import (
    JsonValue,
    OutputMode,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    VscodeImportError,
)
from odoo_instance_sdk.internal.generated_config import project_generated_config_path
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.project_init import (
    validate_generated_config_target as _validate_generated_config_target,
)
from odoo_instance_sdk.internal.project_manifest import manifest_path
from odoo_instance_sdk.internal.urls import normalize_base_url
from odoo_instance_sdk.internal.vscode_import import import_vscode_launch
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.project import (
    PostgresProjectConfig,
    ProjectConfig,
    RemoteSourceConfig,
    TestInstanceProjectConfig,
    normalize_remote_name,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


InitOption = str | int | bool | tuple[str, ...] | tuple[tuple[str, str, str, str], ...] | None


class _InitRunOrPreview(Protocol):
    def __call__(
        self,
        build_command: Callable[[], Command[dict[str, JsonValue]]],
        *,
        command_name: str,
        mode: OutputMode,
        dry_run: bool,
        result: Callable[[dict[str, JsonValue] | None], dict[str, JsonValue]] | None = None,
        provenance: dict[str, JsonValue] | None = None,
        confirm: Callable[[], None] | None = None,
        rich: Callable[[OutputDocument], str] | None = None,
        preview: Callable[[Command[dict[str, JsonValue]]], dict[str, JsonValue]] | None = None,
        emit_normal: bool = True,
    ) -> tuple[int, dict[str, JsonValue] | None]: ...


@dataclass(frozen=True, slots=True)
class _InitRequest:
    odoo_bin: str | None
    python: str | None
    source_config: str | None
    default_source_database: str | None
    preferred_http_port: int | None
    requirements: tuple[str, ...]
    run_args: tuple[str, ...]
    runtime_cwd: str | None
    from_vscode: str | None
    launch_name: str | None
    postgres_mode: str
    postgres_image: str | None
    postgres_port: int | None
    postgres_user: str | None
    no_input: bool
    yes: bool
    dry_run: bool
    test_url: str | None
    test_database: str | None
    test_branch: str | None
    remote_entries: tuple[tuple[str, str, str, str], ...]
    local_config: bool
    allow_partial: bool
    output_format: str | None
    json_output: bool
    project_path: str | None


def _bind_init_request(options: dict[str, InitOption]) -> _InitRequest:
    return _InitRequest(
        odoo_bin=cast("str | None", options["odoo_bin"]),
        python=cast("str | None", options["python"]),
        source_config=cast("str | None", options["source_config"]),
        default_source_database=cast("str | None", options["default_source_database"]),
        preferred_http_port=cast("int | None", options["preferred_http_port"]),
        requirements=cast("tuple[str, ...]", options["requirements"]),
        run_args=cast("tuple[str, ...]", options["run_args"]),
        runtime_cwd=cast("str | None", options["runtime_cwd"]),
        from_vscode=cast("str | None", options["from_vscode"]),
        launch_name=cast("str | None", options["launch_name"]),
        postgres_mode=cast("str", options["postgres_mode"]),
        postgres_image=cast("str | None", options["postgres_image"]),
        postgres_port=cast("int | None", options["postgres_port"]),
        postgres_user=cast("str | None", options["postgres_user"]),
        no_input=cast("bool", options["no_input"]),
        yes=cast("bool", options["yes"]),
        dry_run=cast("bool", options["dry_run"]),
        test_url=cast("str | None", options["test_url"]),
        test_database=cast("str | None", options["test_database"]),
        test_branch=cast("str | None", options["test_branch"]),
        remote_entries=cast("tuple[tuple[str, str, str, str], ...]", options["remote_entries"]),
        local_config=cast("bool", options["local_config"]),
        allow_partial=cast("bool", options["allow_partial"]),
        output_format=cast("str | None", options["output_format"]),
        json_output=cast("bool", options["json_output"]),
        project_path=cast("str | None", options["project_path"]),
    )


def _confirm_partial_callback(
    *,
    output_mode: OutputMode,
    dry_run: bool,
    effective_no_input: bool,
    allow_partial: bool,
) -> Callable[[list[str], dict[str, str]], None] | None:
    if effective_no_input or allow_partial or dry_run or output_mode is not OutputMode.RICH:
        return None

    def confirm(missing: list[str], details: dict[str, str]) -> None:
        missing_text = ", ".join(missing)
        if not click.confirm(
            f"Setup is incomplete ({missing_text}). Continue with partial initialization?",
            default=False,
        ):
            fail(
                output_mode,
                "init",
                f"init_incomplete: missing capabilities {missing} ({details})",
                dry_run=dry_run,
                error_code="init_incomplete",
            )

    return confirm


def _execute_init(
    request: _InitRequest,
    *,
    output_mode: OutputMode,
    effective_no_input: bool,
    confirm_partial: Callable[[list[str], dict[str, str]], None] | None,
    run_or_preview: _InitRunOrPreview,
) -> None:
    resolved_project = Path(request.project_path) if request.project_path else Path.cwd()
    provenance: dict[str, list[str]] = {
        "option": [],
        "vscode": [],
        "discovery": [],
        "default": [],
    }
    option_state = _OptionState(
        odoo_bin=Path(request.odoo_bin) if request.odoo_bin else None,
        python=request.python,
        source_config=Path(request.source_config) if request.source_config else None,
        default_source_database=request.default_source_database,
        preferred_http_port=request.preferred_http_port,
        requirements=request.requirements,
        default_run_args=request.run_args,
        runtime_cwd=Path(request.runtime_cwd) if request.runtime_cwd else None,
    )
    _record_option_provenance(option_state, provenance)

    if request.from_vscode is not None:
        vscode_cfg = _import_vscode(
            request.from_vscode,
            request.launch_name,
            request.no_input,
            output_mode,
            request.dry_run,
        )
        if vscode_cfg is None:
            return
        _merge_vscode(option_state, vscode_cfg, provenance)

    from odoo_instance_sdk.commands.cli_parts.callbacks import _resolve_odoo_bin

    _resolve_odoo_bin(
        option_state,
        request.no_input,
        output_mode,
        request.dry_run,
        provenance,
    )
    postgres_cfg, postgres_allocated = _resolve_postgres_state(
        postgres_mode=request.postgres_mode,
        postgres_image=request.postgres_image,
        postgres_port=request.postgres_port,
        postgres_user=request.postgres_user,
        source_config=option_state.source_config,
        no_input=request.no_input,
        output_mode=output_mode,
        project_path=resolved_project,
        dry_run=request.dry_run,
    )
    if postgres_cfg is not None:
        provenance["option"].append("postgres")

    test_instance_cfg = _resolve_test_instance(
        resolved_project,
        test_url=request.test_url,
        test_database=request.test_database,
        test_branch=request.test_branch,
    )
    existing_test_instance = _existing_test_instance(resolved_project)
    if test_instance_cfg is None and existing_test_instance is not None:
        test_instance_cfg = existing_test_instance
    existing_remote_instances = _existing_remote_instances(resolved_project)
    remote_instances = (
        _resolve_remote_entries(request.remote_entries)
        if request.remote_entries
        else existing_remote_instances
    )

    effective_source_config = option_state.source_config
    if request.local_config and postgres_cfg is not None and postgres_cfg.mode == "compose":
        effective_source_config = project_generated_config_path(resolved_project)
        provenance["option"].append("local_config")

    config = ProjectConfig(
        repository_root=resolved_project.resolve(),
        odoo_bin=option_state.odoo_bin,
        python=option_state.python,
        source_config=effective_source_config,
        default_source_database=option_state.default_source_database,
        preferred_http_port=option_state.preferred_http_port,
        requirements=option_state.requirements,
        default_run_args=option_state.default_run_args,
        runtime_cwd=option_state.runtime_cwd,
        postgres=postgres_cfg,
        test_instance=test_instance_cfg,
        remote_instances=remote_instances,
        ticket_link_enabled=False,
    )
    if config.postgres is not None and config.postgres.mode == "compose":
        try:
            _validate_generated_config_target(
                project_generated_config_path(resolved_project), project_root=resolved_project
            )
        except InstanceConfigurationError as exc:
            fail(output_mode, "init", str(exc), dry_run=request.dry_run)

    from odoo_instance_sdk.commands.cli_parts.callbacks import _handle_existing_manifest

    existing = manifest_path(resolved_project)
    if existing.is_file() and _handle_existing_manifest(
        existing,
        resolved_project,
        config,
        request.no_input,
        request.yes,
        output_mode,
        dry_run=request.dry_run,
    ):
        return
    from odoo_instance_sdk.project_init import init_completeness_preview, init_project_command

    status, _ = run_or_preview(
        lambda: init_project_command(
            resolved_project,
            config,
            postgres_allocated=postgres_allocated,
            local_config=request.local_config,
            postgres_image=request.postgres_image,
            existing_test_instance=existing_test_instance,
            allow_partial=request.allow_partial,
            no_input=effective_no_input,
            dry_run=request.dry_run,
            confirm_partial=confirm_partial,
        ),
        command_name="init",
        mode=output_mode,
        dry_run=request.dry_run,
        result=lambda value: cast("dict[str, JsonValue]", value),
        provenance=cast("dict[str, JsonValue]", provenance),
        preview=lambda command: {
            **init_completeness_preview(
                resolved_project,
                config,
                local_config=request.local_config,
                postgres_image=request.postgres_image,
                existing_test_instance=existing_test_instance,
                dry_run=True,
                remote_database_names=None,
                postgres_allocated=postgres_allocated,
            ),
            "plan": model_to_dict(command.plan),
        },
        rich=lambda _document: (
            f"Dry run — no files written.\n{config.to_manifest()}"
            if request.dry_run
            else f"Wrote {existing}"
        ),
    )
    sys.exit(status)


def register_init_command(cli: click.Group) -> None:
    """Register the ``init`` leaf on the root CLI group."""

    @cli.command(help="Create or update the project manifest.")
    @click.option(
        "--odoo-bin", "odoo_bin", type=click.Path(), default=None, help="Path to odoo-bin."
    )
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
    @click.option(
        "--runtime-cwd", "runtime_cwd", type=click.Path(), default=None, help="Runtime cwd."
    )
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
    @click.option(
        "--test-url",
        "test_url",
        default=None,
        help="Remote test instance base URL for [test_instance].url.",
    )
    @click.option(
        "--test-database",
        "test_database",
        default=None,
        help="Remote test instance database for [test_instance].database.",
    )
    @click.option(
        "--test-branch",
        "test_branch",
        default=None,
        help="Remote test instance git branch for [test_instance].git_branch.",
    )
    @click.option(
        "--remote",
        "remote_entries",
        nargs=4,
        multiple=True,
        metavar="NAME URL DATABASE GIT_REF",
        help="Named remote source; repeat for multiple sources.",
    )
    @click.option(
        "--local-config",
        "local_config",
        is_flag=True,
        default=False,
        help="Select generated .odcli/odoo.conf as the effective local source_config.",
    )
    @click.option(
        "--allow-partial",
        "allow_partial",
        is_flag=True,
        default=False,
        help="Allow partial initialization when setup is incomplete.",
    )
    @output_options
    @click.option(
        "--project",
        "project_path",
        type=click.Path(exists=False),
        default=None,
        help="Project path.",
    )
    def init(**options: InitOption) -> None:
        request = _bind_init_request(options)
        output_mode = resolve_output_mode(request.output_format, request.json_output)
        confirm_partial = _confirm_partial_callback(
            output_mode=output_mode,
            dry_run=request.dry_run,
            effective_no_input=request.no_input or output_mode is not OutputMode.RICH,
            allow_partial=request.allow_partial,
        )
        _execute_init(
            request,
            output_mode=output_mode,
            effective_no_input=request.no_input or output_mode is not OutputMode.RICH,
            confirm_partial=confirm_partial,
            run_or_preview=cast("_InitRunOrPreview", run_or_preview),
        )


def _resolve_test_instance(
    project_root: Path,
    *,
    test_url: str | None,
    test_database: str | None,
    test_branch: str | None,
) -> TestInstanceProjectConfig | None:
    """Build a [test_instance] from explicit options.

    On re-run without test-instance options, callers preserve an existing
    valid section via ``_existing_test_instance``; this helper returns ``None``
    when no test-instance option was supplied so the preservation path applies.
    """
    if test_url is None and test_database is None and test_branch is None:
        return None
    from odoo_instance_sdk.project import TestInstanceProjectConfig

    if test_url is None:
        existing = _existing_test_instance(project_root)
        url = existing.base_url if existing is not None else ""
    else:
        url = test_url
    if not url:
        return None
    return TestInstanceProjectConfig(
        base_url=url,
        database=test_database,
        git_branch=test_branch,
    )


def _existing_test_instance(project_root: Path) -> TestInstanceProjectConfig | None:
    """Return an existing valid [test_instance] from the manifest, if any."""
    manifest = manifest_path(project_root)
    if not manifest.is_file():
        return None
    try:
        existing_cfg = ProjectConfig.load(project_root)
    except Exception:
        return None
    return existing_cfg.test_instance


def _existing_remote_instances(project_root: Path) -> tuple[RemoteSourceConfig, ...]:
    """Return existing named sources so re-init without ``--remote`` preserves them."""
    manifest = manifest_path(project_root)
    if not manifest.is_file():
        return ()
    try:
        return ProjectConfig.load(project_root).remote_instances
    except Exception:
        return ()


def _resolve_remote_entries(
    entries: tuple[tuple[str, str, str, str], ...],
) -> tuple[RemoteSourceConfig, ...]:
    sources = tuple(
        RemoteSourceConfig(
            name=normalize_remote_name(name),
            base_url=normalize_base_url(url),
            database=database,
            git_branch=git_ref,
        )
        for name, url, database, git_ref in entries
    )
    if len({source.name for source in sources}) != len(sources):
        raise click.UsageError("duplicate --remote names are not allowed")
    return tuple(sorted(sources, key=lambda source: source.name))


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
    from odoo_instance_sdk.internal.paths import get_catalog_path
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog_path = get_catalog_path(ensure_exists=True)
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
