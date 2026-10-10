from __future__ import annotations

import gc
import io
import json
import time
from collections.abc import Mapping
from pathlib import Path

import msgspec
from click.testing import CliRunner

from odoo_instance_sdk import execution as execution_module
from odoo_instance_sdk.commands.cli_parts.registration import cli
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
    SessionDecision,
    SessionLimits,
    build_registry,
    invoke_local,
    run_approval_session,
)


class ReadRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    value: str


def _context() -> OperationContext:
    return OperationContext(cwd=Path("/private/project"), project_selector="selected")


def _data_code(record: Mapping[str, object]) -> str:
    data = record.get("data")
    assert isinstance(data, Mapping)
    code = data.get("code")
    assert isinstance(code, str)
    return code


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
    fingerprint = first.records[1].get("fingerprint")
    assert isinstance(fingerprint, str)

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

        def callback(context: RunContext[str]) -> str:
            executions.append("ran")
            context.action("write")
            return "done"

        return Command.create(
            ExecutionPlan(
                steps=(
                    ActionStep(step_id="write", action="write", description="write", mutating=True),
                )
            ),
            callback,
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
    assert outcome.records[-1]["event"] == "error"
    assert _data_code(outcome.records[-1]) == "stale_approval"
    assert executions == []


def test_cancel_and_timeout_are_non_execution_terminal_outcomes() -> None:
    executions: list[str] = []

    def build() -> Command[str]:
        private = PreparedAction(
            step_id="write", action="write", description="write", mutating=True
        )

        def callback(context: RunContext[str]) -> str:
            executions.append("ran")
            context.action("write")
            return "done"

        return Command.create(
            ExecutionPlan(
                steps=(
                    ActionStep(step_id="write", action="write", description="write", mutating=True),
                )
            ),
            callback,
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
        def readline(self, size: int = -1) -> str:  # type: ignore[override]
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
    assert timed_out.records[-1]["event"] == "error"
    assert _data_code(timed_out.records[-1]) == "timeout"
    assert executions == []


def test_preview_contains_only_the_redacted_public_plan() -> None:
    private_value = "private-session-secret"
    private = PreparedStep(
        step_id="write",
        argv=("tool", private_value),
        secret_values=(private_value,),
    )
    command: Command[str] = Command.create(
        ExecutionPlan(steps=(private.public_projection(),)),
        lambda context: context.process("write"),
        (private,),
    )
    output = io.StringIO()
    run_approval_session("fixture.write", lambda: command, io.StringIO(), output)

    assert private_value not in output.getvalue()
    assert "<redacted>" in output.getvalue()


def test_session_limits_always_emit_typed_terminal_bounded_output_error() -> None:
    executions: list[str] = []

    def build() -> Command[str]:
        private = PreparedAction(
            step_id="write", action="write", description="write", mutating=True
        )

        def callback(context: RunContext[str]) -> str:
            executions.append("ran")
            context.action("write")
            return "done"

        return Command.create(
            ExecutionPlan(
                steps=(
                    ActionStep(step_id="write", action="write", description="write", mutating=True),
                )
            ),
            callback,
            (private,),
        )

    for limits in (SessionLimits(max_records=3), SessionLimits(max_output_bytes=1)):
        output = io.StringIO()
        outcome = run_approval_session("fixture.write", build, io.StringIO(), output, limits=limits)

        assert outcome.exit_code != 0
        assert outcome.records[-1]["event"] == "error"
        assert _data_code(outcome.records[-1]) == "bounded_output"
        terminal = json.loads(output.getvalue().splitlines()[-1])
        assert isinstance(terminal, Mapping)
        assert _data_code(terminal) == "bounded_output"
    assert executions == []


def test_session_decision_is_strict_and_forbids_unknown_fields() -> None:
    assert SessionDecision(decision="approve", fingerprint="fp")

    def build() -> Command[str]:
        private = PreparedAction(
            step_id="write", action="write", description="write", mutating=True
        )

        def callback(context: RunContext[str]) -> str:
            context.action("write")
            return "done"

        return Command.create(
            ExecutionPlan(
                steps=(
                    ActionStep(step_id="write", action="write", description="write", mutating=True),
                )
            ),
            callback,
            (private,),
        )

    for raw in (
        '{"event":"approve","fingerprint":"fp"}\n',
        '{"decision":"cancel","unknown":true}\n',
        '{"decision":"maybe"}\n',
    ):
        outcome = run_approval_session("fixture.write", build, io.StringIO(raw), io.StringIO())
        assert outcome.exit_code != 0
        assert outcome.records[-1]["event"] == "error"
        assert _data_code(outcome.records[-1]) == "invalid_sequence"


def test_session_cli_rejects_ineligible_operation_with_jsonl_error_without_prompt() -> None:
    result = CliRunner().invoke(cli, ["operation", "session", "odcli.git.check"])

    assert result.exit_code == 1
    assert result.stderr == ""
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 1
    assert records[0]["event"] == "error"
    assert _data_code(records[0]) == "session_transport_required"
    assert "schema_version" not in records[0]


def test_session_eof_and_input_limits_are_typed_terminal_errors() -> None:
    def build() -> Command[str]:
        return Command.create(
            ExecutionPlan(
                steps=(
                    ActionStep(step_id="write", action="write", description="write", mutating=True),
                )
            ),
            lambda context: "done",
            (PreparedAction(step_id="write", action="write", description="write", mutating=True),),
        )

    cases = (
        (io.StringIO(), SessionLimits()),
        (io.StringIO('{"decision":"approve"}\n'), SessionLimits(max_input_bytes=4)),
    )
    for input_stream, limits in cases:
        output = io.StringIO()
        outcome = run_approval_session("fixture.write", build, input_stream, output, limits=limits)
        assert outcome.exit_code == 1
        assert outcome.records[-1]["event"] == "error"
        assert _data_code(outcome.records[-1]) in {"eof", "input_limit"}
        terminal = json.loads(output.getvalue().splitlines()[-1])
        assert terminal == outcome.records[-1]


def test_session_interrupt_cancels_and_drops_private_command_snapshot() -> None:
    command_id: int | None = None

    def build() -> Command[str]:
        nonlocal command_id

        def interrupt(context: object) -> str:
            raise KeyboardInterrupt

        command = Command.create(
            ExecutionPlan(
                steps=(
                    ActionStep(step_id="write", action="write", description="write", mutating=True),
                )
            ),
            interrupt,
            (PreparedAction(step_id="write", action="write", description="write", mutating=True),),
        )
        command_id = id(command)
        return command

    first = run_approval_session("fixture.write", build, io.StringIO(), io.StringIO())
    assert first.exit_code == 1
    assert _data_code(first.records[-1]) == "eof"

    output = io.StringIO()
    fingerprint = first.records[1].get("fingerprint")
    assert isinstance(fingerprint, str)
    second = run_approval_session(
        "fixture.write",
        build,
        io.StringIO(json.dumps({"decision": "approve", "fingerprint": fingerprint}) + "\n"),
        output,
    )
    assert second.exit_code == 130
    assert second.records[-1]["event"] == "cancelled"
    assert command_id is not None
    gc.collect()
    assert command_id not in execution_module._COMMANDS


def test_builtin_read_and_mutation_bindings_keep_sdk_primitive_delegation() -> None:
    registry = build_registry()
    read = registry.get("odcli.git.check")
    mutation = registry.get("odcli.stop")

    assert read.descriptor.canonical_path == ("git", "check")
    assert read.factory is not None
    assert read.sdk_primitive == "GitResource.check_command"
    assert mutation.descriptor.approval_required is True
    assert mutation.factory is not None
    assert mutation.sdk_primitive == "OdooInstance.stop_runtime_command"
