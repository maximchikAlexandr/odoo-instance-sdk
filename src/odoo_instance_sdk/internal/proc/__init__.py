"""Private prepared process snapshots and per-run consumption ledgers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, cast

from odoo_instance_sdk.exceptions import (
    DuplicateStepError,
    OmittedStepError,
    UnplannedStepError,
)
from odoo_instance_sdk.models import EnvironmentCheckoutPlan

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import ActionStep, JsonValue, ProcessStep

    from .executor import ProcessHandle


class PreparedProcess(Protocol):
    """Minimum private process-step contract used by the ledger."""

    @property
    def step_id(self) -> str: ...

    @property
    def argv(self) -> tuple[str, ...]: ...


class ProcessResultLike(Protocol):
    """Private executor result marker; concrete executors may refine it."""

    def __repr__(self) -> str: ...


type PrivateJsonValue = (
    None
    | bool
    | int
    | float
    | str
    | tuple["PrivateJsonValue", ...]
    | Mapping[str, "PrivateJsonValue"]
)

# The only private compatibility projection currently stored with a command is
# the captured checkout domain plan.  Keeping this alias concrete prevents the
# command boundary from becoming an untyped side channel.
type PrivateProjection = EnvironmentCheckoutPlan


class ProcessExecutor(Protocol):
    def execute(self, step: PreparedStep) -> ProcessResultLike:
        """Execute one already-captured step."""

    def spawn(self, step: PreparedStep) -> ProcessHandle:
        """Spawn one already-captured long-running step."""


class _NullExecutor:
    def execute(self, step: PreparedStep) -> ProcessResultLike:
        return cast("ProcessResultLike", None)

    def spawn(self, step: PreparedStep) -> ProcessHandle:
        return cast("ProcessHandle", None)


@dataclass(frozen=True, slots=True)
class PreparedStep:
    """Exact private process inputs; never expose this through public models."""

    step_id: str
    argv: tuple[str, ...]
    # Captured at construction and consumed by every public projection.  This
    # is intentionally private: callers cannot reconstruct a safe argv by
    # applying a second, weaker redaction pass later.
    sensitive_argv_indices: tuple[int, ...] = ()
    cwd: str | None = None
    environment: tuple[tuple[str, str], ...] = ()
    # Full immutable child environment, private to execution.  ``environment``
    # remains the historical explicit-overrides payload for ProcessResult.
    environment_snapshot: tuple[tuple[str, str], ...] = ()
    # Exact private values above are used for execution.  This separate tuple
    # contains only caller-supplied overrides that are eligible for public
    # projection; inherited environment values never need to be serialized.
    environment_overrides: tuple[tuple[str, str], ...] = ()
    environment_policy: str = "sanitized-inherit"
    stdin: bytes | None = None
    wrapper_nonce: str | None = None
    secret_config_path: str | None = None
    public_input_preview: str | None = None
    timeout: float | None = None
    mode: str = "captured"
    secret_values: tuple[str, ...] = ()
    read_only: bool = False
    mutating: bool = False
    interactive: bool = False
    long_running: bool = False
    text: bool = True
    start_new_session: bool = False
    inherit_stdio: bool = False

    def __post_init__(self) -> None:
        if not self.environment_snapshot:
            from odoo_instance_sdk.internal.process_env import captured_child_environment

            snapshot, overrides = captured_child_environment(dict(self.environment) or None)
            if self.environment_policy == "explicit":
                snapshot = overrides
            object.__setattr__(self, "environment_snapshot", snapshot)
            if not self.environment_overrides:
                object.__setattr__(self, "environment_overrides", overrides)
        from .redaction import capture_sensitive_argv_indices

        captured = capture_sensitive_argv_indices(self.argv, secrets=self.secret_values)
        object.__setattr__(
            self,
            "sensitive_argv_indices",
            tuple(sorted(set(self.sensitive_argv_indices).union(captured))),
        )

    def public_projection(self) -> ProcessStep:
        from odoo_instance_sdk.internal.proc.redaction import project_process_step

        return project_process_step(self)


@dataclass(frozen=True, slots=True)
class PreparedAction:
    step_id: str
    action: str = ""
    description: str = ""
    details: PrivateJsonValue = None
    read_only: bool = False
    mutating: bool = False

    def public_projection(self) -> ActionStep:
        from odoo_instance_sdk.execution import ActionStep

        return ActionStep(
            step_id=self.step_id,
            action=self.action or self.step_id,
            description=self.description or self.action or self.step_id,
            details=cast("JsonValue", self.details),
            read_only=self.read_only,
            mutating=self.mutating,
        )


Step = PreparedStep | PreparedAction
T = TypeVar("T")
_ACTIVE_CONTEXT: ContextVar[RunContext[PrivateJsonValue] | None] = ContextVar(
    "odoo_sdk_active_run_context", default=None
)


class RunContext(Generic[T]):
    """Mutable only for one invocation of a command."""

    def __init__(self, steps: tuple[Step, ...], executor: ProcessExecutor) -> None:
        self._steps = {step.step_id: step for step in steps}
        self._executor = executor
        self._consumed: set[str] = set()
        self._results: dict[str, ProcessResultLike] = {}

    def process(self, step_id: str) -> T:
        """Consume a captured process by identifier through the exact path."""
        return cast("T", self.process_prepared(self.prepared(step_id)))

    def process_prepared(self, requested: PreparedStep) -> ProcessResultLike:
        """Consume the exact immutable captured step, never a substituted request."""
        captured = self._steps.get(requested.step_id)
        if not isinstance(captured, PreparedStep) or captured != requested:
            raise UnplannedStepError(requested.step_id)
        if requested.step_id in self._consumed:
            raise DuplicateStepError(requested.step_id)
        self._consumed.add(requested.step_id)
        result = self._executor.execute(captured)
        self._results[requested.step_id] = result
        return result

    def spawn(self, step_id: str) -> ProcessHandle:
        step = self._consume(step_id)
        if not isinstance(step, PreparedStep):
            raise UnplannedStepError(step_id, reason="requested step is not a process")
        return self._executor.spawn(step)

    def action(self, step_id: str) -> PreparedAction:
        step = self._consume(step_id)
        if not isinstance(step, PreparedAction):
            raise UnplannedStepError(step_id, reason="requested step is not an action")
        return step

    def skip(self, step_id: str) -> None:
        """Consume a captured step when its guarded effect is intentionally omitted.

        A prepared command must account for every step even when a preceding
        result makes a later operation unnecessary.  This keeps the ledger
        honest without launching a process that the callback has decided not
        to perform.
        """
        self._consume(step_id)

    def _consume(self, step_id: str) -> Step:
        step = self._steps.get(step_id)
        if step is None:
            raise UnplannedStepError(step_id)
        if step_id in self._consumed:
            raise DuplicateStepError(step_id)
        self._consumed.add(step_id)
        return step

    def complete(self) -> None:
        omitted = tuple(sorted(set(self._steps) - self._consumed))
        if omitted:
            raise OmittedStepError(omitted)

    def consumed(self, step_id: str) -> bool:
        """Return whether this invocation accounted for a captured step."""
        return step_id in self._consumed

    def prepared(self, step_id: str) -> PreparedStep:
        """Return an immutable captured process step for an active adapter."""
        step = self._steps.get(step_id)
        if not isinstance(step, PreparedStep):
            raise UnplannedStepError(step_id, reason="requested step is not a process")
        return step

    def planned(self, step_id: str) -> bool:
        """Return whether this invocation captured a step with this identity."""
        return step_id in self._steps

    def skip_remaining(self) -> None:
        """Account for optional captured probes that a collector did not need."""
        self._consumed.update(
            step_id for step_id, step in self._steps.items() if isinstance(step, PreparedStep)
        )

    @property
    def results(self) -> Mapping[str, ProcessResultLike]:
        """Results captured by this invocation, keyed by private step ID."""
        return self._results

    @property
    def executor(self) -> ProcessExecutor:
        """Return the executor for an explicit nested phase command."""
        return self._executor


def active_context() -> RunContext[PrivateJsonValue] | None:
    """Return the command context active on this execution thread."""

    return _ACTIVE_CONTEXT.get()


Callback = Callable[[RunContext[T]], T]


@dataclass(frozen=True, slots=True)
class PreparedCommand(Generic[T]):
    callback: Callback[T]
    steps: tuple[Step, ...]
    executor: ProcessExecutor
    private_projection: PrivateProjection | None = None

    def run(self) -> T:
        context: RunContext[T] = RunContext(self.steps, self.executor)
        token = _ACTIVE_CONTEXT.set(cast("RunContext[PrivateJsonValue]", context))
        try:
            result = self.callback(context)
            context.complete()
            return result
        finally:
            _ACTIVE_CONTEXT.reset(token)


def prepared_command(
    callback: Callback[T],
    steps: Sequence[Step] = (),
    *,
    executor: ProcessExecutor | None = None,
    private_projection: PrivateProjection | None = None,
) -> PreparedCommand[T]:
    frozen_steps = tuple(steps)
    identifiers = [step.step_id for step in frozen_steps]
    if len(identifiers) != len(set(identifiers)):
        duplicate = next(
            identifier for identifier in identifiers if identifiers.count(identifier) > 1
        )
        raise DuplicateStepError(duplicate)
    return PreparedCommand(
        callback,
        frozen_steps,
        executor or _NullExecutor(),
        private_projection,
    )


__all__ = [
    "PreparedAction",
    "PreparedCommand",
    "PreparedProcess",
    "PreparedStep",
    "ProcessExecutionError",
    "ProcessExecutor",
    "ProcessHandle",
    "ProcessResult",
    "ProcessResultLike",
    "ProcessSpawnError",
    "ProcessTimeoutError",
    "RecordingExecutor",
    "RunContext",
    "SubprocessExecutor",
    "active_context",
    "owned_handle",
    "prepared_command",
    "prepared_step",
    "run_captured",
    "run_captured_limited",
    "spawn",
    "terminate",
    "wait_foreground",
]


# Imported at the end to keep the private snapshot definitions independent of
# the real subprocess implementation.  The package remains one seam for
# callers while the implementation stays split by responsibility.
from .executor import (  # noqa: E402
    ProcessExecutionError,
    ProcessHandle,
    ProcessResult,
    ProcessSpawnError,
    ProcessTimeoutError,
    SubprocessExecutor,
    owned_handle,
    prepared_step,
    run_captured,
    run_captured_limited,
    spawn,
    terminate,
    wait_foreground,
)
from .testing import RecordingExecutor  # noqa: E402
