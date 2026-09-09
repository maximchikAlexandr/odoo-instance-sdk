"""The sole deterministic executor seam used by process tests."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from . import (
    ExecutionDeadline,
    PreparedProcess,
    PreparedStep,
    ProcessResultLike,
    StepObserver,
    bounded_process_inputs,
    event_for_step,
)
from .executor import ProcessHandle, ProcessResult, _notify, _notify_output, _safe_error


@dataclass(slots=True)
class RecordingExecutor:
    """Record exact private inputs while returning configured typed effects."""

    results: dict[str, ProcessResultLike] = field(default_factory=dict)
    handles: dict[str, ProcessHandle] = field(default_factory=dict)
    default_result: ProcessResultLike | None = None
    result_factory: Callable[[PreparedProcess], ProcessResultLike] | None = None
    executed: list[PreparedStep] = field(default_factory=list)
    spawned: list[PreparedStep] = field(default_factory=list)
    effective_timeouts: list[float] = field(default_factory=list)
    effective_environment_snapshots: list[tuple[tuple[str, str], ...]] = field(default_factory=list)
    stdout_chunks: tuple[str, ...] = ()
    stderr_chunks: tuple[str, ...] = ()

    def execute(
        self,
        step: PreparedProcess,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResultLike:
        prepared = cast("PreparedStep", step)
        self.executed.append(prepared)
        _notify(observer, event_for_step(prepared, "started", elapsed=0.0))
        try:
            result: ProcessResultLike | None
            if self.result_factory is not None:
                result = self.result_factory(prepared)
            else:
                result = self.results.get(prepared.step_id, self.default_result)
                if result is None:
                    result = ProcessResult(
                        argv=prepared.argv,
                        returncode=0,
                        stdout=b"" if not prepared.text else "",
                        stderr=b"" if not prepared.text else "",
                        duration=0.0,
                        cwd=prepared.cwd,
                        environment=prepared.environment,
                    )
        except Exception as error:
            _notify(
                observer,
                event_for_step(
                    prepared,
                    "failed",
                    error=_safe_error(prepared, error),
                    elapsed=0.0,
                ),
            )
            raise
        if isinstance(result, ProcessResult):
            if observe_output:
                if self.stdout_chunks:
                    for chunk in self.stdout_chunks:
                        _notify(
                            observer,
                            event_for_step(prepared, "stdout", chunk=chunk),
                        )
                else:
                    _notify_output(observer, prepared, result.stdout, "stdout")
                if self.stderr_chunks:
                    for chunk in self.stderr_chunks:
                        _notify(
                            observer,
                            event_for_step(prepared, "stderr", chunk=chunk),
                        )
                else:
                    _notify_output(observer, prepared, result.stderr, "stderr")
            returncode = result.returncode
        else:
            returncode = None
        _notify(
            observer,
            event_for_step(
                prepared,
                "completed",
                returncode=returncode,
                elapsed=getattr(result, "duration", 0.0),
            ),
        )
        return result

    def execute_with_deadline(
        self,
        step: PreparedProcess,
        deadline: ExecutionDeadline,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResultLike:
        prepared = cast("PreparedStep", step)
        bounded = bounded_process_inputs(prepared, deadline)
        self.effective_timeouts.append(bounded.timeout)
        self.effective_environment_snapshots.append(bounded.environment_snapshot)
        # Keep the result factory and executed ledger entry on the exact
        # captured step.  The bounded values are separate transport inputs,
        # never a substituted PreparedStep.
        return self.execute(prepared, observer=observer, observe_output=observe_output)

    def spawn(
        self,
        step: PreparedProcess,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessHandle:
        prepared = cast("PreparedStep", step)
        self.spawned.append(prepared)
        started = time.perf_counter()
        _notify(observer, event_for_step(prepared, "started", elapsed=0.0))
        try:
            handle = self.handles[prepared.step_id]
        except Exception as error:
            _notify(
                observer,
                event_for_step(
                    prepared,
                    "failed",
                    error=_safe_error(prepared, error),
                    elapsed=time.perf_counter() - started,
                ),
            )
            raise
        handle.observer = observer
        handle.step = prepared
        handle.started_at = started
        return handle


__all__ = ["RecordingExecutor"]
