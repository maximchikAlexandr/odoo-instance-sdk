"""Public, inspectable command and execution-plan models.

The objects in this module are deliberately a projection of execution.  The
exact executable inputs live in :mod:`odoo_instance_sdk.internal.proc` and
never become part of a public model or representation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Generic, Literal, Protocol, TypeVar, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    DuplicateStepError,
    OmittedStepError,
    PlanError,
    PlanValidationError,
    StalePlanError,
    UnplannedStepError,
)
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedCommand,
    PreparedStep,
    PrivateJsonValue,
    PrivateProjection,
    ProcessExecutor,
    ProcessResult,
    RunContext,
    Step,
    StepEvent,
    SubprocessExecutor,
    activate_context,
    prepared_command,
    prepared_step,
)

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


class _PlanObservation(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
    omit_defaults=True,
):
    """Immutable, serializable metadata attached to an execution plan."""

    kind: str
    scope: str
    step_ids: tuple[str, ...]
    budget_seconds: float
    read_only: bool = False
    executed_during_planning: bool = False


class PlanPrecondition(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """A decision-level safety check retained alongside an execution plan."""

    name: str
    status: Literal["passed", "failed", "unknown"]
    detail: str


class SemanticPlanObservation(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """Typed human-facing intent for a plan.

    This lives in the existing ``observations`` slot so the private prepared
    process snapshot and the public step fields remain untouched.
    """

    kind: Literal["semantic"]
    goal: str
    targets: tuple[str, ...] = ()
    mutations: tuple[str, ...] = ()
    preconditions: tuple[PlanPrecondition, ...] = ()
    warnings: tuple[str, ...] = ()
    active_sessions: tuple[dict[str, JsonValue], ...] = ()


type PlanObservation = JsonValue | _PlanObservation | SemanticPlanObservation


class ProcessStep(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
    tag_field="kind",
    tag="process",
):
    """A captured child-process invocation safe to display and serialize."""

    step_id: str
    argv: tuple[str, ...]
    display: str
    executable: str
    cwd: str | None = None
    environment_policy: str = "sanitized-inherit"
    environment_overrides: tuple[tuple[str, str], ...] = ()
    input_preview: str | None = None
    timeout: float | None = None
    mode: str = "captured"
    read_only: bool = False
    mutating: bool = False
    interactive: bool = False
    long_running: bool = False


class ActionStep(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
    tag_field="kind",
    tag="action",
):
    """An honest in-process effect; it is never represented as shell text."""

    step_id: str
    action: str
    description: str
    details: JsonValue = None
    read_only: bool = False
    mutating: bool = False


ExecutionStep = ProcessStep | ActionStep


class ExecutionPlan(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """The immutable public projection of one prepared operation."""

    steps: tuple[ExecutionStep, ...] = ()
    observations: tuple[PlanObservation, ...] = ()
    warnings: tuple[str, ...] = ()
    fingerprint: str = ""

    @property
    def process_steps(self) -> tuple[ProcessStep, ...]:
        return tuple(step for step in self.steps if isinstance(step, ProcessStep))

    def with_fingerprint(self, *, secrets: Sequence[str] = ()) -> ExecutionPlan:
        """Return this plan with its canonical redacted fingerprint attached."""

        return msgspec.structs.replace(self, fingerprint=fingerprint_plan(self, secrets=secrets))


def canonical_plan_projection(plan: ExecutionPlan, *, secrets: Sequence[str] = ()) -> JsonValue:
    """Return the redacted plan value used as the digest's sole input."""

    builtins = cast("dict[str, JsonValue]", msgspec.to_builtins(plan))
    without_fingerprint = {key: value for key, value in builtins.items() if key != "fingerprint"}
    from odoo_instance_sdk.internal.proc.redaction import redacted_projection

    return redacted_projection(without_fingerprint, secrets=secrets, field="plan")


def canonical_plan_bytes(plan: ExecutionPlan, *, secrets: Sequence[str] = ()) -> bytes:
    """Serialize a redacted plan deterministically for hashing or comparison."""

    projection = canonical_plan_projection(plan, secrets=secrets)
    return json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint_plan(plan: ExecutionPlan, *, secrets: Sequence[str] = ()) -> str:
    """Hash only canonical redacted plan data, excluding ``fingerprint`` itself."""

    return hashlib.sha256(canonical_plan_bytes(plan, secrets=secrets)).hexdigest()


T = TypeVar("T")
R = TypeVar("R")


