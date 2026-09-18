from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.internal.applied_settings import (
    AppliedSettingsError,
    decode_applied_settings,
    encode_applied_settings,
)
from odoo_instance_sdk.internal.git_activity import collect_git_activity
from odoo_instance_sdk.internal.git_worktree import (
    _run as _run_git,
)
from odoo_instance_sdk.internal.git_worktree import (
    worktree_is_dirty,
)
from odoo_instance_sdk.resources.environment.checkout_planning import _git_ticket

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue

from odoo_instance_sdk.internal.doctor.manifest import _DriftComponent

_REMEDIATION = {
    "python": "run odcli env sync",
    "dependencies": "run odcli env sync",
    "odoo_config": "recreate the environment",
    "addons": "recreate the environment",
    "git_provenance": "recreate the environment",
}


def _live_git_component(worktree: Path, base_ref: str) -> JsonValue | None:
    if not worktree.is_dir():
        return None
    try:
        branch_proc = _run_git(
            ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"],
            check=False,
        )
        base_proc = _run_git(
            ["git", "-C", str(worktree), "rev-parse", "--verify", base_ref],
            check=False,
        )
    except (OSError, ValueError):
        return None
    branch = branch_proc.stdout.strip()
    if branch_proc.returncode != 0 or not branch or base_proc.returncode != 0:
        return None
    # The ref name is persisted as provenance; rev-parse proves it is live
    # without introducing a fetch or relying on mutable remote state.
    return _git_component(branch, base_ref)


def _git_component(branch: str, base_ref: str) -> JsonValue | None:
    document = decode_applied_settings(
        encode_applied_settings(
            git={"ticket": _git_ticket(branch), "branch": branch, "base": base_ref}
        )
    )
    components = document["components"]
    if not isinstance(components, dict):
        return None
    return components.get("git")


def _stored_drift_components(raw: str | None) -> dict[str, JsonValue] | None:
    if not isinstance(raw, str):
        return None
    try:
        document = decode_applied_settings(raw)
    except AppliedSettingsError:
        return None
    components = document.get("components")
    if not isinstance(components, dict):
        return None
    return {
        "python": components.get("python"),
        "dependencies": components.get("dependencies"),
        "odoo_config": components.get("odoo"),
        "addons": components.get("addons"),
        "git_provenance": components.get("git"),
    }


_UNKNOWN_REASONS = {
    "python": "Python selector or artifact is unavailable",
    "dependencies": "dependency input is unavailable",
    "odoo_config": "source or generated Odoo config is unavailable",
    "addons": "source or generated add-on paths are unavailable",
    "git_provenance": "live branch/base identity is unavailable",
}


def _difference_reason(name: str, current: JsonValue, stored: JsonValue) -> str:
    if name == "python" and isinstance(current, dict) and isinstance(stored, dict):
        for field, reason in (
            ("selector", "Python selector differs"),
            ("path", "Python artifact path differs"),
            ("owned", "Python ownership differs"),
        ):
            if current.get(field) != stored.get(field):
                return reason
    if name == "git_provenance" and isinstance(current, dict) and isinstance(stored, dict):
        if current.get("branch") != stored.get("branch"):
            return "live branch differs"
        if current.get("base") != stored.get("base"):
            return "live base differs"
    return {
        "dependencies": "dependency input identity or fingerprint differs",
        "odoo_config": "managed Odoo value differs",
        "addons": "add-on path differs",
        "git_provenance": "live branch/base differs",
    }.get(name, "current evidence differs")


def _drift_component(
    name: str,
    current: JsonValue,
    stored: JsonValue | None,
    *,
    reason: str | None = None,
) -> _DriftComponent:
    remediation = _REMEDIATION[name]
    if not isinstance(current, dict) or current.get("status") != "known":
        return _DriftComponent(name, "unknown", _UNKNOWN_REASONS[name], remediation)
    if not isinstance(stored, dict) or stored.get("status") != "known":
        return _DriftComponent(
            name,
            "unknown",
            f"applied {name} evidence is unavailable",
            remediation,
        )
    if reason is not None:
        return _DriftComponent(name, "drifted", reason, remediation)
    if current != stored:
        return _DriftComponent(
            name,
            "drifted",
            reason or _difference_reason(name, current, stored),
            remediation,
        )
    return _DriftComponent(name, "in_sync", "current evidence matches applied", remediation)


def _git_context(worktree: Path, base_ref: str) -> Mapping[str, JsonValue]:
    if not worktree.is_dir():
        return {"dirty": None, "ahead": None, "behind": None}
    try:
        activity = collect_git_activity(worktree, base_ref=base_ref)
        return {
            "dirty": worktree_is_dirty(worktree),
            "ahead": activity.ahead,
            "behind": activity.behind,
        }
    except Exception:
        return {"dirty": None, "ahead": None, "behind": None}
