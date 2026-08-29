"""Private prepared process snapshots and per-run consumption ledgers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, cast

from odoo_instance_sdk.exceptions import (
    DuplicateStepError,
    OmittedStepError,
    UnplannedStepError,
)

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


class _PrivateCommandProjection(Protocol):
    """Typed marker for resource compatibility data kept off public commands."""


type PrivateJsonValue = (
    None
    | bool
    | int
    | float
    | str
    | tuple["PrivateJsonValue", ...]
    | Mapping[str, "PrivateJsonValue"]
)


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
    cwd: str | None = None
    environment: tuple[tuple[str, str], ...] = ()
    environment_policy: str = "sanitized-inherit"
    stdin: bytes | None = None
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


class RunContext(Generic[T]):
    """Mutable only for one invocation of a command."""

    def __init__(self, steps: tuple[Step, ...], executor: ProcessExecutor) -> None:
        self._steps = {step.step_id: step for step in steps}
        self._executor = executor
        self._consumed: set[str] = set()

    def process(self, step_id: str) -> T:
        step = self._consume(step_id)
        if not isinstance(step, PreparedStep):
            raise UnplannedStepError(step_id, reason="requested step is not a process")
        return cast("T", self._executor.execute(step))

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


Callback = Callable[[RunContext[T]], T]


@dataclass(frozen=True, slots=True)
class PreparedCommand(Generic[T]):
    callback: Callback[T]
    steps: tuple[Step, ...]
    executor: ProcessExecutor
    private_projection: _PrivateCommandProjection | None = None

    def run(self) -> T:
        context: RunContext[T] = RunContext(self.steps, self.executor)
        result = self.callback(context)
        context.complete()
        return result


def prepared_command(
    callback: Callback[T],
    steps: Sequence[Step] = (),
    *,
    executor: ProcessExecutor | None = None,
    private_projection: _PrivateCommandProjection | None = None,
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
    "owned_handle",
    "prepared_command",
    "prepared_step",
    "run_captured",
    "spawn",
    "terminate",
    "wait",
    "wait_foreground",
    "wait_with_cleanup",
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
    spawn,
    terminate,
    wait,
    wait_foreground,
    wait_with_cleanup,
)
from .testing import RecordingExecutor  # noqa: E402
