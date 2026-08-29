"""Private prepared process snapshots and per-run consumption ledgers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, cast

from odoo_instance_sdk.exceptions import (
    DuplicateStepError,
    OmittedStepError,
    UnplannedStepError,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import ProcessStep


class PreparedProcess(Protocol):
    """Minimum private process-step contract used by the ledger."""

    @property
    def step_id(self) -> str: ...

    @property
    def argv(self) -> tuple[str, ...]: ...


class ProcessExecutor(Protocol):
    def execute(self, step: PreparedProcess) -> object:
        """Execute one already-captured step."""


class _NullExecutor:
    def execute(self, step: PreparedProcess) -> object:
        return step


@dataclass(frozen=True, slots=True)
class PreparedStep:
    """Exact private process inputs; never expose this through public models."""

    step_id: str
    argv: tuple[str, ...]
    cwd: str | None = None
    environment: tuple[tuple[str, str], ...] = ()
    environment_policy: str = "sanitized-inherit"
    stdin: bytes | None = None
    timeout: float | None = None
    mode: str = "captured"
    secret_values: tuple[str, ...] = ()
    read_only: bool = False
    mutating: bool = False
    interactive: bool = False
    long_running: bool = False

    def public_projection(self) -> ProcessStep:
        from odoo_instance_sdk.internal.proc.redaction import project_process_step

        return project_process_step(self)


@dataclass(frozen=True, slots=True)
class PreparedAction:
    step_id: str


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
) -> PreparedCommand[T]:
    frozen_steps = tuple(steps)
    identifiers = [step.step_id for step in frozen_steps]
    if len(identifiers) != len(set(identifiers)):
        duplicate = next(
            identifier for identifier in identifiers if identifiers.count(identifier) > 1
        )
        raise DuplicateStepError(duplicate)
    return PreparedCommand(callback, frozen_steps, executor or _NullExecutor())


__all__ = [
    "PreparedAction",
    "PreparedCommand",
    "PreparedProcess",
    "PreparedStep",
    "ProcessExecutor",
    "RunContext",
    "prepared_command",
]
