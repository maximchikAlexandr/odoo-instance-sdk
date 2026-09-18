"""Compatibility re-export shim."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.cli_parts.registration import (
    _rich_shell_projection,
    _run_shell_command,
    _shell_failure,
    _shell_payload,
    _ShellCommandFailure,
    cli,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.commands.context import ResolvedContext, RuntimeView
    from odoo_instance_sdk.commands.output import OutputDocument
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.automation import TranslationExportResult
    from odoo_instance_sdk.internal.doctor.manifest import DoctorReport
    from odoo_instance_sdk.internal.proc import ProcessExecutor
    from odoo_instance_sdk.internal.test_selection import _TestSelection
    from odoo_instance_sdk.models import (
        CommandResult,
        DepsVerifyResult,
        OdooTestResult,
        OdooTestSpec,
        StartConfig,
    )
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.instance import OdooInstance

    class _RichVscodeGenerate(Protocol):
        def __call__(self, document: OutputDocument) -> str: ...

    class _BuildLaunchProfile(Protocol):
        def __call__(self, runtime: RuntimeView) -> dict[str, JsonValue]: ...

    class _EvalExpressionCommand(Protocol):
        def __call__(
            self, instance: OdooInstance, expression: str, *, commit: bool = False
        ) -> Command[CommandResult]: ...

    class _ExecScriptCommand(Protocol):
        def __call__(
            self,
            instance: OdooInstance,
            script: str,
            argv: tuple[str, ...] = (),
            *,
            commit: bool = False,
        ) -> Command[CommandResult]: ...

    class _ExportTranslationsCommand(Protocol):
        def __call__(
            self,
            instance: OdooInstance,
            modules: tuple[str, ...],
            languages: tuple[str, ...],
            *,
            worktree_root: Path,
        ) -> Command[list[TranslationExportResult]]: ...

    class _GetCatalogPath(Protocol):
        def __call__(self, *, ensure_exists: bool = True) -> Path: ...

    class _InitProjectCommand(Protocol):
        def __call__(
            self,
            project_path: Path,
            config: ProjectConfig,
            *,
            postgres_allocated: bool,
        ) -> Command[dict[str, JsonValue]]: ...

    class _ListModulesCommand(Protocol):
        def __call__(
            self,
            instance: OdooInstance,
            names: tuple[str, ...] = (),
            *,
            state: str | None = None,
        ) -> Command[CommandResult]: ...

    class _ModuleTestsCommand(Protocol):
        def __call__(
            self,
            instance: OdooInstance,
            spec: OdooTestSpec,
            *,
            http_interface: str,
            http_port: int,
        ) -> Command[tuple[OdooTestResult, str | None]]: ...

    class _ResolveModuleTestSelection(Protocol):
        def __call__(
            self,
            worktree_path: str | Path,
            start_config: StartConfig,
            modules: tuple[str, ...],
            test_tags: str,
        ) -> tuple[_TestSelection, ...]: ...

    class _RunDoctor(Protocol):
        def __call__(
            self,
            client: OdooClient,
            project_path: Path | None,
            *,
            resolved_context: ResolvedContext | None = None,
        ) -> DoctorReport: ...

    class _VerifyDepsCommand(Protocol):
        def __call__(
            self,
            *,
            recorded_python: Path | str,
            worktree_root: Path,
            uv_executable: str | Path = "uv",
            executor: ProcessExecutor | None = None,
        ) -> Command[DepsVerifyResult]: ...

    type _LazyExport = (
        _RichVscodeGenerate
        | _BuildLaunchProfile
        | _EvalExpressionCommand
        | _ExecScriptCommand
        | _ExportTranslationsCommand
        | _GetCatalogPath
        | _InitProjectCommand
        | _ListModulesCommand
        | _ModuleTestsCommand
        | _ResolveModuleTestSelection
        | _RunDoctor
        | _VerifyDepsCommand
    )

    _rich_vscode_generate: _RichVscodeGenerate
    build_launch_profile: _BuildLaunchProfile
    eval_expression_command: _EvalExpressionCommand
    exec_script_command: _ExecScriptCommand
    export_translations_command: _ExportTranslationsCommand
    get_catalog_path: _GetCatalogPath
    init_project_command: _InitProjectCommand
    list_modules_command: _ListModulesCommand
    module_tests_command: _ModuleTestsCommand
    resolve_module_test_selection: _ResolveModuleTestSelection
    run_doctor: _RunDoctor
    verify_deps_command: _VerifyDepsCommand


__all__ = [
    "_ShellCommandFailure",
    "_rich_shell_projection",
    "_rich_vscode_generate",
    "_run_shell_command",
    "_shell_failure",
    "_shell_payload",
    "build_launch_profile",
    "cli",
    "cli_context",
    "eval_expression_command",
    "exec_script_command",
    "export_translations_command",
    "get_catalog_path",
    "init_project_command",
    "list_modules_command",
    "module_tests_command",
    "resolve_module_test_selection",
    "run_doctor",
    "verify_deps_command",
]


_LAZY_EXPORTS: dict[str, str] = {
    "_rich_vscode_generate": "odoo_instance_sdk.commands.cli_parts.callbacks",
    "build_launch_profile": "odoo_instance_sdk.internal.vscode_generate",
    "eval_expression_command": "odoo_instance_sdk.internal.automation",
    "exec_script_command": "odoo_instance_sdk.internal.automation",
    "export_translations_command": "odoo_instance_sdk.internal.automation",
    "get_catalog_path": "odoo_instance_sdk.internal.paths",
    "init_project_command": "odoo_instance_sdk.project_init",
    "list_modules_command": "odoo_instance_sdk.internal.automation",
    "module_tests_command": "odoo_instance_sdk.resources.testing",
    "resolve_module_test_selection": "odoo_instance_sdk.commands.test",
    "run_doctor": "odoo_instance_sdk.internal.doctor",
    "verify_deps_command": "odoo_instance_sdk.resources.deps",
}


def __getattr__(name: str) -> _LazyExport:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return cast("_LazyExport", value)


if __name__ == "__main__":
    cli()
