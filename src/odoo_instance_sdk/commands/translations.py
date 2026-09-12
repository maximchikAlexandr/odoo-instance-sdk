"""CLI registration for translation export."""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

if TYPE_CHECKING:
    import click

    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        ProcessHandle,
        ProcessResultLike,
        RunContext,
        StepObserver,
    )
    from odoo_instance_sdk.models import CommandResult
    from odoo_instance_sdk.resources.instance import OdooInstance
else:
    import rich_click as click
from rich.console import Console
from rich.table import Table

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    fail,
    output_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.internal.cli_format import human_bytes as _human_bytes
from odoo_instance_sdk.internal.cli_format import rich_cell


@dataclass(frozen=True, slots=True)
class TranslationValidationResult:
    """Bounded output from the optional GNU translation validator."""

    tool: str
    status: Literal["passed", "failed"]
    output: str
    returncode: int


@dataclass(frozen=True, slots=True)
class TranslationExportResult:
    """One published translation file and its optional validation evidence."""

    module: str
    requested_lang: str
    actual_filename: str
    path: Path
    bytes_written: int
    validation: TranslationValidationResult | None = None


class _TranslationCommandBuilder(Protocol):
    def __call__(
        self,
        instance: OdooInstance,
        modules: tuple[str, ...],
        languages: tuple[str, ...],
        *,
        worktree_root: Path,
    ) -> Command[list[TranslationExportResult]]: ...


