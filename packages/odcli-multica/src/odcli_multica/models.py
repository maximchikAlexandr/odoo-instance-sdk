"""Frozen request and observation models for the stateless integration."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import msgspec

MULTICA_PY_VERSION = "0.1.0"
MULTICA_PY_REVISION = "c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed"
MULTICA_CLI_MINIMUM = "0.5.3"


@dataclass(frozen=True, slots=True)
class MulticaCompatibility:
    """Verified package/CLI identity required before a native operation."""

    package_version: str
    package_revision: str
    native_cli_version: str
    typed_checkout: bool
    typed_daemon_status: bool
    observed: bool


class ContextRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Explicit identities used to verify one native task checkout."""

    checkout_path: Path
    core_project: Path
    multica_project: str
    issue: str
    run: str
    repository_url: str | None = None
    core_repository_url: str | None = None
    workspace_id: str | None = None
    runtime_id: str | None = None


class PreparationRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """The caller-owned inputs for the second, core-adoption phase."""

    context: ContextRequest
    base_ref: str
    remote_name: str | None = None
    backup_id: uuid.UUID | str | None = None
    source_database: str | None = None


class VerifiedTaskContext(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Finite same-host evidence; this is not a lease or persisted binding."""

    checkout_path: str
    task_root: str
    repository_url: str
    workspace_id: str
    multica_project_id: str
    issue_id: str
    run_id: str
    runtime_id: str
    daemon_id: str
    observed_at: datetime
    root_issue_id: str | None = None
    root_creator_id: str | None = None


class RootCreatorContext(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """The human authority proven by a finite issue-parent traversal."""

    issue_id: str
    root_issue_id: str
    root_creator_id: str
    workspace_id: str


class GitLabCredentialMapping(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """Non-secret mapping from one Multica user and host to a token key."""

    user_id: str
    host: str
    login: str
    token_key: str


class GitLabCredentialIdentity(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """Safe public identity for one resolved host-scoped credential."""

    user_id: str
    host: str
    login: str
    token_key: str
    workspace_id: str
    issue_id: str
    root_issue_id: str


@dataclass(frozen=True, slots=True, repr=False)
class GitLabCredentialContext:
    """Private per-command credential handle with a secret-free representation."""

    identity: GitLabCredentialIdentity
    _token: str = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return f"GitLabCredentialContext(identity={self.identity!r})"

    @property
    def user_id(self) -> str:
        return self.identity.user_id

    @property
    def host(self) -> str:
        return self.identity.host

    @property
    def login(self) -> str:
        return self.identity.login

    def child_environment(self) -> Mapping[str, str]:
        """Return an immutable private child snapshot; never include it in output."""
        return MappingProxyType({"GITLAB_LOGIN": self.login, "GITLAB_TOKEN": self._token})

    def askpass_environment(self, askpass: str) -> Mapping[str, str]:
        """Return the private environment used by one Git child."""
        return MappingProxyType(
            {
                "GITLAB_LOGIN": self.login,
                "GITLAB_TOKEN": self._token,
                "GIT_ASKPASS": askpass,
                "GIT_TERMINAL_PROMPT": "0",
            }
        )


class ContextVerificationError(ValueError):
    """Raised when typed task evidence is absent, conflicting, or unsafe."""


class MergeRequestPublicationResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The stable public result of one GitLab merge-request publication."""

    merge_request_id: int
    web_url: str
    source_branch: str
    target_branch: str
    outcome: Literal["created", "updated"]


TaskContext = VerifiedTaskContext
