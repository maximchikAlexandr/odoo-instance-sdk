"""Environment-facts provider for bound OpenSpec changes."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Literal

from odcli_openspec.adapter import progress_from_parts
from odcli_openspec.errors import OpenSpecError
from odcli_openspec.models import OpenSpecProgress
from odcli_openspec.probes import assess_probe, probe_steps, run_probe
from odcli_openspec.store import read_binding
from odoo_instance_sdk.execution import ExternalEffects, external_command
from odoo_instance_sdk.models import CheckoutRow, EnvironmentFactsSummary


class OpenSpecFactsProvider:
    """Contribute one OpenSpec summary per checkout row.

    Args:
        probe_timeout: Timeout for one OpenSpec process, in seconds.
        budget_seconds: Total probe budget for one inventory sample.
    """

    def __init__(self, *, probe_timeout: float = 0.4, budget_seconds: float = 1.2) -> None:
        self._probe_timeout = probe_timeout
        self._budget_seconds = budget_seconds

    @property
    def provider_id(self) -> str:
        """Stable provider id used as the Rich column and JSON key."""
        return "openspec"

    def collect(self, rows: Sequence[CheckoutRow]) -> Mapping[str, EnvironmentFactsSummary]:
        """Probe bound worktrees and leave every other row visible.

        Args:
            rows: Immutable checkout rows from one inventory sample.

        Returns:
            Summaries keyed by environment id, or ``main`` for the main checkout.
        """
        deadline = time.monotonic() + self._budget_seconds
        summaries: dict[str, EnvironmentFactsSummary] = {}
        for row in rows:
            row_id = row.environment_id or "main"
            summaries[row_id] = self._summary(row, deadline)
        return summaries

    def _summary(self, row: CheckoutRow, deadline: float) -> EnvironmentFactsSummary:
        if row.environment_id is None:
            return _summary_for(_unbound(None))
        try:
            binding = read_binding(uuid.UUID(row.environment_id))
        except (ValueError, OpenSpecError) as exc:
            reason = exc.reason if isinstance(exc, OpenSpecError) else "invalid_binding"
            return _summary_for(_failed(row.environment_id, reason))
        if binding is None:
            return _summary_for(_unbound(row.environment_id))
        if time.monotonic() + self._probe_timeout > deadline:
            return _summary_for(_failed(binding.change_id, "probe_budget_exceeded"))
        progress = _probe(
            row.worktree_path, binding.executable, binding.change_id, self._probe_timeout
        )
        return _summary_for(progress)


def load_provider() -> OpenSpecFactsProvider:
    """Return the entry-point provider with the default probe budget."""
    return OpenSpecFactsProvider()


def _probe(worktree: str, executable: str, change_id: str, timeout: float) -> OpenSpecProgress:
    steps = probe_steps(
        prefix="openspec.facts",
        executable=executable,
        change_id=change_id,
        cwd=worktree,
        timeout=timeout,
    )

    def execute(effects: ExternalEffects) -> OpenSpecProgress:
        outputs, process_reason = run_probe(effects, "openspec.facts")
        return assess_probe(change_id=change_id, outputs=outputs, process_reason=process_reason)

    return external_command(steps, execute).run()


def _summary_for(progress: OpenSpecProgress) -> EnvironmentFactsSummary:
    state: Literal["available", "unavailable", "error"]
    if progress.binding_state == "bound":
        state = "available"
        tasks = (
            "no tasks"
            if progress.task_state == "no_tasks"
            else f"{progress.tasks_completed}/{progress.tasks_total}"
        )
        text = (
            f"{progress.change_id} plan {progress.planning_complete}/{progress.planning_total} "
            f"tasks {tasks}"
        )
    elif progress.binding_state in {"missing", "invalid"}:
        state = "error"
        text = progress.reason or progress.binding_state
    else:
        state = "unavailable"
        text = progress.reason or progress.binding_state
    return EnvironmentFactsSummary(
        provider="openspec",
        state=state,
        text=text,
        details=(
            ("change_id", progress.change_id),
            ("binding_state", progress.binding_state),
            ("planning_complete", progress.planning_complete),
            ("planning_total", progress.planning_total),
            ("tasks_completed", progress.tasks_completed),
            ("tasks_total", progress.tasks_total),
            ("implementation_percent", progress.implementation_percent),
            ("reason", progress.reason),
        ),
    )


def _unbound(change_id: str | None) -> OpenSpecProgress:
    return progress_from_parts(
        binding_state="unbound",
        change_id=change_id,
        root=None,
        version=None,
    )


def _failed(change_id: str, reason: str) -> OpenSpecProgress:
    state = "invalid" if reason == "invalid_binding" else "unavailable"
    return progress_from_parts(
        binding_state=state,
        change_id=change_id,
        root=None,
        version=None,
        reason=reason,
    )
