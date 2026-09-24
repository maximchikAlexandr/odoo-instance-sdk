from __future__ import annotations

from typing import Literal

import msgspec

UpdateOutcome = Literal[
    "updated",
    "already_current",
    "unsupported_install",
    "preflight_failed",
    "rolled_back",
    "update_incomplete",
]

UpdatePhaseName = Literal[
    "inspect",
    "resolve",
    "preflight",
    "quiesce",
    "snapshot",
    "install",
    "migrate",
    "verify",
    "commit",
]


class UpdatePhaseDuration(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Per-phase wall-clock duration for one ``odcli update`` run."""

    phase: UpdatePhaseName
    duration_seconds: float


class UpdateResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """The typed contract returned by ``update_command()``.

    No field carries GitHub credentials, environment secrets, project passwords,
    or private config contents.  ``manual_argv`` is a tuple of strings, never a
    shell string, so it cannot be passed to ``shell=True``.
    """

    outcome: UpdateOutcome
    source_repo: str | None = None
    previous_version: str | None = None
    target_version: str | None = None
    final_version: str | None = None
    previous_sha: str | None = None
    target_sha: str | None = None
    final_sha: str | None = None
    executable_path: str | None = None
    tool_env_path: str | None = None
    executed_migration_ids: tuple[str, ...] = ()
    skipped_migration_ids: tuple[str, ...] = ()
    final_schema_versions: dict[str, str] = {}
    snapshot_state: str | None = None
    journal_state: str | None = None
    rollback_outcome: str | None = None
    next_step: str | None = None
    phase_durations: tuple[UpdatePhaseDuration, ...] = ()
    manual_argv: tuple[str, ...] | None = None
    recovery_argv: tuple[str, ...] | None = None


__all__ = [
    "UpdateOutcome",
    "UpdatePhaseDuration",
    "UpdatePhaseName",
    "UpdateResult",
]
