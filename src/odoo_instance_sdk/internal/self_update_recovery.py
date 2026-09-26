"""Small immutable recovery helpers for the self-update coordinator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.proc import PreparedStep, is_process_alive

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.self_update import InstalledProvenance


@dataclass(frozen=True, slots=True)
class UpdateRecoveryState:
    """Read-only projection of canonical interrupted-update evidence."""

    journal_state: Literal["present", "absent"]
    snapshot_state: Literal["present", "absent"]
    target_ref: str | None
    snapshot_sha: str | None
    maintenance_pid: int | None
    maintenance_pid_alive: bool | None
    valid: bool
    diagnostic: str | None = None

    @property
    def has_evidence(self) -> bool:
        return self.journal_state == "present" or self.snapshot_state == "present"

    @property
    def recoverable(self) -> bool:
        return self.valid and self.journal_state == "present" and self.snapshot_state == "present"


def _read_journal(path: Path) -> dict[str, JsonValue] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def inspect_update_recovery_state(user_root: Path) -> UpdateRecoveryState:
    """Inspect recovery paths without creating, locking, or mutating them."""
    from odoo_instance_sdk.internal.self_update import _JOURNAL_PHASES, _is_full_sha

    root = user_root / "update"
    journal_path = root / "journal.json"
    snapshot_dir = root / "snapshot"
    journal_present = journal_path.exists()
    snapshot_present = snapshot_dir.exists()
    journal = _read_journal(journal_path) if journal_present else None
    if not journal_present and not snapshot_present:
        return UpdateRecoveryState(
            journal_state="absent",
            snapshot_state="absent",
            target_ref=None,
            snapshot_sha=None,
            maintenance_pid=None,
            maintenance_pid_alive=None,
            valid=True,
        )
    if journal is None:
        return UpdateRecoveryState(
            journal_state="present" if journal_present else "absent",
            snapshot_state="present" if snapshot_present else "absent",
            target_ref=None,
            snapshot_sha=None,
            maintenance_pid=None,
            maintenance_pid_alive=None,
            valid=False,
            diagnostic="update recovery journal is unreadable or not a JSON object",
        )
    phase = journal.get("phase")
    raw_target_ref = journal.get("target_ref")
    raw_snapshot_sha = journal.get("snapshot_sha")
    raw_pid = journal.get("maintenance_pid")
    target_ref = (
        raw_target_ref.lower()
        if isinstance(raw_target_ref, str) and _is_full_sha(raw_target_ref)
        else None
    )
    snapshot_sha = (
        raw_snapshot_sha.lower()
        if isinstance(raw_snapshot_sha, str) and _is_full_sha(raw_snapshot_sha)
        else None
    )
    maintenance_pid = (
        raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None
    )
    if raw_pid is not None and maintenance_pid is None:
        diagnostic = "update recovery journal has malformed maintenance_pid"
    elif phase not in _JOURNAL_PHASES:
        diagnostic = "update recovery journal has an invalid phase"
    elif target_ref is None:
        diagnostic = "update recovery journal has no immutable target_ref"
    elif snapshot_sha is None:
        diagnostic = "update recovery journal has no immutable snapshot_sha"
    else:
        diagnostic = None
    return UpdateRecoveryState(
        journal_state="present" if journal_present else "absent",
        snapshot_state="present" if snapshot_present else "absent",
        target_ref=target_ref,
        snapshot_sha=snapshot_sha,
        maintenance_pid=maintenance_pid,
        maintenance_pid_alive=(
            is_process_alive(maintenance_pid) if maintenance_pid is not None else None
        ),
        valid=diagnostic is None,
        diagnostic=diagnostic,
    )


def _full_sha(value: str) -> bool:
    from odoo_instance_sdk.internal.self_update import _is_full_sha

    return _is_full_sha(value)


def journal_resume_phase(journal: dict[str, JsonValue] | None) -> str | None:
    if journal is None:
        return None
    phase = journal.get("phase")
    return phase if isinstance(phase, str) else None


def journal_target_ref(journal: dict[str, JsonValue] | None) -> str | None:
    if journal is None:
        return None
    raw = journal.get("target_ref")
    if isinstance(raw, str) and _full_sha(raw):
        return raw.lower()
    from odoo_instance_sdk.exceptions import UpdateError

    raise UpdateError("unfinished update journal has no immutable target_ref")


def journal_snapshot_sha(
    journal: dict[str, JsonValue] | None,
    provenance: InstalledProvenance,
) -> str | None:
    if journal is None:
        return provenance.commit_id
    raw = journal.get("snapshot_sha")
    if isinstance(raw, str) and _full_sha(raw):
        return raw.lower()
    from odoo_instance_sdk.exceptions import UpdateError

    raise UpdateError("unfinished update journal has no immutable snapshot_sha")


def recovery_step_for_snapshot(snapshot_sha: str | None) -> PreparedStep:
    from odoo_instance_sdk.internal.self_update import _DEFAULT_REF, _install_argv

    return PreparedStep(
        step_id="update.recovery",
        argv=_install_argv(snapshot_sha or _DEFAULT_REF),
        mutating=True,
    )
