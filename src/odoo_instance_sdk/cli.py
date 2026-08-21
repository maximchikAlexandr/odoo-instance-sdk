from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from odoo_instance_sdk.exceptions import VscodeImportError
from odoo_instance_sdk.internal.project_manifest import manifest_path, write_manifest
from odoo_instance_sdk.internal.vscode_import import import_vscode_launch
from odoo_instance_sdk.project import ProjectConfig


@click.group()
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
    ctx.ensure_object(dict)
    ctx.obj["project"] = project
    ctx.obj["env"] = env_selector


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
@click.option("--no-input", "no_input", is_flag=True, default=False, help="Forbid prompts.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Do not write.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
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
    no_input: bool,
    dry_run: bool,
    json_output: bool,
    project_path: str | None,
) -> None:
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
        vscode_cfg = _import_vscode(from_vscode, launch_name, no_input, json_output)
        if vscode_cfg is None:
            return
        _merge_vscode(option_state, vscode_cfg, provenance)

    if option_state.odoo_bin is None:
        if no_input or json_output or dry_run:
            _fail(no_input, json_output, "Missing required option --odoo-bin")
            return
        option_state.odoo_bin = Path(click.prompt("Path to odoo-bin"))
        provenance["discovery"].append("odoo_bin")

    if not option_state.odoo_bin:
        _fail(no_input, json_output, "odoo_bin is required")
        return

    config = ProjectConfig(
        odoo_bin=option_state.odoo_bin,
        python=option_state.python,
        source_config=option_state.source_config,
        default_source_database=option_state.default_source_database,
        preferred_http_port=option_state.preferred_http_port,
        requirements=option_state.requirements,
        default_run_args=option_state.default_run_args,
        runtime_cwd=option_state.runtime_cwd,
    )

    existing = manifest_path(resolved_project)
    if existing.is_file() and _handle_existing_manifest(
        existing, resolved_project, config, no_input, json_output
    ):
        return

    if dry_run:
        if json_output:
            _emit_json(
                ok=True,
                command="init",
                data=_manifest_dict(config),
                provenance=provenance,
                dry_run=True,
            )
        else:
            click.echo("Dry run — no files written.")
            click.echo(config.to_manifest())
        return

    write_manifest(resolved_project, config)
    if json_output:
        _emit_json(
            ok=True,
            command="init",
            data=_manifest_dict(config),
            provenance=provenance,
            dry_run=False,
        )
    else:
        click.echo(f"Wrote {existing}")


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
    from_vscode: str, launch_name: str | None, no_input: bool, json_output: bool
) -> ProjectConfig | None:
    try:
        result = import_vscode_launch(from_vscode, launch_name=launch_name, no_input=no_input)
    except VscodeImportError as e:
        _fail(no_input, json_output, str(e))
        return None
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
    json_output: bool,
) -> bool:
    try:
        existing_cfg = ProjectConfig.load(resolved_project)
    except Exception as e:
        _fail(no_input, json_output, f"Existing manifest unreadable: {e}")
        return True
    if existing_cfg == config:
        if json_output:
            _emit_json(
                ok=True, command="init", data=_manifest_dict(config), provenance={}, dry_run=True
            )
        else:
            click.echo("Manifest already up to date; no-op.")
        return True
    if no_input or json_output:
        _fail(
            no_input, json_output, "manifest exists and differs; remove it first or adjust options"
        )
        return True
    if not click.confirm("Manifest exists and differs; overwrite?", default=False):
        click.echo("Aborted.")
        return True
    return False


def _manifest_dict(config: ProjectConfig) -> dict[str, Any]:
    return {
        "odoo_bin": str(config.odoo_bin) if config.odoo_bin else None,
        "python": str(config.python) if config.python else None,
        "source_config": str(config.source_config) if config.source_config else None,
        "default_source_database": config.default_source_database,
        "preferred_http_port": config.preferred_http_port,
        "requirements": list(config.requirements),
        "default_run_args": list(config.default_run_args),
        "runtime_cwd": str(config.runtime_cwd) if config.runtime_cwd else None,
    }


def _emit_json(
    *, ok: bool, command: str, data: dict[str, Any], provenance: dict[str, Any], dry_run: bool
) -> None:
    envelope = {
        "schema_version": 1,
        "ok": ok,
        "command": command,
        "data": data,
        "provenance": provenance,
        "dry_run": dry_run,
        "warnings": [],
    }
    click.echo(json.dumps(envelope, indent=2, default=str))


def _fail(no_input: bool, json_output: bool, message: str) -> None:
    if json_output:
        _emit_json(ok=False, command="init", data={}, provenance={}, dry_run=False)
        click.echo(
            json.dumps(
                {
                    "schema_version": 1,
                    "ok": False,
                    "error": {"code": "init_failed", "message": message},
                },
                indent=2,
            ),
            err=True,
        )
    else:
        click.echo(message, err=True)
    sys.exit(1)


if __name__ == "__main__":
    cli()
