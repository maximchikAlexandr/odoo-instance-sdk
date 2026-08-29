"""The sole deterministic executor seam used by process tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from . import PreparedProcess, PreparedStep, ProcessResultLike
from .executor import ProcessHandle, ProcessResult


@dataclass(slots=True)
class RecordingExecutor:
    """Record exact private inputs while returning configured typed effects."""

    results: dict[str, ProcessResultLike] = field(default_factory=dict)
    handles: dict[str, ProcessHandle] = field(default_factory=dict)
    default_result: ProcessResultLike | None = None
    result_factory: Callable[[PreparedProcess], ProcessResultLike] | None = None
    executed: list[PreparedStep] = field(default_factory=list)
    spawned: list[PreparedStep] = field(default_factory=list)

    def execute(self, step: PreparedProcess) -> ProcessResultLike:
        prepared = cast("PreparedStep", step)
        self.executed.append(prepared)
        if self.result_factory is not None:
            return self.result_factory(prepared)
        result = self.results.get(prepared.step_id, self.default_result)
        if result is None:
            return ProcessResult(
                argv=prepared.argv,
                returncode=0,
                stdout=b"" if not prepared.text else "",
                stderr=b"" if not prepared.text else "",
                duration=0.0,
                cwd=prepared.cwd,
                environment=prepared.environment,
            )
        return result

    def spawn(self, step: PreparedProcess) -> ProcessHandle:
        prepared = cast("PreparedStep", step)
        self.spawned.append(prepared)
        return self.handles[prepared.step_id]


__all__ = ["RecordingExecutor"]
