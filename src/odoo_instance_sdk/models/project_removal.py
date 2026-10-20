"""Public, secret-free models for project lifecycle removal."""

from __future__ import annotations

from typing import Literal

import msgspec

ProjectRemovalMode = Literal["forget", "purge"]
ProjectRegistrationState = Literal["registered", "forgotten"]
ProjectResourceAction = Literal["stop", "unpublish", "delete", "retain", "forget"]


class ProjectSelector(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """The one exact selector accepted by project lifecycle operations."""

    path: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if (self.path is None) == (self.project_id is None):
            raise ValueError("exactly one of path or project_id is required")
        if self.path is not None and not self.path.strip():
            raise ValueError("path must not be empty")
        if self.project_id is not None and not self.project_id.strip():
            raise ValueError("project_id must not be empty")

    @classmethod
    def from_path(cls, path: str) -> ProjectSelector:
        return cls(path=path)

    @classmethod
    def from_project_id(cls, project_id: str) -> ProjectSelector:
        return cls(project_id=project_id)


class ProjectResourceDecision(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
):
    """The deliberately small public projection of one removal decision."""

    identity: str
    resource_type: str
    action: ProjectResourceAction
    reason: str
    target: str
    order: int

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("decision identity must not be empty")
        if not self.resource_type.strip():
            raise ValueError("decision resource_type must not be empty")
        if not self.reason.strip():
            raise ValueError("decision reason must not be empty")
        if self.order < 0:
            raise ValueError("decision order must not be negative")

    @property
    def stable_identity(self) -> str:
        """Alias used by callers that describe the identity as stable."""

        return self.identity


class ProjectRemovalResult(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
):
    """Secret-free result shared by Rich and machine-facing adapters."""

    project_id: str
    mode: ProjectRemovalMode
    registration_state: ProjectRegistrationState
    completed: tuple[ProjectResourceDecision, ...] = ()
    retained: tuple[ProjectResourceDecision, ...] = ()
    remaining: tuple[ProjectResourceDecision, ...] = ()

    @property
    def final_registration_state(self) -> ProjectRegistrationState:
        return self.registration_state


class ProjectRemovalErrorDetails(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
):
    """Structured, bounded diagnostics for a failed removal attempt."""

    project_id: str
    mode: ProjectRemovalMode
    registration_state: ProjectRegistrationState
    completed: tuple[ProjectResourceDecision, ...] = ()
    retained: tuple[ProjectResourceDecision, ...] = ()
    remaining: tuple[ProjectResourceDecision, ...] = ()
    failed_step: str


__all__ = [
    "ProjectRegistrationState",
    "ProjectRemovalErrorDetails",
    "ProjectRemovalMode",
    "ProjectRemovalResult",
    "ProjectResourceAction",
    "ProjectResourceDecision",
    "ProjectSelector",
]