def _captured_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class CapturedProcessOutput(msgspec.Struct, frozen=True, kw_only=True):
    """Stdout and stderr captured from one planned process step."""

    returncode: int
    stdout: str
    stderr: str


class ExternalEffects:
    """Runs the steps of one continued or external command.

    Args:
        context: Ledger for the command that owns these steps.
    """

    def __init__(self, context: RunContext[object]) -> None:
        self._context = context

    def process(self, step_id: str) -> CapturedProcessOutput:
        """Execute one planned process step.

        Args:
            step_id: Identifier captured in the command plan.

        Returns:
            The captured return code and text streams.
        """
        result = cast("ProcessResult", self._context.process(step_id))
        return CapturedProcessOutput(
            returncode=result.returncode,
            stdout=_captured_text(result.stdout),
            stderr=_captured_text(result.stderr),
        )

    def begin(self, step_id: str) -> None:
        """Start one planned in-process action.

        Args:
            step_id: Identifier captured in the command plan.
        """
        self._context.action(step_id)

    def complete(self, step_id: str) -> None:
        """Complete one action that already started.

        Args:
            step_id: Identifier passed to ``begin``.
        """
        self._context.complete_action(step_id)

    def fail(self, step_id: str, error: BaseException) -> None:
        """Close one started action after its effect fails.

        Args:
            step_id: Identifier passed to ``begin``.
            error: Failure recorded on the sanitized lifecycle event.
        """
        self._context.fail_action(step_id, error)

    def skip(self, step_id: str) -> None:
        """Account for a planned step that this invocation will not run.

        Args:
            step_id: Identifier captured in the command plan.
        """
        self._context.skip(step_id)


def _private_step(step: ExecutionStep) -> Step:
    if isinstance(step, ProcessStep):
        if not step.argv:
            raise PlanValidationError("process step argv must not be empty")
        executable, *args = step.argv
        return prepared_step(
            executable,
            args,
            step_id=step.step_id,
            cwd=step.cwd,
            timeout=step.timeout,
            mode=step.mode,
            read_only=step.read_only,
            mutating=step.mutating,
            interactive=step.interactive,
            long_running=step.long_running,
            environment_policy=step.environment_policy,
            env=dict(step.environment_overrides) or None,
        )
    return PreparedAction(
        step_id=step.step_id,
        action=step.action,
        description=step.description,
        details=cast("PrivateJsonValue", step.details),
        read_only=step.read_only,
        mutating=step.mutating,
    )


def _command_from_private(
    private_steps: tuple[Step, ...],
    callback: Callable[[RunContext[R]], R],
    *,
    observations: tuple[PlanObservation, ...] = (),
    warnings: tuple[str, ...] = (),
    executor: ProcessExecutor | None = None,
    private_projection: PrivateProjection | None = None,
) -> Command[R]:
    prepared = prepared_command(
        callback,
        private_steps,
        executor=executor or SubprocessExecutor(),
        private_projection=private_projection,
    )
    plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in private_steps),
        observations=observations,
        warnings=warnings,
    ).with_fingerprint()
    return Command.from_prepared(plan, prepared)


def external_command(
    steps: Sequence[ExecutionStep],
    callback: Callable[[ExternalEffects], R],
) -> Command[R]:
    """Capture one external command from public process and action steps.

    Args:
        steps: Inspectable steps preview and execution both use.
        callback: Effect that consumes every step exactly once.

    Returns:
        The immutable command snapshot.
    """
    private_steps = tuple(_private_step(step) for step in steps)

    def run(context: RunContext[R]) -> R:
        return callback(ExternalEffects(cast("RunContext[object]", context)))

    return _command_from_private(private_steps, run)


def continue_command(
    base: Command[T],
    steps: Sequence[ExecutionStep],
    callback: Callable[[T, ExternalEffects], R],
) -> Command[R]:
    """Append steps to a captured command and return one combined snapshot.

    The original callback runs once on a nested ledger that shares the
    executor. Appended steps then run on the outer ledger. Preview and
    ``run`` both use this combined snapshot.

    Args:
        base: Command whose prepared steps run first.
        steps: Public steps appended after the base command.
        callback: Receives the base result and the appended-step effects.

    Returns:
        One command whose plan contains both step sequences.
    """
    prepared = base._prepared()
    private_steps = prepared.steps + tuple(_private_step(step) for step in steps)

    def run(context: RunContext[R]) -> R:
        child: RunContext[T] = RunContext(
            prepared.steps,
            context.executor,
            observer=context.observer,
            observe_output=context.observes_output(),
        )
        with activate_context(child):
            try:
                value = prepared.callback(child)
                child.complete()
                child.finish_actions()
            except BaseException as error:
                child.fail_actions(error)
                raise
        for step in prepared.steps:
            context.note_nested(step.step_id)
        return callback(value, ExternalEffects(cast("RunContext[object]", context)))

    return _command_from_private(
        private_steps,
        run,
        observations=tuple(base.plan.observations),
        warnings=base.plan.warnings,
        executor=prepared.executor,
        private_projection=prepared.private_projection,
    )


