"""Frozen OpenSpec binding and progress models."""

from __future__ import annotations

from typing import Literal

import msgspec

type BindingState = Literal["bound", "missing", "invalid", "unavailable", "unbound"]


class OpenSpecChange(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One active change reported by ``openspec list --json``."""

    name: str
    completed_tasks: int
    total_tasks: int
    status: str
    implementation_percent: int | None


class OpenSpecBinding(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Extension-owned binding for one OdCLI environment.

    The record keeps provenance and the last observation identity. It does
    not copy proposal, spec, design, or task content.
    """

    schema_version: int
    environment_id: str
    change_id: str
    openspec_root: str
    executable: str
    version: str
    bound_at: str
    observation_id: str


class OpenSpecProgress(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Planning progress and implementation-task progress for one row."""

    root: str | None
    change_id: str | None
    binding_state: BindingState
    planning_complete: int | None
    planning_total: int | None
    planning_state: str | None
    tasks_completed: int | None
    tasks_total: int | None
    task_state: str | None
    implementation_percent: int | None
    sampled_at: str
    openspec_version: str | None
    reason: str | None


class OpenSpecCheckoutResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Checkout outcome plus the OpenSpec binding result.

    ``environment_created`` and ``binding_failed`` stay independent so a
    binding failure is not described as a rolled-back checkout.
    """

    environment_created: bool
    binding_failed: bool
    environment_id: str
    environment_name: str
    worktree_path: str
    change_id: str
    reason: str | None = None
    binding: OpenSpecBinding | None = None


class UnbindResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Result of deleting one extension-owned binding file."""

    environment_id: str
    removed: bool
