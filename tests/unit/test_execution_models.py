from __future__ import annotations

from collections.abc import Callable
from threading import Barrier, Thread
from typing import cast

import msgspec
import pytest

from odoo_instance_sdk import (
    ActionStep,
    Command,
    ExecutionPlan,
    JsonValue,
    ProcessStep,
    canonical_plan_bytes,
    canonical_plan_projection,
    fingerprint_plan,
)
from odoo_instance_sdk.exceptions import (
    DuplicateStepError,
    OmittedStepError,
    StalePlanError,
    UnplannedStepError,
)
from odoo_instance_sdk.internal.proc import PreparedStep, RecordingExecutor, RunContext
from odoo_instance_sdk.internal.proc.redaction import (
    REDACTION_MARKER,
    redacted_argv,
    redacted_projection,
)


def _prepared() -> PreparedStep:
    return PreparedStep(
        step_id="child",
        argv=("tool", "--password=super secret", "quoted value"),
        cwd="/work tree",
        environment=(("TOKEN", "super secret"), ("LANG", "C")),
        environment_overrides=(("TOKEN", "super secret"), ("LANG", "C")),
        stdin=b"password: super secret\nprint('quoted value')",
        timeout=2.5,
        mode="captured",
        secret_values=("super secret",),
        read_only=True,
    )


def test_public_projection_is_frozen_json_safe_and_keeps_argv_boundaries() -> None:
    step = _prepared()
    public = step.public_projection()

    assert isinstance(public, ProcessStep)
    assert public.argv == ("tool", f"--password={REDACTION_MARKER}", "quoted value")
    assert public.environment_overrides == (("TOKEN", REDACTION_MARKER), ("LANG", "C"))
    assert REDACTION_MARKER in (public.input_preview or "")
    assert "super secret" not in repr(public)
    assert msgspec.json.decode(msgspec.json.encode(public), type=ProcessStep) == public
    with pytest.raises(AttributeError):
        public.argv = ("changed",)  # type: ignore[misc]


def test_public_projection_separates_inherited_environment_and_redacts_argv_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INHERITED_PRIVATE_MARKER", "ambient-secret")
    step = PreparedStep(
        step_id="secure",
        argv=(
            "tool",
            "--token",
            "token-value",
            "--password=passwd-value",
            "https://user:uri-password@example.test/path",
        ),
        environment=(
            ("DATABASE_URL", "postgresql://db-user:db-password@example.test/app"),
            ("INHERITED_PRIVATE_MARKER", "ambient-secret"),
            ("SAFE_OVERRIDE", "safe"),
        ),
        environment_snapshot=(
            ("DATABASE_URL", "postgresql://db-user:db-password@example.test/app"),
            ("INHERITED_PRIVATE_MARKER", "ambient-secret"),
        ),
        environment_overrides=(
            ("DATABASE_URL", "postgresql://db-user:db-password@example.test/app"),
            ("SAFE_OVERRIDE", "safe"),
        ),
        secret_values=("token-value", "passwd-value", "uri-password", "db-password"),
    )

    public = step.public_projection()

    assert public.argv == (
        "tool",
        "--token",
        REDACTION_MARKER,
        f"--password={REDACTION_MARKER}",
        f"https://{REDACTION_MARKER}@example.test/path",
    )
    assert public.environment_overrides == (
        ("DATABASE_URL", REDACTION_MARKER),
        ("SAFE_OVERRIDE", REDACTION_MARKER),
    )
    encoded = msgspec.json.encode(public)
    assert b"ambient-secret" not in encoded
    assert b"db-password" not in encoded
    assert redacted_argv(("--password", "passwd-value"), secrets=("passwd-value",)) == (
        "--password",
        REDACTION_MARKER,
    )


def test_plan_serializes_action_and_process_values_without_private_snapshot() -> None:
    step = _prepared()
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


