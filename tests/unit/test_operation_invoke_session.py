from __future__ import annotations

import io
import json
import time
from pathlib import Path

import msgspec

from odoo_instance_sdk.commands.context import OperationContext
from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    RecordingExecutor,
    RunContext,
)
from odoo_instance_sdk.operations import (
    OperationBinding,
    OperationDescriptor,
    OperationResult,
    OperationTransport,
    SessionLimits,
    build_registry,
    invoke_local,
    run_approval_session,
)


class ReadRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    value: str


def _context() -> OperationContext:
    return OperationContext(cwd=Path("/private/project"), project_selector="selected")


def _binding(factory: object, *, approval: bool = False) -> OperationBinding:
    return OperationBinding(
        OperationDescriptor(
            operation_id="fixture.read",
            canonical_path=("fixture", "read"),
            request_type=ReadRequest,
            result_type=OperationResult,
            error_types=(),
            transport=OperationTransport.DOCUMENT,
            preview=approval,
            approval_required=approval,
        ),
        factory=factory,  # type: ignore[arg-type]
    )


def test_local_invoke_decodes_typed_request_and_preserves_private_context() -> None:
    seen: list[tuple[str, str | None]] = []

    def factory(request: ReadRequest, context: OperationContext) -> dict[str, str]:
        seen.append((request.value, context.project))
        return {"status": "negative-domain-result", "value": request.value}

    result = invoke_local(
        "fixture.read",
        {"value": "ok"},
        _context(),
        registry=build_registry((_binding(factory),)),
    )

    assert result.exit_code == 0
    assert result.document.ok is True
    assert result.value == {"status": "negative-domain-result", "value": "ok"}
    assert seen == [("ok", "selected")]
    assert "/private/project" not in json.dumps(result.document.context)


def test_approval_session_runs_the_same_command_after_matching_fingerprint() -> None:
    executions: list[str] = []
    built: list[Command[dict[str, str]]] = []

    def build() -> Command[dict[str, str]]:
        private = PreparedAction(
            step_id="write", action="write", description="write", mutating=True
        )

        def callback(context: RunContext[dict[str, str]]) -> dict[str, str]:
            executions.append("ran")
            context.action("write")
            context.complete_action("write")
            return {"status": "done"}

        command = Command.create(
            ExecutionPlan(
                steps=(
                    ActionStep(step_id="write", action="write", description="write", mutating=True),
                )
            ),
            callback,
            (private,),
            executor=RecordingExecutor(),
        )
        built.append(command)
        return command

    preview = io.StringIO()
    first = run_approval_session("fixture.write", build, io.StringIO(""), preview)
    assert first.exit_code == 1
    assert executions == []
    fingerprint = first.records[1]["fingerprint"]

    approved = io.StringIO(json.dumps({"decision": "approve", "fingerprint": fingerprint}) + "\n")
    output = io.StringIO()
    second = run_approval_session("fixture.write", lambda: built[0], approved, output)

    assert second.exit_code == 0
    assert executions == ["ran"]
    assert [record["event"] for record in second.records][:3] == [
        "accepted",
        "preview",
        "approval_required",
    ]
    assert second.records[-1]["event"] == "result"


def test_approval_session_rejects_stale_decision_without_execution() -> None:
    executions: list[str] = []

    def build() -> Command[str]:
        private = PreparedAction(
            step_id="write", action="write", description="write", mutating=True
        )
        return Command.create(
            ExecutionPlan(
                steps=(
                    ActionStep(step_id="write", action="write", description="write", mutating=True),
                )
            ),
            lambda context: (executions.append("ran"), context.action("write"), "done")[2],
            (private,),
        )

    output = io.StringIO()
    outcome = run_approval_session(
        "fixture.write",
        build,
        io.StringIO(json.dumps({"decision": "approve", "fingerprint": "stale"}) + "\n"),
        output,
    )

    assert outcome.exit_code == 1
    assert outcome.records[-1]["event"] == "stale_approval"
    assert executions == []


def test_cancel_and_timeout_are_non_execution_terminal_outcomes() -> None:
    executions: list[str] = []

    def build() -> Command[str]:
        private = PreparedAction(
            step_id="write", action="write", description="write", mutating=True
        )
        return Command.create(
            ExecutionPlan(
                steps=(
                    ActionStep(step_id="write", action="write", description="write", mutating=True),
                )
            ),
            lambda context: (executions.append("ran"), context.action("write"), "done")[2],
            (private,),
        )

    cancelled = run_approval_session(
        "fixture.write",
        build,
        io.StringIO('{"decision":"cancel"}\n'),
        io.StringIO(),
    )
    assert cancelled.exit_code == 0
    assert cancelled.records[-1]["event"] == "cancelled"

    class SlowInput(io.StringIO):
        def readline(self, size: int = -1) -> str:
            time.sleep(0.02)
            return super().readline(size)

    timed_out = run_approval_session(
        "fixture.write",
        build,
        SlowInput('{"decision":"approve"}\n'),
        io.StringIO(),
        limits=SessionLimits(decision_timeout=0.001),
    )
    assert timed_out.exit_code == 1
    assert timed_out.records[-1]["event"] == "timeout"
    assert executions == []


def test_preview_contains_only_the_redacted_public_plan() -> None:
    private_value = "private-session-secret"
    private = PreparedStep(
        step_id="write",
        argv=("tool", private_value),
        secret_values=(private_value,),
    )
    command = Command.create(
        ExecutionPlan(steps=(private.public_projection(),)),
        lambda context: context.process("write"),
        (private,),
    )
    output = io.StringIO()
    run_approval_session("fixture.write", lambda: command, io.StringIO(), output)

    assert private_value not in output.getvalue()
    assert "<redacted>" in output.getvalue()