class _StoredCommand(Protocol):
    steps: tuple[Step, ...]

    def run(
        self,
        *,
        observer: Callable[[StepEvent], None] | None = None,
        observe_output: bool = False,
    ) -> ProcessResult: ...

    @property
    def private_projection(self) -> PrivateProjection | None: ...


_COMMANDS: dict[int, _StoredCommand] = {}


class Command(msgspec.Struct, Generic[T], frozen=True, forbid_unknown_fields=True):
    """An immutable public plan paired with a private executable snapshot."""

    plan: ExecutionPlan

    @classmethod
    def from_prepared(cls, plan: ExecutionPlan, prepared: PreparedCommand[T]) -> Command[T]:
        prepared_steps = tuple(step.public_projection() for step in prepared.steps)
        if plan.steps != prepared_steps:
            raise PlanValidationError(
                "public execution plan steps do not match prepared command steps"
            )
        if not plan.fingerprint:
            plan = plan.with_fingerprint()
        command = cls(plan=plan)
        _COMMANDS[id(command)] = cast("_StoredCommand", prepared)
        return command

    @classmethod
    def create(
        cls,
        plan: ExecutionPlan,
        callback: Callable[[RunContext[T]], T],
        steps: Sequence[Step] = (),
        *,
        executor: ProcessExecutor | None = None,
        private_projection: PrivateProjection | None = None,
    ) -> Command[T]:
        """Create a command for resource implementations and focused tests.

        Resource code should normally use ``internal.proc.prepared_command``
        so exact process inputs stay private at the construction boundary.
        """

        return cls.from_prepared(
            plan,
            prepared_command(
                callback,
                steps,
                executor=executor,
                private_projection=private_projection,
            ),
        )

    @property
    def commands(self) -> tuple[ProcessStep, ...]:
        """Stable process-only view of the plan, in execution order."""

        return self.plan.process_steps

    def run(
        self,
        *,
        observer: Callable[[StepEvent], None] | None = None,
        observe_output: bool = False,
    ) -> T:
        """Execute the captured snapshot with a fresh ledger for this call."""

        prepared = _COMMANDS.get(id(self))
        if prepared is None:
            raise PlanError("command has no prepared executable snapshot")
        return cast("T", prepared.run(observer=observer, observe_output=observe_output))

    def _private_projection(self) -> PrivateProjection | None:
        """Read resource compatibility data without exposing it publicly."""
        prepared = _COMMANDS.get(id(self))
        if prepared is None or prepared.private_projection is None:
            return None
        return prepared.private_projection

    def _prepared(self) -> PreparedCommand[T]:
        """Return the private snapshot for composing CLI-internal commands."""
        prepared = _COMMANDS.get(id(self))
        if prepared is None:
            raise PlanError("command has no prepared executable snapshot")
        return cast("PreparedCommand[T]", prepared)

    def _private_wrapper_nonce(self) -> str | None:
        """Return the nonce captured by the command's shell process step."""
        prepared = _COMMANDS.get(id(self))
        if prepared is None:
            raise PlanError("command has no prepared executable snapshot")
        for step in prepared.steps:
            if isinstance(step, PreparedStep) and step.wrapper_nonce is not None:
                return step.wrapper_nonce
        return None

    def __del__(self) -> None:
        _COMMANDS.pop(id(self), None)


__all__ = [
    "ActionStep",
    "CapturedProcessOutput",
    "Command",
    "DuplicateStepError",
    "ExecutionPlan",
    "ExecutionStep",
    "ExternalEffects",
    "JsonValue",
    "OmittedStepError",
    "PlanError",
    "PlanPrecondition",
    "PlanValidationError",
    "ProcessStep",
    "SemanticPlanObservation",
    "StalePlanError",
    "UnplannedStepError",
    "canonical_plan_bytes",
    "canonical_plan_projection",
    "continue_command",
    "external_command",
    "fingerprint_plan",
]
