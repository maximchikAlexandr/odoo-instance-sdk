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
    PreparedCommand,
    PrivateProjection,
    ProcessExecutor,
    ProcessResult,
    RunContext,
    Step,
    StepEvent,
    prepared_command,
)

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


class _PlanObservation(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Immutable, serializable metadata attached to an execution plan."""

    kind: str
    scope: str
    step_ids: tuple[str, ...]
    budget_seconds: float


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


class _StoredCommand(Protocol):
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

    def __del__(self) -> None:
        _COMMANDS.pop(id(self), None)


__all__ = [
    "ActionStep",
    "Command",
    "DuplicateStepError",
    "ExecutionPlan",
    "ExecutionStep",
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
    "fingerprint_plan",
]
