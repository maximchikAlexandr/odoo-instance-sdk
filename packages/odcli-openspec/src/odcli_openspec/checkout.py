"""Compose checkout and OpenSpec binding into one command snapshot."""

from __future__ import annotations

import uuid
from typing import Protocol, TypeVar

import msgspec

from odcli_openspec.errors import OpenSpecError
from odcli_openspec.models import OpenSpecBinding, OpenSpecCheckoutResult, OpenSpecProgress
from odcli_openspec.probes import (
    assess_probe,
    bind_action,
    binding_from_progress,
    openspec_executable,
    planned_worktree,
    probe_steps,
    run_probe,
    same_worktree,
)
from odcli_openspec.store import write_binding
from odoo_instance_sdk.execution import (
    ActionStep,
    Command,
    ExecutionStep,
    ExternalEffects,
    continue_command,
)

_CHECKOUT_TIMEOUT = 5.0


class CheckoutTarget(Protocol):
    """Fields checkout must provide before a binding can be stored."""

    @property
    def id(self) -> uuid.UUID:
        """Canonical environment id."""

    @property
    def name(self) -> str:
        """Human-readable environment name."""

    @property
    def worktree_path(self) -> str:
        """Worktree that is authoritative for the OpenSpec change."""


_TargetT = TypeVar("_TargetT", bound=CheckoutTarget)


def compose_checkout_command(
    checkout: Command[_TargetT],
    change: str,
) -> Command[OpenSpecCheckoutResult]:
    """Return one checkout command that also validates and binds a change.

    Args:
        checkout: Captured core checkout command.
        change: OpenSpec change id.

    Returns:
        The combined command. A binding failure still returns the created
        environment instead of claiming that checkout was rolled back.
    """
    worktree = _planned_worktree(checkout)
    environment_id = _planned_environment_id(checkout)
    executable = openspec_executable()
    steps: tuple[ExecutionStep, ...] = (
        *probe_steps(
            prefix="openspec.checkout",
            executable=executable,
            change_id=change,
            cwd=worktree,
            timeout=_CHECKOUT_TIMEOUT,
        ),
        bind_action(environment_id or "pending", change),
    )

    def execute(environment: _TargetT, effects: ExternalEffects) -> OpenSpecCheckoutResult:
        outputs, process_reason = run_probe(effects, "openspec.checkout")
        progress = assess_probe(
            change_id=change,
            outputs=outputs,
            process_reason=process_reason,
        )
        result = OpenSpecCheckoutResult(
            environment_created=True,
            binding_failed=True,
            environment_id=str(environment.id),
            environment_name=environment.name,
            worktree_path=environment.worktree_path,
            change_id=change,
            reason=progress.reason,
        )
        effects.begin("openspec.bind")
        failure = _binding_failure(environment, environment_id, worktree, progress)
        binding: OpenSpecBinding | None = None
        if failure is None:
            try:
                binding = binding_from_progress(
                    environment_id=str(environment.id),
                    executable=executable,
                    progress=progress,
                )
                write_binding(binding)
            except OpenSpecError as exc:
                failure = exc
            except OSError:
                failure = OpenSpecError("binding_io", "OpenSpec binding file could not be written")
        if failure is not None or binding is None:
            effects.fail(
                "openspec.bind",
                failure or OpenSpecError("invalid_change", "OpenSpec binding failed"),
            )
            return msgspec.structs.replace(
                result, reason=(failure.reason if failure else "invalid_change")
            )
        effects.complete("openspec.bind")
        return msgspec.structs.replace(result, binding_failed=False, reason=None, binding=binding)

    return continue_command(checkout, steps, execute)


def _binding_failure(
    environment: CheckoutTarget,
    environment_id: str | None,
    worktree: str,
    progress: OpenSpecProgress,
) -> OpenSpecError | None:
    if environment_id is not None and environment_id != str(environment.id):
        return OpenSpecError(
            "environment_mismatch", "checkout environment id does not match the plan"
        )
    if not same_worktree(worktree, environment.worktree_path):
        return OpenSpecError("worktree_mismatch", "checkout worktree does not match the plan")
    if progress.binding_state != "bound":
        return OpenSpecError(progress.reason or "invalid_change", "OpenSpec binding failed")
    return None


def _planned_worktree(checkout: Command[_TargetT]) -> str:
    for step in checkout.plan.process_steps:
        if step.step_id == "checkout.worktree":
            return planned_worktree(step.argv)
    raise OpenSpecError("worktree_missing", "checkout plan has no worktree step")


def _planned_environment_id(checkout: Command[_TargetT]) -> str | None:
    for step in checkout.plan.steps:
        if isinstance(step, ActionStep) and step.step_id == "checkout.catalog":
            details = step.details
            if isinstance(details, dict):
                value = details.get("environment_id")
                if isinstance(value, str):
                    return value
    return None