@dataclass(slots=True)
class _TranslationValidationExecutor:
    """Bind generated PO bytes at the existing process boundary."""

    delegate: ProcessExecutor
    inputs: dict[str, bytes]

    def bind(self, step_id: str, content: bytes) -> None:
        self.inputs[step_id] = content

    def execute(
        self,
        step: PreparedStep,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResultLike:
        from dataclasses import replace

        content = self.inputs.pop(step.step_id, None)
        if content is not None:
            step = replace(step, stdin=content)
        return self.delegate.execute(step, observer=observer, observe_output=observe_output)

    def spawn(
        self,
        step: PreparedStep,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessHandle:
        return self.delegate.spawn(step, observer=observer, observe_output=observe_output)


def export_translations(
    instance: OdooInstance,
    modules: tuple[str, ...],
    languages: tuple[str, ...],
    *,
    worktree_root: Path,
) -> list[TranslationExportResult]:
    return export_translations_command(
        instance,
        modules,
        languages,
        worktree_root=worktree_root,
    ).run()


def export_translations_command(  # noqa: C901
    instance: OdooInstance,
    modules: tuple[str, ...],
    languages: tuple[str, ...],
    *,
    worktree_root: Path,
) -> Command[list[TranslationExportResult]]:
    """Capture export and optional validation in one inspectable command."""
    if not modules or not languages:
        from odoo_instance_sdk.exceptions import ConfigError

        raise ConfigError("translations export requires --module and --language")
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.automation import _build_export_source, _finalize_export
    from odoo_instance_sdk.internal.executables import resolve_optional_executable
    from odoo_instance_sdk.internal.proc import PreparedStep

    chunks = [
        _build_export_source(module, language) for module in modules for language in languages
    ]
    source = "_odcli_exports = []\n"
    for index, chunk in enumerate(chunks):
        if index:
            source += "del result\n"
        source += chunk
        source += "_odcli_exports.append(result)\n"
    source += "result = _odcli_exports\n"

    msgfmt = resolve_optional_executable("msgfmt")
    validation_steps = (
        tuple(
            PreparedStep(
                step_id=f"translations.msgfmt.{index}",
                argv=(msgfmt.path, "--check", "--statistics", "-o", "/dev/null", "-"),
                environment=(("LC_ALL", "C"),),
                public_input_preview="<generated PO>",
                timeout=30.0,
                read_only=True,
                text=False,
            )
            for index in range(len(chunks))
        )
        if msgfmt.path is not None
        else ()
    )
    shell_command: Command[CommandResult] = instance._shell_script_command(
        source, commit=False, exclusive=False
    )
    shell = shell_command._prepared()
    validation_executor = _TranslationValidationExecutor(shell.executor, {})
    captured_steps: list[PreparedStep | PreparedAction] = []
    inserted = False
    for step in shell.steps:
        captured_steps.append(step)
        if isinstance(step, PreparedStep) and step.step_id == "instance.shell_script":
            captured_steps.extend(validation_steps)
            inserted = True
    if not inserted:
        raise RuntimeError("translation export shell step was not captured")

    def convert(
        result: CommandResult, context: RunContext[list[TranslationExportResult]]
    ) -> list[TranslationExportResult]:
        from odoo_instance_sdk.internal.server import parse_payload

        payload = parse_payload(result.stdout)
        raw_results = payload.get("result") if payload is not None else None
        if not isinstance(raw_results, list):
            from odoo_instance_sdk.exceptions import ConfigError

            raise ConfigError("translations export produced no payload")
        addons_paths = (
            instance.config.start_config.addons_path
            if instance.config.start_config is not None
            else None
        )
        pairs = tuple(
            zip(
                ((module, language) for module in modules for language in languages),
                raw_results,
                strict=True,
            )
        )
        validations: list[TranslationValidationResult | None] = []
        for index, ((module, language), raw) in enumerate(pairs):
            if not validation_steps:
                validations.append(None)
                continue
            raw_data = raw.get("data") if isinstance(raw, dict) else None
            content = _decode_translation_payload(
                raw_data if isinstance(raw_data, str) else "", module, language
            )
            validation = _run_msgfmt_validation(
                cast("RunContext[JsonValue]", context),
                validation_executor,
                validation_steps[index],
                content,
            )
            validations.append(validation)

        exports: list[TranslationExportResult] = []
        for ((module, language), raw), captured_validation in zip(pairs, validations, strict=True):
            published = _finalize_export(
                module,
                language,
                raw if isinstance(raw, dict) else {},
                worktree_root=worktree_root,
                addons_paths=addons_paths,
            )
            exports.append(
                TranslationExportResult(
                    module=published.module,
                    requested_lang=published.requested_lang,
                    actual_filename=published.actual_filename,
                    path=published.path,
                    bytes_written=published.bytes_written,
                    validation=captured_validation,
                )
            )
        return exports

    def execute(
        context: RunContext[list[TranslationExportResult]],
    ) -> list[TranslationExportResult]:
        result = shell.callback(cast("RunContext[CommandResult]", context))
        return convert(result, context)

    plan_steps = tuple(step.public_projection() for step in captured_steps)
    plan = ExecutionPlan(
        steps=plan_steps,
        observations=shell_command.plan.observations,
        warnings=shell_command.plan.warnings,
    ).with_fingerprint()
    return Command.create(
        plan,
        execute,
        tuple(captured_steps),
        executor=validation_executor,
        private_projection=shell.private_projection,
    )


def _decode_translation_payload(data_b64: str, module: str, lang: str) -> bytes:
    from odoo_instance_sdk.exceptions import ConfigError

    if not data_b64:
        raise ConfigError(f"translations export produced empty payload for {module}/{lang}")
    try:
        content = base64.b64decode(data_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"translations export produced invalid base64 for {module}/{lang}"
        ) from exc
    if not content:
        raise ConfigError(f"translations export produced empty payload for {module}/{lang}")
    return content


def _run_msgfmt_validation(
    context: RunContext[JsonValue],
    executor: _TranslationValidationExecutor,
    step: PreparedStep,
    content: bytes,
) -> TranslationValidationResult:
    from odoo_instance_sdk.exceptions import ConfigError
    from odoo_instance_sdk.internal.proc import ProcessResult

    executor.bind(step.step_id, content)
    result = context.process(step.step_id)
    if not isinstance(result, ProcessResult):
        raise ConfigError("msgfmt validation returned no process result")
    stdout = (
        result.stdout.decode(errors="replace")
        if isinstance(result.stdout, bytes)
        else result.stdout
    )
    stderr = (
        result.stderr.decode(errors="replace")
        if isinstance(result.stderr, bytes)
        else result.stderr
    )
    output = _bounded_validation_output(f"{stdout}{stderr}")
    validation = TranslationValidationResult(
        tool="msgfmt",
        status="passed" if result.returncode == 0 else "failed",
        output=output,
        returncode=result.returncode,
    )
    if result.returncode != 0:
        detail = output or "<no diagnostic>"
        raise ConfigError(f"msgfmt validation failed (rc={result.returncode}): {detail}")
    return validation


def _bounded_validation_output(value: str, *, limit: int = 8192) -> str:
    if len(value) <= limit:
        return value
    marker = "\n[output truncated]"
    return f"{value[: limit - len(marker)]}{marker}"


def _translation_command_builder() -> _TranslationCommandBuilder:
    """Keep the extracted CLI seam patchable while selecting the owned builder."""
    from odoo_instance_sdk.internal.automation import (
        export_translations_command as legacy_builder,
    )

    builder = getattr(sys.modules["odoo_instance_sdk.cli"], "export_translations_command", None)
    if builder is legacy_builder or not callable(builder):
        return export_translations_command
    return cast("_TranslationCommandBuilder", builder)


def _rich_translation_export(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    exports = result.get("exports", [])
    table = Table("Module", "Language", "File", "Size", "Validation", title="Translation export")
    validation_output: list[tuple[str, str, str]] = []
    if isinstance(exports, list) and exports:
        for item in exports:
            if not isinstance(item, dict):
                continue
            size = item.get("bytes_written")
            validation = item.get("validation")
            validation_label = (
                validation.get("status", "unknown") if isinstance(validation, dict) else "not run"
            )
            if isinstance(validation, dict) and validation.get("output"):
                validation_output.append(
                    (
                        str(validation.get("tool", "msgfmt")),
                        str(validation.get("status", "unknown")),
                        str(validation["output"]),
                    )
                )
            table.add_row(
                rich_cell(item.get("module", "")),
                rich_cell(item.get("requested_lang", "")),
                rich_cell(item.get("actual_filename", "")),
                rich_cell(
                    _human_bytes(size)
                    if isinstance(size, int) and not isinstance(size, bool)
                    else "—"
                ),
                rich_cell(validation_label),
            )
    else:
        table.add_row("(none)", "—", "—", "—", "—")
    output = StringIO()
    console = Console(file=output, color_system=None, width=180)
    console.print(table)
    if validation_output:
        details = Table("Tool", "Status", "Output", title="Translation validation")
        for tool, status, diagnostic in validation_output:
            details.add_row(rich_cell(tool), rich_cell(status), rich_cell(diagnostic))
        console.print(details)
    return output.getvalue().rstrip()


@click.group("translations", help="Export Odoo module translations.")
def _translations_group() -> None:
    pass


@_translations_group.command("export", help="Export selected module translations.")
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
        status, _results = run_or_preview(
            lambda: _translation_command_builder()(
                runtime_context.instance,
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
                        **(
                            {
                                "validation": {
                                    "tool": item.validation.tool,
                                    "status": item.validation.status,
                                    "output": item.validation.output,
                                    "returncode": item.validation.returncode,
                                }
                            }
                            if item.validation is not None
                            else {}
                        ),
                    }
                    for item in cast("list[TranslationExportResult]", value or [])
                ]
            },
            rich=_rich_translation_export,
            progress=True,
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "translations.export", e, dry_run=dry_run)
    raise click.exceptions.Exit(status)


def register_translation_commands(group: click.Group) -> None:
    """Attach translation commands through the shared CLI seam."""
    group.add_command(_translations_group)


__all__ = [
    "TranslationExportResult",
    "TranslationValidationResult",
    "export_translations",
    "export_translations_command",
    "register_translation_commands",
]
