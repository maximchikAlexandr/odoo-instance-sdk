from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

import msgspec

from odoo_instance_sdk.models.runtime import GitActivityState


class GitDiff(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    added: int
    deleted: int


class GitActivity(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    default_branch: str
    head_sha: str | None
    short_sha: str | None
    branch: str
    ahead: int | None
    behind: int | None
    diff: GitDiff | None
    state: GitActivityState


class GitCommitContext(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    module: str
    tag: str
    description: str
    ticket: str | None
    ticket_link: str | None
    message: str
    staged_paths: tuple[str, ...]
    branch: str
    repository: str
    command: tuple[str, ...]
    staged_entries: tuple[tuple[str, tuple[str, ...]], ...] = ()
    unrelated_paths: tuple[str, ...] = ()
    index_tree: str = ""

    @property
    def scope(self) -> str:
        return self.module


class GitCheckIssue(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    code: str
    message: str
    commit: str | None = None
    paths: tuple[str, ...] = ()


class GitCheckResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    base: str
    branch: str
    valid: bool
    commits: tuple[str, ...] = ()
    issues: tuple[GitCheckIssue, ...] = ()
    pending_fixups: tuple[str, ...] = ()


class GitAbsorbResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    executable: str
    base: str
    returncode: int
    stdout: str
    stderr: str
    unmapped_hunks: tuple[str, ...] = ()


class GitSyncResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    branch: str
    base: str
    fetched_sha: str | None
    rebased: bool
    pushed: bool
    returncode: int = 0
    guidance: tuple[str, ...] = ()


class GitLabCredentialMapping(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """Non-secret mapping for one selected member and GitLab host."""

    user_id: str
    host: str
    login: str
    token_key: str


class GitLabCredentialIdentity(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """Public identity bound to a host-scoped credential.

    The optional lineage fields keep the extension package source-compatible;
    core operations never populate them from ambient Multica state.
    """

    user_id: str
    host: str
    login: str
    token_key: str
    workspace_id: str | None = None
    issue_id: str | None = None
    root_issue_id: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class GitLabCredentialContext:
    """Private credential snapshot; the token is intentionally not public."""

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
        """Return private child values without making them part of projections."""
        return MappingProxyType({"GITLAB_LOGIN": self.login, "GITLAB_TOKEN": self._token})

    def _redaction_secrets(self) -> tuple[str, ...]:
        """Return the private values needed by an existing diagnostic boundary."""
        return (self._token,)

    def askpass_environment(self, askpass: str, *, channel_path: str) -> Mapping[str, str]:
        return MappingProxyType(
            {
                "GITLAB_LOGIN": self.login,
                "GIT_ASKPASS": askpass,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull,
                "ODCLI_GITLAB_HOST": self.host,
                "ODCLI_GITLAB_TOKEN_FILE": channel_path,
            }
        )

    def public_projection(self) -> GitLabCredentialIdentity:
        """Return the only credential projection allowed in plans/results."""
        return self.identity


class GitLabIdentity(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Verified GitLab API identity and commit identity."""

    user_id: int
    username: str
    name: str
    email: str
    host: str

    @property
    def login(self) -> str:
        return self.username

    @property
    def author_name(self) -> str:
        return self.name

    @property
    def author_email(self) -> str:
        return self.email

    @property
    def committer_name(self) -> str:
        return self.name

    @property
    def committer_email(self) -> str:
        return self.email


GitLabVerifiedIdentity = GitLabIdentity


class GitRepositorySnapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Immutable, public repository evidence captured before a Git operation."""

    canonical_root: str
    git_common_dir: str
    origin: str
    host: str
    project: str
    branch: str | None
    staged_paths: tuple[str, ...] = ()
    worktree_dirty: bool = False
    initialized_odoo: bool = False

    @property
    def repository_root(self) -> str:
        return self.canonical_root

    @property
    def root(self) -> str:
        return self.canonical_root

    @property
    def common_dir(self) -> str:
        return self.git_common_dir

    @property
    def origin_url(self) -> str:
        return self.origin

    @property
    def project_path(self) -> str:
        return self.project

    @property
    def odoo_initialized(self) -> bool:
        return self.initialized_odoo


RepositorySnapshot = GitRepositorySnapshot


class GitCloneResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    destination: str
    origin: str
    reused: bool
    initialized_submodules: tuple[str, ...] = ()


class GitCommitResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    repository: str
    commit: str
    subject: str
    author: GitLabIdentity
    committer: GitLabIdentity


class GitUploadResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    project: str
    markdown: str
    digest: str


class GitMergeRequestResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    project: str
    merge_request_id: int
    web_url: str
    source_branch: str
    target_branch: str
    outcome: Literal["created", "updated"]


GitLabUserIdentity = GitLabIdentity
RepositoryResolution = GitRepositorySnapshot
