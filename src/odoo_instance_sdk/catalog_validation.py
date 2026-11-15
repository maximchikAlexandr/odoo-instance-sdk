"""Optional msgfmt check captured as one immutable command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import msgspec

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.executables import resolve_optional_executable

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import RunContext


class CatalogValidation(msgspec.Struct, frozen=True, kw_only=True):
    """Bounded result of one optional catalog check."""

    tool: str
    status: str
    output: str
    returncode: int | None


def catalog_validation_command(path: Path) -> Command[CatalogValidation]:
    """Capture ``msgfmt --check`` for one file, or a skipped action when it is absent."""
    from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan, action_command
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, SubprocessExecutor

    tool = resolve_optional_executable("msgfmt")
    if tool.path is None:
        skipped = CatalogValidation(tool="msgfmt", status="skipped", output="", returncode=None)

        def result() -> CatalogValidation:
            return skipped

        return action_command(
            (
                ActionStep(
                    step_id="translations.msgfmt.absent",
                    action="msgfmt.absent",
                    description="msgfmt is not installed; catalog validation is skipped",
                    details={"tool": "msgfmt", "path": path.name},
                    read_only=True,
                ),
            ),
            (lambda: None,),
            result,
        )
    step = PreparedStep(
        step_id="translations.msgfmt",
        argv=(tool.path, "--check", "--statistics", "-o", "/dev/null", str(path)),
        environment=(("LC_ALL", "C"),),
        timeout=30.0,
        read_only=True,
        text=False,
    )

    def callback(context: RunContext[CatalogValidation]) -> CatalogValidation:
        completed = context.process(step.step_id)
        if not isinstance(completed, ProcessResult):
            raise ConfigError("msgfmt validation returned no process result")
        output = _bounded(_decode(completed.stdout) + _decode(completed.stderr))
        if completed.returncode != 0:
            raise ConfigError(
                f"msgfmt validation failed (rc={completed.returncode}): {output or '<no diagnostic>'}"
            )
        return CatalogValidation(
            tool="msgfmt",
            status="passed",
            output=output,
            returncode=completed.returncode,
        )

    plan = ExecutionPlan(steps=(step.public_projection(),)).with_fingerprint()
    return Command.create(plan, callback, (step,), executor=SubprocessExecutor())


def _decode(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _bounded(value: str, *, limit: int = 8192) -> str:
    if len(value) <= limit:
        return value
    marker = "\n[output truncated]"
    return f"{value[: limit - len(marker)]}{marker}"
