from __future__ import annotations

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
