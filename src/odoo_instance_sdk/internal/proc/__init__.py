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
    kind: Literal["started", "stdout", "stderr", "completed", "failed"]
    chunk: str | None = None
    returncode: int | None = None
    error: str | None = None


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


Step = PreparedStep | PreparedAction
T = TypeVar("T")
_ACTIVE_CONTEXT: ContextVar[RunContext[PrivateJsonValue] | None] = ContextVar(
    "odoo_sdk_active_run_context", default=None
)


class _BufferedStepObserver:
    """Delay stream chunks until a whole captured result can be redacted."""

    def __init__(self, observer: StepObserver, step: PreparedStep) -> None:
        self._observer = observer
        self._step = step
        self._chunks: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def __call__(self, event: StepEvent) -> None:
        if event.kind in {"stdout", "stderr"}:
            if event.chunk:
                self._chunks[event.kind].append(event.chunk)
            return
        if event.kind in {"completed", "failed"}:
            from .redaction import captured_secret_values, redacted_projection

            secrets = captured_secret_values(self._step)
            for stream in ("stdout", "stderr"):
                chunk = "".join(self._chunks[stream])
                if not chunk:
                    continue
                safe = cast("str", redacted_projection(chunk, secrets=secrets, field=stream))
                self._observer(
                    StepEvent(
                        step_id=self._step.step_id,
                        kind=stream,
                        chunk=safe,
                    )
                )
            self._observer(event)
            return
        self._observer(event)


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
        self._started_actions: list[str] = []

    def process(self, step_id: str) -> T:
        """Consume a captured process by identifier through the exact path."""
        return cast("T", self.process_prepared(self.prepared(step_id)))

    def process_prepared(self, requested: PreparedStep) -> ProcessResultLike:
        """Consume the exact immutable captured step, never a substituted request."""
        captured = self._capture_prepared(requested)
        observer = (
            _BufferedStepObserver(self._observer, captured) if self._observer is not None else None
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
            _BufferedStepObserver(self._observer, captured) if self._observer is not None else None
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
            _BufferedStepObserver(self._observer, step) if self._observer is not None else None
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
        _notify(self._observer, StepEvent(step_id=step.step_id, kind="started"))
        self._started_actions.append(step.step_id)
        return step

    def finish_actions(self) -> None:
        """Complete all logical actions after their guarded callback succeeds."""
        for step_id in self._started_actions:
            _notify(self._observer, StepEvent(step_id=step_id, kind="completed", returncode=0))
        self._started_actions.clear()

    def fail_actions(self, error: BaseException) -> None:
        """Close logical actions with a sanitized failure when execution aborts."""
        from odoo_instance_sdk.internal.sanitize import sanitize_event_message

        message = sanitize_event_message(str(error))
        for step_id in self._started_actions:
            _notify(self._observer, StepEvent(step_id=step_id, kind="failed", error=message))
        self._started_actions.clear()

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
    "owned_handle",
    "prepared_command",
    "prepared_step",
    "require_deadline_executor",
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
