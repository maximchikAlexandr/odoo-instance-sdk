"""Private prepared process snapshots and per-run consumption ledgers."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, Protocol, TypeVar, cast

from odoo_instance_sdk.exceptions import (
    DuplicateStepError,
    OmittedStepError,
    PlanValidationError,
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


@dataclass(frozen=True, slots=True)
class StepEvent:
    """A sanitized lifecycle event for one captured process step."""

    step_id: str
    kind: Literal["started", "progress", "stdout", "stderr", "completed", "failed"]
    operation: str | None = None
    target: str | None = None
    chunk: str | None = None
    returncode: int | None = None
    error: str | None = None
    elapsed: float | None = None
    completed_units: int | float | None = None
    total_units: int | float | None = None

    def __post_init__(self) -> None:
        """Keep even compatibility-created events attributable and inert."""
        from odoo_instance_sdk.internal.sanitize import sanitize_event_message

        step_id = sanitize_event_message(self.step_id) or "step"
        operation = sanitize_event_message(self.operation or step_id) or step_id
        target = sanitize_event_message(self.target or step_id) or operation
        object.__setattr__(self, "step_id", step_id)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "target", target)
        if self.chunk is not None:
            from odoo_instance_sdk.internal.sanitize import sanitize_terminal_text

            object.__setattr__(
                self,
                "chunk",
                sanitize_terminal_text(self.chunk, preserve_newlines=True),
            )
        if self.error is not None:
            object.__setattr__(self, "error", sanitize_event_message(self.error))


type StepObserver = Callable[[StepEvent], None]


MIN_PROCESS_TIMEOUT = 0.001


class DeadlineExceeded(TimeoutError):
    """The shared monotonic deadline has no safe process-start remainder."""


@dataclass(frozen=True, slots=True, repr=False)
class ExecutionDeadline:
    """One per-run monotonic deadline shared by a sequence of process steps."""

    started_at: float
    budget: float
    monotonic: Callable[[], float] = time.monotonic

    @classmethod
    def start(
        cls, budget: float, *, monotonic: Callable[[], float] = time.monotonic
    ) -> ExecutionDeadline:
        if (
            isinstance(budget, bool)
            or not isinstance(budget, (int, float))
            or not math.isfinite(budget)
            or budget <= 0
        ):
            raise ValueError("deadline budget must be finite and greater than zero")
        return cls(started_at=monotonic(), budget=float(budget), monotonic=monotonic)

    @property
    def expires_at(self) -> float:
        return self.started_at + self.budget

    def remaining(self) -> float:
        return max(0.0, self.expires_at - self.monotonic())

    def timeout_for(self, requested: float | None) -> float:
        """Return a timeout no greater than the current monotonic remainder."""
        remaining = self.remaining()
        if remaining < MIN_PROCESS_TIMEOUT:
            raise DeadlineExceeded
        if requested is None:
            return remaining
        return min(float(requested), remaining)


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
    def execute(
        self,
        step: PreparedStep,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResultLike:
        """Execute one already-captured step."""

    def spawn(
        self,
        step: PreparedStep,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessHandle:
        """Spawn one already-captured long-running step."""


class DeadlineProcessExecutor(ProcessExecutor, Protocol):
    def execute_with_deadline(
        self,
        step: PreparedStep,
        deadline: ExecutionDeadline,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResultLike:
        """Execute the exact captured step under a shared monotonic deadline."""


def require_deadline_executor(executor: ProcessExecutor) -> DeadlineProcessExecutor:
    """Validate the optional deadline capability before a process can launch."""
    execute_with_deadline = getattr(executor, "execute_with_deadline", None)
    if not callable(execute_with_deadline):
        raise PlanValidationError(
            "status server-summary requires an executor implementing execute_with_deadline"
        )
    return cast("DeadlineProcessExecutor", executor)


class _NullExecutor:
    def execute(
        self,
        step: PreparedStep,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResultLike:
        return cast("ProcessResultLike", None)

    def execute_with_deadline(
        self,
        step: PreparedStep,
        deadline: ExecutionDeadline,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResultLike:
        return cast("ProcessResultLike", None)

    def spawn(
        self,
        step: PreparedStep,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessHandle:
        return cast("ProcessHandle", None)


@dataclass(frozen=True, slots=True, repr=False)
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

    def __repr__(self) -> str:
        """Render only the already-redacted public process projection."""
        return f"PreparedStep(public_projection={self.public_projection()!r})"

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
class BoundedProcessInputs:
    """Ephemeral child-process controls derived from a captured step.

    This is intentionally not a ``PreparedStep``.  The ledger and recording
    executor retain the exact captured step; only the subprocess boundary
    receives these per-attempt controls.
    """

    timeout: float
    environment_snapshot: tuple[tuple[str, str], ...]


def bounded_process_inputs(step: PreparedStep, deadline: ExecutionDeadline) -> BoundedProcessInputs:
    """Calculate bounded child controls without replacing the captured step."""
    timeout = deadline.timeout_for(step.timeout)
    environment = dict(step.environment_snapshot)
    statement_timeout = environment.get("PGOPTIONS")
    if statement_timeout is not None and statement_timeout.startswith("-c statement_timeout="):
        # PostgreSQL accepts integer milliseconds.  Flooring is required: a
        # ceil would make the server timeout exceed a fractional monotonic
        # remainder (and sub-millisecond attempts are refused above).
        environment["PGOPTIONS"] = f"-c statement_timeout={math.floor(timeout * 1000)}"
    return BoundedProcessInputs(
        timeout=timeout,
        environment_snapshot=tuple(sorted(environment.items())),
    )


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


def event_for_step(
    step: PreparedStep | PreparedAction,
    kind: Literal["started", "progress", "stdout", "stderr", "completed", "failed"],
    *,
    chunk: str | None = None,
    returncode: int | None = None,
    error: str | None = None,
    elapsed: float | None = None,
    completed_units: float | None = None,
    total_units: float | None = None,
) -> StepEvent:
    """Create an identified event from the immutable private step metadata."""
    if isinstance(step, PreparedStep):
        from shlex import join

        from .redaction import captured_secret_values, redacted_argv

        target = join(
            redacted_argv(
                step.argv,
                secrets=captured_secret_values(step),
                sensitive_indices=step.sensitive_argv_indices,
            )
        )
        operation = step.step_id
    else:
        operation = step.action or step.step_id
        target = step.description or operation
    return StepEvent(
        step_id=step.step_id,
        kind=kind,
        operation=operation,
        target=target,
        chunk=chunk,
        returncode=returncode,
        error=error,
        elapsed=elapsed,
        completed_units=completed_units,
        total_units=total_units,
    )


Step = PreparedStep | PreparedAction
T = TypeVar("T")
_ACTIVE_CONTEXT: ContextVar[RunContext[PrivateJsonValue] | None] = ContextVar(
    "odoo_sdk_active_run_context", default=None
)


class _StreamingStepObserver:
    """Redact observer output incrementally and independently per stream."""

    def __init__(self, observer: StepObserver, step: PreparedStep) -> None:
        from .redaction import IncrementalStreamRedactor, captured_secret_values

        self._observer = observer
        self._step = step
        secrets = captured_secret_values(step)
        self._redactors = {
            stream: IncrementalStreamRedactor(secrets=secrets, field=stream)
            for stream in ("stdout", "stderr")
        }

    def __call__(self, event: StepEvent) -> None:
        if event.kind in {"stdout", "stderr"}:
            if event.chunk:
                chunk = self._redactors[event.kind].feed(event.chunk)
                if chunk:
                    _notify(
                        self._observer,
                        StepEvent(
                            step_id=self._step.step_id,
                            kind=event.kind,
                            operation=event.operation,
                            target=event.target,
                            chunk=chunk,
                        ),
                    )
            return
        if event.kind in {"completed", "failed"}:
            for stream in ("stdout", "stderr"):
                chunk = self._redactors[stream].flush()
                if not chunk:
                    continue
                _notify(
                    self._observer,
                    StepEvent(
                        step_id=self._step.step_id,
                        kind=stream,
                        operation=event.operation,
                        target=event.target,
                        chunk=chunk,
                    ),
                )
            _notify(self._observer, event)
            return
        _notify(self._observer, event)


class RunContext(Generic[T]):
    """Mutable only for one invocation of a command."""

    def __init__(
        self,
        steps: tuple[Step, ...],
        executor: ProcessExecutor,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> None:
        self._steps = {step.step_id: step for step in steps}
        self._executor = executor
        self._consumed: set[str] = set()
        self._results: dict[str, ProcessResultLike] = {}
        self._observer = observer
        self._observe_output = observe_output
        self._started_actions: dict[str, float] = {}
        self._action_progress: dict[str, tuple[int | float, int | float | None]] = {}

    def process(self, step_id: str) -> T:
        """Consume a captured process by identifier through the exact path."""
        return cast("T", self.process_prepared(self.prepared(step_id)))

    def process_prepared(self, requested: PreparedStep) -> ProcessResultLike:
        """Consume the exact immutable captured step, never a substituted request."""
        captured = self._capture_prepared(requested)
        observer = (
            _StreamingStepObserver(self._observer, captured) if self._observer is not None else None
        )
        result = self._executor.execute(
            captured,
            observer=observer,
            observe_output=self._observe_output,
        )
        self._results[requested.step_id] = result
        return result

    def process_prepared_with_deadline(
        self, requested: PreparedStep, deadline: ExecutionDeadline
    ) -> ProcessResultLike:
        """Consume the exact captured step under a shared monotonic deadline.

        The deadline is an explicit execution control, not a replacement
        ``PreparedStep``.  This keeps the immutable public plan, ledger entry,
        and injected executor request in parity while the common process
        boundary computes the live child timeout.
        """
        deadline_executor = require_deadline_executor(self._executor)
        captured = self._capture_prepared(requested)
        observer = (
            _StreamingStepObserver(self._observer, captured) if self._observer is not None else None
        )
        result = deadline_executor.execute_with_deadline(
            captured,
            deadline,
            observer=observer,
            observe_output=self._observe_output,
        )
        self._results[requested.step_id] = result
        return result

    def _capture_prepared(self, requested: PreparedStep) -> PreparedStep:
        captured = self._steps.get(requested.step_id)
        if not isinstance(captured, PreparedStep) or captured != requested:
            raise UnplannedStepError(requested.step_id)
        if requested.step_id in self._consumed:
            raise DuplicateStepError(requested.step_id)
        self._consumed.add(requested.step_id)
        return captured

    def spawn(self, step_id: str) -> ProcessHandle:
        step = self._consume(step_id)
        if not isinstance(step, PreparedStep):
            raise UnplannedStepError(step_id, reason="requested step is not a process")
        observer = (
            _StreamingStepObserver(self._observer, step) if self._observer is not None else None
        )
        return self._executor.spawn(
            step,
            observer=observer,
            observe_output=self._observe_output,
        )

    def action(self, step_id: str) -> PreparedAction:
        step = self._consume(step_id)
        if not isinstance(step, PreparedAction):
            raise UnplannedStepError(step_id, reason="requested step is not an action")
        started = time.monotonic()
        _notify(self._observer, event_for_step(step, "started", elapsed=0.0))
        self._started_actions[step.step_id] = started
        return step

    def progress(
        self,
        step_id: str,
        completed_units: float,
        total_units: float | None = None,
    ) -> None:
        """Report reliable work units for a started logical action."""
        started = self._started_actions.get(step_id)
        if started is None:
            raise UnplannedStepError(step_id, reason="action has not started")
        if (
            isinstance(completed_units, bool)
            or not isinstance(completed_units, (int, float))
            or not math.isfinite(completed_units)
            or completed_units < 0
        ):
            raise ValueError("completed_units must be a non-negative number")
        if total_units is not None and (
            isinstance(total_units, bool)
            or not isinstance(total_units, (int, float))
            or not math.isfinite(total_units)
            or total_units < 0
            or total_units < completed_units
        ):
            raise ValueError("total_units must be greater than or equal to completed_units")
        self._action_progress[step_id] = (completed_units, total_units)
        _notify(
            self._observer,
            event_for_step(
                self._steps[step_id],
                "progress",
                elapsed=max(0.0, time.monotonic() - started),
                completed_units=completed_units,
                total_units=total_units,
            ),
        )

    def complete_action(self, step_id: str) -> None:
        """Complete one action after its effect and postcondition succeed."""
        started = self._started_actions.pop(step_id, None)
        if started is None:
            raise UnplannedStepError(step_id, reason="action has not started")
        completed_units, total_units = self._action_progress.pop(step_id, (None, None))
        _notify(
            self._observer,
            event_for_step(
                self._steps[step_id],
                "completed",
                returncode=0,
                elapsed=max(0.0, time.monotonic() - started),
                completed_units=completed_units,
                total_units=total_units,
            ),
        )

    def finish_actions(self) -> None:
        """Complete any legacy actions after their guarded callback succeeds."""
        for step_id in tuple(self._started_actions):
            self.complete_action(step_id)

    def fail_actions(self, error: BaseException) -> None:
        """Close logical actions with a sanitized failure when execution aborts."""
        for step_id in tuple(self._started_actions):
            self.fail_action(step_id, error)

    def fail_action(self, step_id: str, error: BaseException) -> bool:
        """Close one started action after a nested effect fails.

        Nested prepared commands may recover a failed optional probe and return
        a partial result.  They must close only the child action that failed;
        the enclosing action remains eligible to complete after its fallback
        projection succeeds.
        """
        started = self._started_actions.pop(step_id, None)
        if started is None:
            return False
        from odoo_instance_sdk.internal.sanitize import sanitize_event_message

        self._action_progress.pop(step_id, None)
        _notify(
            self._observer,
            event_for_step(
                self._steps[step_id],
                "failed",
                error=sanitize_event_message(str(error)) or "interrupted",
                elapsed=max(0.0, time.monotonic() - started),
            ),
        )
        return True

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

    @property
    def observer(self) -> StepObserver | None:
        """Return the optional lifecycle observer for owned waits."""
        return self._observer


def _notify(observer: StepObserver | None, event: StepEvent) -> None:
    if observer is None:
        return
    try:
        observer(event)
    except Exception:
        return


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

    def run(
        self,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> T:
        context: RunContext[T] = RunContext(
            self.steps,
            self.executor,
            observer=observer,
            observe_output=observe_output,
        )
        token = _ACTIVE_CONTEXT.set(cast("RunContext[PrivateJsonValue]", context))
        try:
            result = self.callback(context)
            context.complete()
            context.finish_actions()
        except BaseException as error:
            context.fail_actions(error)
            raise
        else:
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
    "BoundedProcessInputs",
    "DeadlineExceeded",
    "DeadlineProcessExecutor",
    "ExecutionDeadline",
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
    "StepEvent",
    "StepObserver",
    "SubprocessExecutor",
    "active_context",
    "bounded_process_inputs",
    "is_process_alive",
    "owned_handle",
    "prepared_command",
    "prepared_step",
    "require_deadline_executor",
    "run_captured",
    "run_captured_limited",
    "spawn",
    "terminate",
    "terminate_pid",
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
    is_process_alive,
    owned_handle,
    prepared_step,
    run_captured,
    run_captured_limited,
    spawn,
    terminate,
    terminate_pid,
    wait_foreground,
)
from .testing import RecordingExecutor  # noqa: E402
