"""Tests for appending steps to one captured command snapshot."""

from __future__ import annotations

from typing import cast

import pytest

from odoo_instance_sdk.execution import (
    Command,
    ExecutionPlan,
    ExternalEffects,
    ProcessStep,
    continue_command,
)
from odoo_instance_sdk.internal.proc import (
    ProcessResult,
    RunContext,
    SubprocessExecutor,
    prepared_command,
    prepared_step,
)


@pytest.mark.unit
def test_continue_command_runs_the_base_snapshot_then_appended_steps() -> None:
    base_step = prepared_step("/bin/echo", ["base"], step_id="base", read_only=True)

    def run_base(context: RunContext[str]) -> str:
        output = cast("ProcessResult", context.process("base"))
        return str(output.stdout).strip()

    base = Command.from_prepared(
        ExecutionPlan(steps=(base_step.public_projection(),)).with_fingerprint(),
        prepared_command(run_base, (base_step,), executor=SubprocessExecutor()),
    )
    extra = ProcessStep(
        step_id="extra",
        argv=("/bin/echo", "extra"),
        display="/bin/echo extra",
        executable="/bin/echo",
        read_only=True,
    )

    def run_extra(base_output: str, effects: ExternalEffects) -> tuple[str, str]:
        output = effects.process("extra")
        return base_output, output.stdout.strip()

    command = continue_command(base, (extra,), run_extra)
    assert [step.step_id for step in command.plan.steps] == ["base", "extra"]
    assert command.run() == ("base", "extra")
