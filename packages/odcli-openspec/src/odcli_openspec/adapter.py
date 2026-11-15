"""Parse the verified OpenSpec 1.12 and 1.13 JSON CLI shapes."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import cast

from odcli_openspec.errors import OpenSpecError
from odcli_openspec.models import BindingState, OpenSpecChange, OpenSpecProgress

_VERSION = re.compile(r"1\.(12|13)\.\d+\Z")
_CHANGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
type JsonData = bool | int | float | str | list["JsonData"] | dict[str, "JsonData"] | None
type JsonObject = dict[str, JsonData]


def now_stamp() -> str:
    """Return the current UTC timestamp in ISO-8601 form."""
    return datetime.now(UTC).isoformat()


def implementation_percent(completed: int, total: int) -> int | None:
    """Return the half-up integer percentage of completed tasks.

    Args:
        completed: Non-negative completed task count.
        total: Total task count. Zero means the percentage is absent.

    Returns:
        ``None`` when there are no tasks. Otherwise
        ``(completed * 100 + total // 2) // total``.

    Raises:
        OpenSpecError: Task counts are outside the documented range.
    """
    if total <= 0:
        return None
    if completed < 0 or completed > total:
        raise OpenSpecError("openspec_incompatible", "task counts are outside the documented range")
    return (completed * 100 + total // 2) // total


def supported_version(stdout: str) -> str:
    """Return a supported ``openspec --version`` line.

    Args:
        stdout: Raw standard output. Colored or extra text is rejected.

    Returns:
        The stripped version string.

    Raises:
        OpenSpecError: The text is not OpenSpec 1.12 or 1.13.
    """
    text = stdout.strip()
    if _VERSION.fullmatch(text) is None:
        raise OpenSpecError("openspec_incompatible", "OpenSpec version is not 1.12 or 1.13")
    return text


def require_change_id(change: str) -> None:
    """Reject a change id that is a path or an option.

    Args:
        change: Requested OpenSpec change id.

    Raises:
        OpenSpecError: The id is not a single change name.
    """
    if _CHANGE_ID.fullmatch(change) is None or ".." in change:
        raise OpenSpecError(
            "invalid_change",
            "OpenSpec change id must be a single change name, not a path",
        )


def decode_object(stdout: str) -> JsonObject:
    """Decode one JSON object and reject every other payload.

    Args:
        stdout: Standard output that must be a single JSON object.

    Returns:
        The decoded object.

    Raises:
        OpenSpecError: The payload is malformed or not an object.
    """
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise OpenSpecError("malformed_json", "OpenSpec returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise OpenSpecError("openspec_incompatible", "OpenSpec JSON root must be an object")
    return cast("JsonObject", value)


def parse_changes(stdout: str) -> tuple[OpenSpecChange, ...]:
    """Parse ``openspec list --json`` into typed changes.

    Args:
        stdout: Documented list payload.

    Returns:
        Active changes with separate task counts and percentage.
    """
    payload = decode_object(stdout)
    raw_changes = payload.get("changes")
    if not isinstance(raw_changes, list):
        raise OpenSpecError("openspec_incompatible", "OpenSpec list JSON has no changes array")
    parsed: list[OpenSpecChange] = []
    for item in raw_changes:
        parsed.append(_one_change(item))
    return tuple(parsed)


def _one_change(item: JsonData) -> OpenSpecChange:
    if not isinstance(item, dict):
        raise OpenSpecError("openspec_incompatible", "OpenSpec change entry is not an object")
    name = item.get("name")
    completed = item.get("completedTasks")
    total = item.get("totalTasks")
    status = item.get("status")
    if not isinstance(name, str) or not isinstance(status, str):
        raise OpenSpecError("openspec_incompatible", "OpenSpec change identity is not text")
    if not isinstance(completed, int) or isinstance(completed, bool):
        raise OpenSpecError("openspec_incompatible", "completedTasks is not an integer")
    if not isinstance(total, int) or isinstance(total, bool):
        raise OpenSpecError("openspec_incompatible", "totalTasks is not an integer")
    task_state = "no_tasks" if total == 0 else status
    return OpenSpecChange(
        name=name,
        completed_tasks=completed,
        total_tasks=total,
        status=task_state,
        implementation_percent=implementation_percent(completed, total),
    )


def planning_progress(stdout: str, change_id: str) -> tuple[int, int, str, str]:
    """Parse planning counts from ``openspec status --json``.

    Args:
        stdout: Documented status payload.
        change_id: Change that was requested.

    Returns:
        Complete count, total count, planning state, and OpenSpec root path.

    Raises:
        OpenSpecError: The change is missing or the shape is unsupported.
    """
    payload = decode_object(stdout)
    if _status_error(payload, change_id):
        raise OpenSpecError("missing_change", f"OpenSpec change {change_id} was not found")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise OpenSpecError("openspec_incompatible", "OpenSpec status JSON has no artifacts")
    complete = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("status"), str):
            raise OpenSpecError("openspec_incompatible", "OpenSpec artifact status is not text")
        if artifact["status"] in {"done", "skipped"}:
            complete += 1
    root = _root_path(payload)
    state = "complete" if payload.get("isPlanningComplete") is True else "incomplete"
    return complete, len(artifacts), state, root


def _status_error(payload: JsonObject, change_id: str) -> bool:
    status = payload.get("status")
    if not isinstance(status, list):
        reported = payload.get("changeName")
        return isinstance(reported, str) and reported != change_id
    return any(isinstance(item, dict) and item.get("code") == "change_error" for item in status)


def _root_path(payload: JsonObject) -> str:
    root = payload.get("root")
    path = root.get("path") if isinstance(root, dict) else None
    if not isinstance(path, str):
        raise OpenSpecError("openspec_incompatible", "OpenSpec status JSON has no root path")
    return path


def find_change(changes: tuple[OpenSpecChange, ...], change_id: str) -> OpenSpecChange:
    """Return the named change from a list payload.

    Args:
        changes: Parsed active changes.
        change_id: Requested change id.

    Returns:
        The matching change.

    Raises:
        OpenSpecError: The change is absent from the target worktree.
    """
    for change in changes:
        if change.name == change_id:
            return change
    raise OpenSpecError("missing_change", f"OpenSpec change {change_id} was not found")


def observation_id(
    *,
    version: str,
    change_id: str,
    root: str,
    planning_complete: int,
    planning_total: int,
    tasks_completed: int,
    tasks_total: int,
) -> str:
    """Hash the last observation identity without copying OpenSpec documents.

    Args:
        version: Supported OpenSpec version.
        change_id: Bound change id.
        root: OpenSpec root path.
        planning_complete: Completed planning artifacts.
        planning_total: Planning artifact total.
        tasks_completed: Completed implementation tasks.
        tasks_total: Implementation task total.

    Returns:
        SHA-256 hex digest of those fields.
    """
    payload = {
        "change_id": change_id,
        "planning_complete": planning_complete,
        "planning_total": planning_total,
        "root": root,
        "tasks_completed": tasks_completed,
        "tasks_total": tasks_total,
        "version": version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def progress_from_parts(
    *,
    binding_state: str,
    change_id: str | None,
    root: str | None,
    version: str | None,
    planning_complete: int | None = None,
    planning_total: int | None = None,
    planning_state: str | None = None,
    tasks_completed: int | None = None,
    tasks_total: int | None = None,
    task_state: str | None = None,
    implementation_percent_value: int | None = None,
    reason: str | None = None,
) -> OpenSpecProgress:
    """Build one progress row with an explicit sampled timestamp.

    Args:
        binding_state: One of the documented binding states.
        change_id: Bound change id, when known.
        root: OpenSpec root, when known.
        version: OpenSpec version, when known.
        planning_complete: Completed planning artifacts.
        planning_total: Planning artifact total.
        planning_state: Planning state from status JSON.
        tasks_completed: Completed implementation tasks.
        tasks_total: Implementation task total.
        task_state: Task state from list JSON, or ``no_tasks``.
        implementation_percent_value: Half-up percentage, or null.
        reason: Typed failure reason, or null on success.

    Returns:
        The frozen progress row.

    Raises:
        OpenSpecError: ``binding_state`` is not a documented state.
    """
    if binding_state not in {"bound", "missing", "invalid", "unavailable", "unbound"}:
        raise OpenSpecError("openspec_incompatible", "binding state is not supported")
    return OpenSpecProgress(
        root=root,
        change_id=change_id,
        binding_state=cast("BindingState", binding_state),
        planning_complete=planning_complete,
        planning_total=planning_total,
        planning_state=planning_state,
        tasks_completed=tasks_completed,
        tasks_total=tasks_total,
        task_state=task_state,
        implementation_percent=implementation_percent_value,
        sampled_at=now_stamp(),
        openspec_version=version,
        reason=reason,
    )
