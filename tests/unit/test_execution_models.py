from __future__ import annotations

from collections.abc import Callable
from typing import cast

import msgspec
import pytest

from odoo_instance_sdk import ActionStep, Command, ExecutionPlan, JsonValue, ProcessStep
from odoo_instance_sdk.exceptions import (
    DuplicateStepError,
    OmittedStepError,
    StalePlanError,
    UnplannedStepError,
)
from odoo_instance_sdk.internal.proc import PreparedProcess, PreparedStep, RunContext
from odoo_instance_sdk.internal.proc.redaction import REDACTION_MARKER, redacted_projection


class RecordingExecutor:
    def __init__(self) -> None:
        self.argvs: list[tuple[str, ...]] = []

    def execute(self, step: PreparedProcess) -> str:
        self.argvs.append(step.argv)
        return step.argv[-1]


def _prepared(executor: RecordingExecutor) -> PreparedStep:
    return PreparedStep(
        step_id="child",
        argv=("tool", "--password=super secret", "quoted value"),
        cwd="/work tree",
        environment=(("TOKEN", "super secret"), ("LANG", "C")),
        stdin=b"password: super secret\nprint('quoted value')",
        timeout=2.5,
        mode="captured",
        secret_values=("super secret",),
        read_only=True,
    )


def test_public_projection_is_frozen_json_safe_and_keeps_argv_boundaries() -> None:
    step = _prepared(RecordingExecutor())
    public = step.public_projection()

    assert isinstance(public, ProcessStep)
    assert public.argv == ("tool", f"--password={REDACTION_MARKER}", "quoted value")
    assert public.environment_overrides == (("TOKEN", REDACTION_MARKER), ("LANG", "C"))
    assert REDACTION_MARKER in (public.input_preview or "")
    assert "super secret" not in repr(public)
    assert msgspec.json.decode(msgspec.json.encode(public), type=ProcessStep) == public
    with pytest.raises(AttributeError):
        public.argv = ("changed",)  # type: ignore[misc]


def test_plan_serializes_action_and_process_values_without_private_snapshot() -> None:
    step = _prepared(RecordingExecutor())
    nested: JsonValue = {"probe": [True, {"revision": "abc"}, None]}
    plan = ExecutionPlan(
        steps=(
            step.public_projection(),
            ActionStep(
                step_id="lock",
                action="acquire-lock",
                description="Acquire the project lock",
                mutating=True,
            ),
        ),
        observations=(nested,),
        warnings=("safe warning",),
    )
    encoded = msgspec.json.encode(plan)
    assert b"super secret" not in encoded
    assert msgspec.to_builtins(plan)["steps"]


def test_stale_plan_error_has_typed_json_fields() -> None:
    error = StalePlanError(
        expected={"head": "abc"},
        actual={"head": "def"},
    )

    assert error.code == "stale_plan"
    assert error.details == {"expected": {"head": "abc"}, "actual": {"head": "def"}}


def test_command_repeat_runs_use_independent_ledgers_and_safe_repr() -> None:
    executor = RecordingExecutor()
    private = _prepared(executor)
    public = private.public_projection()

    command: Command[str] = Command.create(
        ExecutionPlan(steps=(public,)),
        lambda context: context.process("child"),
        (private,),
        executor=executor,
    )

    assert command.commands == (public,)
    assert command.run() == "quoted value"
    assert command.run() == "quoted value"
    assert len(executor.argvs) == 2
    assert "super secret" not in repr(command)
    encoded = msgspec.to_builtins(command)
    assert encoded == {"plan": msgspec.to_builtins(command.plan)}
    assert "_prepared" not in repr(encoded)


@pytest.mark.parametrize(
    ("callback", "error"),
    [
        (lambda context: context.process("missing"), UnplannedStepError),
        (lambda context: (context.process("child"), context.process("child")), DuplicateStepError),
        (lambda context: "finished", OmittedStepError),
    ],
)
def test_command_ledger_rejects_unplanned_duplicate_and_omitted_steps(
    callback: object, error: type[Exception]
) -> None:
    private = PreparedStep(step_id="child", argv=("tool", "value"))
    command: Command[str] = Command.create(
        ExecutionPlan(
            steps=(
                ProcessStep(
                    step_id="child", argv=("tool", "value"), display="tool value", executable="tool"
                ),
            )
        ),
        cast("Callable[[RunContext[str]], str]", callback),
        (private,),
    )

    with pytest.raises(error):
        command.run()


def test_redaction_is_recursive_for_fields_and_preserves_list_boundaries() -> None:
    projected = redacted_projection(
        {
            "argv": ["--token", "private value", "plain"],
            "environment": {"API_TOKEN": "private value", "SAFE": "plain"},
            "stdin": "token=private value\nplain",
            "warnings": ["private value"],
            "error": "private value",
        },
        secrets=("private value",),
    )
    assert projected == {
        "argv": ["--token", REDACTION_MARKER, "plain"],
        "environment": {"API_TOKEN": REDACTION_MARKER, "SAFE": "plain"},
        "stdin": f"token={REDACTION_MARKER}\nplain",
        "warnings": [REDACTION_MARKER],
        "error": REDACTION_MARKER,
    }
