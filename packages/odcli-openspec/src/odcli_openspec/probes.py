"""Build and interpret one captured OpenSpec probe."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from odcli_openspec.adapter import (
    find_change,
    observation_id,
    parse_changes,
    planning_progress,
    progress_from_parts,
    supported_version,
)
from odcli_openspec.errors import OpenSpecError
from odcli_openspec.models import OpenSpecBinding, OpenSpecProgress
from odoo_instance_sdk.execution import (
    ActionStep,
    CapturedProcessOutput,
    ExternalEffects,
    ProcessStep,
)

PROBE_SUFFIXES = ("version", "validate", "status", "list")


def openspec_executable() -> str:
    """Return the absolute OpenSpec executable, or the bare name when absent."""
    return shutil.which("openspec") or "openspec"


def trusted_executable(stored: str) -> str:
    """Return the current OpenSpec executable when a binding still names it.

    Args:
        stored: Executable recorded when the environment was bound.

    Returns:
        The executable resolved from the current PATH.

    Raises:
        OpenSpecError: The stored path is not the current ``openspec`` binary.
    """
    current = openspec_executable()
    if stored in {current, "openspec"} and Path(current).name == "openspec":
        return current
    try:
        same = Path(stored).name == "openspec" and Path(stored).resolve() == Path(current).resolve()
    except OSError:
        same = False
    if not same:
        raise OpenSpecError(
            "missing_executable",
            "stored OpenSpec executable does not match the current openspec",
        )
    return current


def planned_worktree(argv: tuple[str, ...]) -> str:
    """Return the worktree path from a captured ``git worktree add`` argv.

    Args:
        argv: Public checkout.worktree arguments.

    Returns:
        The worktree path argument.

    Raises:
        OpenSpecError: The argv is not a worktree add command.
    """
    try:
        add_at = argv.index("add")
    except ValueError as exc:
        raise OpenSpecError("worktree_missing", "checkout plan has no worktree add step") from exc
    path_at = add_at + 3 if add_at + 1 < len(argv) and argv[add_at + 1] == "-b" else add_at + 1
    if path_at >= len(argv) or argv[path_at].startswith("-"):
        raise OpenSpecError("worktree_missing", "checkout plan has no worktree path")
    return argv[path_at]


def probe_steps(
    *,
    prefix: str,
    executable: str,
    change_id: str,
    cwd: str,
    timeout: float,
) -> tuple[ProcessStep, ...]:
    """Return the version, validate, status, and list steps for one change.

    Args:
        prefix: Unique step-id prefix.
        executable: Resolved OpenSpec executable.
        change_id: OpenSpec change id.
        cwd: Target worktree that is authoritative for this probe.
        timeout: Per-process timeout in seconds.

    Returns:
        Four read-only process steps.
    """
    commands = {
        "version": (executable, "--version"),
        "validate": (
            executable,
            "validate",
            change_id,
            "--type",
            "change",
            "--json",
            "--no-interactive",
        ),
        "status": (executable, "status", "--change", change_id, "--json"),
        "list": (executable, "list", "--json"),
    }
    return tuple(
        ProcessStep(
            step_id=f"{prefix}.{suffix}",
            argv=argv,
            display=" ".join(argv),
            executable=executable,
            cwd=cwd,
            timeout=timeout,
            read_only=True,
        )
        for suffix, argv in commands.items()
    )


def bind_action(environment_id: str, change_id: str) -> ActionStep:
    """Return the in-process step that writes one binding file.

    Args:
        environment_id: Canonical environment id.
        change_id: OpenSpec change id.

    Returns:
        The mutating action step.
    """
    return ActionStep(
        step_id="openspec.bind",
        action="write_binding",
        description="Persist the OpenSpec environment binding",
        details={"environment_id": environment_id, "change_id": change_id},
        mutating=True,
    )


def failure_reason(exc: Exception) -> str:
    """Map a process exception to a typed OpenSpec reason.

    Args:
        exc: Exception raised while running a planned step.

    Returns:
        ``timeout``, ``missing_executable``, or ``non_zero``.
    """
    name = type(exc).__name__
    if "Timeout" in name:
        return "timeout"
    if "Spawn" in name or isinstance(exc, FileNotFoundError):
        return "missing_executable"
    return "non_zero"


def run_probe(
    effects: ExternalEffects,
    prefix: str,
) -> tuple[dict[str, CapturedProcessOutput], str | None]:
    """Run the four probe steps, skipping the rest after a missing executable.

    Args:
        effects: Effects for the command that captured the steps.
        prefix: Step-id prefix used by ``probe_steps``.

    Returns:
        Captured outputs and the first process-level reason, if any.
    """
    outputs: dict[str, CapturedProcessOutput] = {}
    reason: str | None = None
    for suffix in PROBE_SUFFIXES:
        step_id = f"{prefix}.{suffix}"
        if reason in {"missing_executable", "timeout"}:
            effects.skip(step_id)
            continue
        try:
            outputs[suffix] = effects.process(step_id)
        except Exception as exc:
            reason = failure_reason(exc)
    return outputs, reason


def assess_probe(
    *,
    change_id: str,
    outputs: dict[str, CapturedProcessOutput],
    process_reason: str | None,
) -> OpenSpecProgress:
    """Turn probe output into planning and implementation progress.

    Args:
        change_id: Requested change id.
        outputs: Captured probe streams keyed by suffix.
        process_reason: Failure from launching a probe, if any.

    Returns:
        Progress with a null percentage whenever the probe is not a clean
        bound observation.
    """
    if process_reason is not None:
        return _failed(change_id, process_reason)
    version_output = outputs.get("version")
    validate_output = outputs.get("validate")
    status_output = outputs.get("status")
    list_output = outputs.get("list")
    if None in {version_output, validate_output, status_output, list_output}:
        return _failed(change_id, "non_zero")
    assert version_output is not None
    assert validate_output is not None
    assert status_output is not None
    assert list_output is not None
    try:
        version = supported_version(version_output.stdout)
        _require_valid(validate_output)
        planning_complete, planning_total, planning_state, root = planning_progress(
            status_output.stdout, change_id
        )
        change = find_change(parse_changes(list_output.stdout), change_id)
    except OpenSpecError as exc:
        return _failed(change_id, exc.reason, version=_version_or_none(version_output.stdout))
    return progress_from_parts(
        binding_state="bound",
        change_id=change_id,
        root=root,
        version=version,
        planning_complete=planning_complete,
        planning_total=planning_total,
        planning_state=planning_state,
        tasks_completed=change.completed_tasks,
        tasks_total=change.total_tasks,
        task_state=change.status,
        implementation_percent_value=change.implementation_percent,
    )


def binding_from_progress(
    *,
    environment_id: str,
    executable: str,
    progress: OpenSpecProgress,
    bound_at: str | None = None,
) -> OpenSpecBinding:
    """Build the persisted binding from a successful probe.

    Args:
        environment_id: Canonical environment id.
        executable: Resolved OpenSpec executable.
        progress: Successful bound progress row.
        bound_at: Override for tests. Defaults to the current UTC time.

    Returns:
        The binding record.

    Raises:
        OpenSpecError: The progress row is not a complete bound observation.
    """
    if (
        progress.binding_state != "bound"
        or progress.root is None
        or progress.openspec_version is None
        or progress.change_id is None
        or progress.planning_complete is None
        or progress.planning_total is None
        or progress.tasks_completed is None
        or progress.tasks_total is None
    ):
        raise OpenSpecError("invalid_change", "OpenSpec probe did not produce a binding")
    return OpenSpecBinding(
        schema_version=1,
        environment_id=environment_id,
        change_id=progress.change_id,
        openspec_root=progress.root,
        executable=executable,
        version=progress.openspec_version,
        bound_at=bound_at or datetime.now(UTC).isoformat(),
        observation_id=observation_id(
            version=progress.openspec_version,
            change_id=progress.change_id,
            root=progress.root,
            planning_complete=progress.planning_complete,
            planning_total=progress.planning_total,
            tasks_completed=progress.tasks_completed,
            tasks_total=progress.tasks_total,
        ),
    )


def same_worktree(planned: str, actual: str) -> bool:
    """Return whether two checkout paths identify the same directory.

    Args:
        planned: Path captured in the checkout plan.
        actual: Path returned by checkout execution.
    """
    return Path(planned).resolve() == Path(actual).resolve()


def _require_valid(output: CapturedProcessOutput) -> None:
    if output.returncode != 0:
        raise OpenSpecError("invalid_change", "OpenSpec validation failed")


def _failed(
    change_id: str,
    reason: str,
    *,
    version: str | None = None,
) -> OpenSpecProgress:
    state = (
        "missing"
        if reason == "missing_change"
        else "invalid"
        if reason == "invalid_change"
        else "unavailable"
    )
    return progress_from_parts(
        binding_state=state,
        change_id=change_id,
        root=None,
        version=version,
        reason=reason,
    )


def _version_or_none(stdout: str) -> str | None:
    try:
        return supported_version(stdout)
    except OpenSpecError:
        return None