def test_canonical_fingerprint_sorts_mapping_keys_but_preserves_step_order() -> None:
    process = ProcessStep(
        step_id="one", argv=("tool", "value"), display="tool value", executable="tool"
    )
    first = ExecutionPlan(
        steps=(process, ActionStep(step_id="two", action="write", description="Write")),
        observations=({"b": 2, "a": [True, None]},),
    )
    reordered_mapping = ExecutionPlan(
        steps=(process, ActionStep(step_id="two", action="write", description="Write")),
        observations=({"a": [True, None], "b": 2},),
    )
    reordered_steps = ExecutionPlan(
        steps=(ActionStep(step_id="two", action="write", description="Write"), process),
        observations=({"a": [True, None], "b": 2},),
    )

    assert fingerprint_plan(first) == fingerprint_plan(reordered_mapping)
    assert fingerprint_plan(first) != fingerprint_plan(reordered_steps)
    assert b"fingerprint" not in canonical_plan_bytes(first)
    projection = canonical_plan_projection(first)
    assert isinstance(projection, dict)
    assert "fingerprint" not in projection


def test_fingerprint_uses_redacted_projection_and_ignores_private_secret_values() -> None:
    first_private = PreparedStep(step_id="secret", argv=("tool", "alpha"), secret_values=("alpha",))
    second_private = PreparedStep(step_id="secret", argv=("tool", "beta"), secret_values=("beta",))
    first_plan = ExecutionPlan(steps=(first_private.public_projection(),))
    second_plan = ExecutionPlan(steps=(second_private.public_projection(),))

    assert first_plan.steps == second_plan.steps
    assert fingerprint_plan(first_plan, secrets=("alpha",)) == fingerprint_plan(
        second_plan, secrets=("beta",)
    )
    assert "alpha" not in canonical_plan_bytes(first_plan, secrets=("alpha",)).decode()
    assert "beta" not in canonical_plan_bytes(second_plan, secrets=("beta",)).decode()


def test_command_repeat_runs_use_independent_ledgers_and_safe_repr() -> None:
    executor = RecordingExecutor(result_factory=lambda step: step.argv[-1])
    private = _prepared()
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
    assert len(executor.executed) == 2
    assert "super secret" not in repr(command)
    encoded = msgspec.to_builtins(command)
    assert encoded == {"plan": msgspec.to_builtins(command.plan)}
    assert "_prepared" not in repr(encoded)


def test_concurrent_command_runs_use_independent_ledgers() -> None:
    executor = RecordingExecutor(result_factory=lambda step: step.argv[-1])
    private = _prepared()
    barrier = Barrier(2)

    def callback(context: RunContext[str]) -> str:
        barrier.wait(timeout=2)
        return context.process("child")

    command: Command[str] = Command.create(
        ExecutionPlan(steps=(private.public_projection(),)),
        callback,
        (private,),
        executor=executor,
    )
    results: list[str] = []
    threads = [Thread(target=lambda: results.append(command.run())) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert results == ["quoted value", "quoted value"]
    assert len(executor.executed) == 2


def test_command_snapshot_does_not_follow_mutated_inputs_after_construction() -> None:
    executor = RecordingExecutor(result_factory=lambda step: step.argv[-1])
    argv = ["tool", "before"]
    private = PreparedStep(step_id="child", argv=tuple(argv))
    public = private.public_projection()
    command: Command[str] = Command.create(
        ExecutionPlan(steps=(public,)),
        lambda context: context.process("child"),
        (private,),
        executor=executor,
    )
    argv[1] = "after"

    assert command.plan.steps == (public,)
    assert command.run() == "before"
    assert [step.argv for step in executor.executed] == [("tool", "before")]


def test_unplanned_or_duplicate_requests_do_not_launch_requested_child() -> None:
    executor = RecordingExecutor(result_factory=lambda step: step.argv[-1])
    private = PreparedStep(step_id="child", argv=("tool", "value"))
    plan = ExecutionPlan(
        steps=(
            ProcessStep(
                step_id="child", argv=("tool", "value"), display="tool value", executable="tool"
            ),
        )
    )
    unplanned: Command[str] = Command.create(
        plan,
        cast("Callable[[RunContext[str]], str]", lambda context: context.process("substituted")),
        (private,),
        executor=executor,
    )
    with pytest.raises(UnplannedStepError):
        unplanned.run()
    assert executor.executed == []

    duplicate: Command[str] = Command.create(
        plan,
        cast(
            "Callable[[RunContext[str]], str]",
            lambda context: (context.process("child"), context.process("child")),
        ),
        (private,),
        executor=executor,
    )
    with pytest.raises(DuplicateStepError):
        duplicate.run()
    assert [step.argv for step in executor.executed] == [("tool", "value")]


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
