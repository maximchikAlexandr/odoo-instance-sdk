"""Click adapter for local Odoo test selection and execution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    OutputMode,
    emit,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
    success_document,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.models import DevelopmentEnvironment
    from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.automation import (
    TestCommandSnapshot,
    run_odoo_tests_command,
)
from odoo_instance_sdk.internal.test_selection import (
    _ChangedSelection,
    _ChangedSelectionError,
    _TestSelection,
    preflight_installed_modules,
    resolve_changed_selection,
    resolve_test_selection,
)
from odoo_instance_sdk.models import OdooTestResult, OdooTestSpec, StartConfig


def _looks_like_file_target(target: str) -> bool:
    return "/" in target or "\\" in target or target.endswith(".py")


def _validate_options(
    target: str | None,
    *,
    changed: bool,
    base: str | None,
    dry_run: bool,
    tags: str | None,
) -> None:
    if changed and target is not None:
        raise click.UsageError("TARGET cannot be combined with --changed")
    if base is not None and not changed:
        raise click.UsageError("--base requires --changed")
    if dry_run and not changed:
        raise click.UsageError("--dry-run requires --changed")
    if tags is not None and not tags.strip():
        raise click.UsageError("--tags must not be blank")
    if target is not None and tags is not None and _looks_like_file_target(target):
        raise click.UsageError("a test-file target cannot be combined with --tags")


def _selection_dict(
    selection: _TestSelection | tuple[_TestSelection, ...] | dict[str, JsonValue],
) -> dict[str, JsonValue]:
    if isinstance(selection, dict):
        return dict(selection)
    if isinstance(selection, tuple):
        single = selection[0] if len(selection) == 1 else None
        if single is None:
            return {"kind": "module", "value": None, "module_path": None, "file_path": None}
        provenance = single.provenance
        return {
            "kind": "module",
            "value": provenance.value,
            "module_path": str(provenance.module_path),
            "file_path": str(provenance.file_path) if provenance.file_path is not None else None,
        }
    provenance = selection.provenance
    return {
        "kind": provenance.kind,
        "value": provenance.value,
        "module_path": str(provenance.module_path),
        "file_path": str(provenance.file_path) if provenance.file_path is not None else None,
    }


def _common_result(
    env_obj: DevelopmentEnvironment,
    *,
    selection: dict[str, JsonValue],
    modules: tuple[str, ...],
    exit_code: int,
) -> dict[str, JsonValue]:
    return {
        "environment_id": str(env_obj.id),
        "environment_name": str(env_obj.name),
        "worktree": str(env_obj.worktree_path),
        "selection": selection,
        "modules": list(modules),
        "exit_code": exit_code,
    }


def _changed_result(plan: _ChangedSelection, result: dict[str, JsonValue]) -> dict[str, JsonValue]:
    result.update(
        {
            "base_source": plan.base_source,
            "requested_base": plan.requested_base,
            "resolved_base": plan.resolved_base,
            "merge_base": plan.merge_base,
            "head": plan.head,
            "changed_files": list(plan.changed_files),
            "ignored_paths": list(plan.ignored_paths),
            "unmapped_paths": list(plan.unmapped_paths),
        }
    )
    return result


def project_execution_result(
    env_obj: DevelopmentEnvironment,
    selection: _TestSelection | tuple[_TestSelection, ...] | dict[str, JsonValue],
    spec: OdooTestSpec,
    typed: OdooTestResult,
) -> dict[str, JsonValue]:
    """Project every executed test entry point through one result shape."""
    result = _common_result(
        env_obj,
        selection=_selection_dict(selection),
        modules=spec.modules,
        exit_code=typed.exit_code,
    )
    result.update(
        {
            "test_tags": spec.test_tags,
            "reload_tests": spec.reload_tests,
            "allow_empty": spec.allow_empty,
            "counts": dict(typed.counts),
            "failures": typed.failures,
            "zero_tests": typed.zero_tests,
        }
    )
    return result


def _emit_result(
    *,
    mode: OutputMode,
    command: str,
    result: dict[str, JsonValue],
    dry_run: bool,
    diagnostic: str | None = None,
) -> None:
    def rich_projection(_document: OutputDocument) -> str:
        selection = result.get("selection")
        selection_kind = (
            str(selection.get("kind", "unknown")) if isinstance(selection, dict) else "unknown"
        )
        modules = result.get("modules")
        module_text = (
            ", ".join(str(item) for item in modules) if isinstance(modules, list) else "none"
        )
        lines = [
            f"environment={result['environment_name']} ({result['environment_id']})",
            f"selection={selection_kind}",
            f"modules={module_text or 'none'}",
        ]
        if "test_tags" in result:
            counts = result.get("counts")
            counts = counts if isinstance(counts, dict) else {}
            lines.append(
                f"tests={counts.get('tests', 0)} "
                f"ok={counts.get('successful', 0)} "
                f"failed={counts.get('failed', 0)} "
                f"errors={counts.get('errors', 0)} "
                f"skipped={counts.get('skipped', 0)}"
            )
        elif result.get("reason") == "no_addon_changes":
            lines.append("reason=no_addon_changes")
        elif result.get("dry_run"):
            lines.append("dry_run=true")
        lines.append(f"exit_code={result['exit_code']}")
        return "\n".join(lines)

    emit(
        success_document(
            command=command,
            result=result,
            dry_run=dry_run,
        ),
        mode,
        rich=rich_projection,
        diagnostic=diagnostic,
    )


def _start_config(instance: OdooInstance) -> StartConfig:
    config = instance.config.start_config
    if config is None:
        raise ConfigError("selected environment has no generated Odoo config")
    return config


def _execute_selection(
    instance: OdooInstance,
    env_obj: DevelopmentEnvironment,
    selection: _TestSelection,
    *,
    reload_tests: bool,
    allow_empty: bool,
    tags: str | None,
) -> tuple[dict[str, JsonValue], str | None]:
    spec = OdooTestSpec(
        modules=selection.modules,
        test_tags=selection.test_tags if tags is None else tags,
        reload_tests=reload_tests,
        allow_empty=allow_empty,
    )
    preflight_installed_modules(instance, spec.modules)
    typed, diagnostic = run_odoo_tests_command(
        instance,
        spec,
        http_interface=env_obj.http_interface,
        http_port=env_obj.http_port,
    ).run()
    return project_execution_result(env_obj, selection, spec, typed), diagnostic


def resolve_module_test_selection(
    worktree_path: str | Path,
    start_config: StartConfig,
    modules: tuple[str, ...],
    test_tags: str,
) -> tuple[_TestSelection, ...]:
    """Resolve legacy plural modules against the registered worktree only."""
    if not modules:
        raise ConfigError("module test requires at least one module")
    if not test_tags:
        raise ConfigError("module test requires --test-tags")
    selected = tuple(sorted(set(modules)))
    return tuple(
        resolve_test_selection(
            worktree_path,
            start_config,
            target=module,
            cwd=worktree_path,
            tags=test_tags,
        )
        for module in selected
    )


@click.command("test", help="Select and run Odoo tests.")
@click.argument("target", required=False)
@click.option("--tags", "tags", default=None, help="Native Odoo test tags.")
@click.option("--reload-tests", is_flag=True, default=False)
@click.option("--allow-empty", is_flag=True, default=False)
@click.option("--changed", is_flag=True, default=False, help="Select directly changed addons.")
@click.option("--base", default=None, help="Git baseline used with --changed.")
@click.option("--dry-run", is_flag=True, default=False, help="Select and report without running.")
@output_options
@pass_cli_context
def test_command(
    ctx: CliContext,
    target: str | None,
    tags: str | None,
    reload_tests: bool,
    allow_empty: bool,
    changed: bool,
    base: str | None,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    _validate_options(target, changed=changed, base=base, dry_run=dry_run, tags=tags)
    try:
        _client, runtime_context, instance = cli_context.ready_instance(ctx)
        env_obj = cli_context.require_environment(runtime_context)
        start_config = _start_config(instance)
        if changed:
            plan = resolve_changed_selection(
                env_obj.worktree_path,
                start_config,
                base=base,
                environment_base=env_obj.base_ref,
                tags=tags,
            )
            result = _common_result(
                env_obj,
                selection={"kind": "changed", "value": None},
                modules=plan.modules,
                exit_code=0,
            )
            _changed_result(plan, result)
            if plan.unmapped_paths:
                result["exit_code"] = 1
                _emit_result(mode=mode, command="test", result=result, dry_run=dry_run)
                raise click.exceptions.Exit(1)  # noqa: TRY301
            if not plan.modules:
                result["modules"] = []
                result["reason"] = "no_addon_changes"
                if dry_run:
                    result["dry_run"] = True
                _emit_result(mode=mode, command="test", result=result, dry_run=dry_run)
                raise click.exceptions.Exit(0)  # noqa: TRY301
            spec = OdooTestSpec(
                modules=plan.modules,
                test_tags=getattr(plan, "test_tags", None)
                or ",".join(f"/{module}" for module in plan.modules),
                reload_tests=reload_tests,
                allow_empty=allow_empty,
            )
            command = run_odoo_tests_command(
                instance,
                spec,
                http_interface=env_obj.http_interface,
                http_port=env_obj.http_port,
                selection_snapshot=TestCommandSnapshot(
                    worktree=Path(env_obj.worktree_path),
                    git_head=getattr(plan, "head", None),
                    git_base=getattr(plan, "resolved_base", None),
                    changed_files=tuple(plan.changed_files),
                    modules=tuple(plan.modules),
                    database_names=tuple(getattr(instance.config, "configured_database_names", ())),
                    database_identity=(
                        getattr(instance.config, "db_host", None),
                        getattr(instance.config, "db_port", None),
                        getattr(instance.config, "db_user", None),
                    ),
                    interface=env_obj.http_interface,
                    port=env_obj.http_port,
                ),
            )
            if dry_run:
                result["plan"] = model_to_dict(command.plan)
                result["dry_run"] = True
                run_or_preview(
                    lambda: command,
                    command_name="test",
                    mode=mode,
                    dry_run=True,
                    preview=lambda _command: result,
                )
                raise click.exceptions.Exit(0)  # noqa: TRY301
            typed, diagnostic = command.run()
            result = project_execution_result(
                env_obj,
                {"kind": "changed", "value": None},
                spec,
                typed,
            )
            _changed_result(plan, result)
            _emit_result(
                mode=mode, command="test", result=result, dry_run=False, diagnostic=diagnostic
            )
            raise click.exceptions.Exit(typed.exit_code)  # noqa: TRY301

        selection = resolve_test_selection(
            env_obj.worktree_path,
            start_config,
            target=target,
            cwd=Path.cwd(),
            tags=tags,
        )
        result, diagnostic = _execute_selection(
            instance,
            env_obj,
            selection,
            reload_tests=reload_tests,
            allow_empty=allow_empty,
            tags=tags,
        )
        _emit_result(mode=mode, command="test", result=result, dry_run=False, diagnostic=diagnostic)
        raise click.exceptions.Exit(cast("int", result["exit_code"]))  # noqa: TRY301
    except (click.exceptions.Exit, SystemExit):
        raise
    except _ChangedSelectionError as exc:
        result = _common_result(
            env_obj,
            selection={"kind": "changed", "value": None},
            modules=exc.plan.modules,
            exit_code=1,
        )
        _changed_result(exc.plan, result)
        _emit_result(mode=mode, command="test", result=result, dry_run=dry_run, diagnostic=str(exc))
        raise click.exceptions.Exit(1)
    except Exception as exc:
        fail(mode, "test", str(exc))


def run_module_tests(
    instance: OdooInstance,
    selection: tuple[_TestSelection, ...],
    spec: OdooTestSpec,
    *,
    http_interface: str,
    http_port: int,
) -> tuple[OdooTestResult, str | None]:
    """Compatibility adapter for an already-resolved legacy module selection."""
    if tuple(item.modules[0] for item in selection) != spec.modules:
        raise ConfigError("module test selection does not match requested modules")
    preflight_installed_modules(instance, spec.modules)
    typed, diagnostic = run_odoo_tests_command(
        instance,
        spec,
        http_interface=http_interface,
        http_port=http_port,
    ).run()
    return typed, diagnostic


__all__ = [
    "project_execution_result",
    "resolve_module_test_selection",
    "run_module_tests",
    "test_command",
]
