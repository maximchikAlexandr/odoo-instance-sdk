"""Focused lineage, credential-mapping, and Git boundary policies."""

from __future__ import annotations

import json
import re
import shlex
import stat
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

from odcli_multica.models import (
    ContextVerificationError,
    GitLabCredentialMapping,
    RootCreatorContext,
    VerifiedTaskContext,
)
from odoo_instance_sdk.execution import JsonValue

if TYPE_CHECKING:
    from multica_py import Issue


class IssueStore(Protocol):
    def get(self, issue_id: str) -> Issue: ...


class BranchResultLike(Protocol):
    returncode: int
    stdout: str


class BranchCommandLike(Protocol):
    def run(self) -> BranchResultLike: ...


class GitResourceLike(Protocol):
    def passthrough(self, args: Sequence[str]) -> BranchCommandLike | BranchResultLike: ...


def canonical_checkout(value: Path) -> Path:
    try:
        checkout = value.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ContextVerificationError("checkout path is unavailable") from exc
    if not checkout.is_dir():
        raise ContextVerificationError("checkout path is not a directory")
    return checkout


def optional_root_creator(
    issue: Issue,
    issue_id: str,
    workspace_id: str,
    project_id: str,
    issues: IssueStore,
) -> RootCreatorContext | None:
    if issue.creator_type is None and issue.creator_id is None:
        return None
    return resolve_root_creator_from_issue(
        issue,
        issue_id=issue_id,
        workspace_id=workspace_id,
        project_id=project_id,
        issues=issues,
    )


def resolve_root_creator(context: VerifiedTaskContext, issues: IssueStore) -> RootCreatorContext:
    try:
        issue = issues.get(context.issue_id)
    except Exception as exc:
        raise ContextVerificationError("issue lineage evidence is unavailable") from exc
    return resolve_root_creator_from_issue(
        issue,
        issue_id=context.issue_id,
        workspace_id=context.workspace_id,
        project_id=context.multica_project_id,
        issues=issues,
    )


def resolve_root_creator_from_issue(
    first_issue: Issue,
    *,
    issue_id: str,
    workspace_id: str,
    project_id: str,
    issues: IssueStore,
) -> RootCreatorContext:
    visited: set[str] = set()
    current_id = issue_id
    issue = first_issue
    while True:
        if current_id in visited:
            raise ContextVerificationError("issue lineage is cyclic")
        visited.add(current_id)
        verify_lineage_issue(issue, workspace_id=workspace_id, project_id=project_id)
        parent_id = issue.parent_id
        if parent_id is None or not parent_id.strip():
            creator_type = (issue.creator_type or "").strip().casefold()
            creator_id = (issue.creator_id or "").strip()
            if creator_type != "member" or not creator_id:
                raise ContextVerificationError("root issue human creator evidence is unavailable")
            return RootCreatorContext(
                issue_id=issue_id,
                root_issue_id=current_id,
                root_creator_id=creator_id,
                workspace_id=workspace_id,
            )
        current_id = parent_id.strip()
        if current_id in visited:
            raise ContextVerificationError("issue lineage is cyclic")
        try:
            issue = issues.get(current_id)
        except Exception as exc:
            raise ContextVerificationError("parent issue evidence is unavailable") from exc


def verify_lineage_issue(issue: Issue, *, workspace_id: str, project_id: str) -> None:
    if issue.project_id != project_id:
        raise ContextVerificationError("issue lineage crosses the verified workspace")
    issue_workspace = getattr(issue, "workspace_id", None)
    if issue_workspace is not None and issue_workspace != workspace_id:
        raise ContextVerificationError("issue lineage crosses the verified workspace")


def default_mapping_path(project_root: Path) -> Path:
    return project_root.expanduser().resolve() / ".odcli" / "gitlab-credentials.toml"


def read_gitlab_credential_mappings(path: Path | str) -> tuple[GitLabCredentialMapping, ...]:
    path = Path(path)
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ContextVerificationError("GitLab credential mapping is unavailable") from exc
    if path.is_symlink() or not path.is_file() or (stat.S_IMODE(metadata.st_mode) & 0o077):
        raise ContextVerificationError("GitLab credential mapping requires owner-only access")
    try:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if path.suffix.casefold() == ".json" else tomllib.loads(text)
    except (OSError, UnicodeError) as exc:
        raise ContextVerificationError("GitLab credential mapping is unavailable") from exc
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        raise ContextVerificationError("GitLab credential mapping is malformed") from exc
    mappings: list[GitLabCredentialMapping] = []
    seen: set[tuple[str, str]] = set()
    for entry in mapping_entries(raw):
        if not isinstance(entry, Mapping):
            raise ContextVerificationError("GitLab credential mapping is malformed")
        try:
            user_id = mapping_text(entry, "user_id")
            host = gitlab_host(mapping_text(entry, "host"))
            login = mapping_text(entry, "login")
            token_key = mapping_text(entry, "token_key")
        except (KeyError, TypeError, ValueError) as exc:
            raise ContextVerificationError("GitLab credential mapping is malformed") from exc
        if not re.fullmatch(r"ODCLI_GITLAB_TOKEN_[A-Za-z0-9_]+", token_key):
            raise ContextVerificationError("GitLab credential token key is malformed")
        if (user_id, host) in seen:
            raise ContextVerificationError("GitLab credential mapping is ambiguous")
        seen.add((user_id, host))
        mappings.append(
            GitLabCredentialMapping(user_id=user_id, host=host, login=login, token_key=token_key)
        )
    return tuple(mappings)


def mapping_entries(raw: JsonValue) -> tuple[JsonValue, ...]:
    if isinstance(raw, list):
        return tuple(raw)
    if not isinstance(raw, Mapping):
        raise ContextVerificationError("GitLab credential mapping is malformed")
    named = named_mapping_entries(raw)
    if named is not None:
        return named
    users = raw.get("users")
    if isinstance(users, Mapping):
        return user_mapping_entries(users)
    if {"user_id", "host", "login", "token_key"}.issubset(raw):
        return (raw,)
    raise ContextVerificationError("GitLab credential mapping is malformed")


def named_mapping_entries(raw: Mapping[str, JsonValue]) -> tuple[JsonValue, ...] | None:
    for key in ("mappings", "credentials", "credential", "gitlab"):
        value = raw.get(key)
        if isinstance(value, list):
            return tuple(value)
        if isinstance(value, Mapping):
            return (value,)
    return None


def user_mapping_entries(users: Mapping[str, JsonValue]) -> tuple[JsonValue, ...]:
    values: list[dict[str, JsonValue]] = []
    for user_id, hosts in users.items():
        if not isinstance(hosts, Mapping):
            raise ContextVerificationError("GitLab credential mapping is malformed")
        for host, details in hosts.items():
            if not isinstance(details, Mapping):
                raise ContextVerificationError("GitLab credential mapping is malformed")
            values.append({"user_id": user_id, "host": host, **details})
    return tuple(values)


def mapping_text(entry: Mapping[str, JsonValue], key: str) -> str:
    value = entry[key]
    if not isinstance(value, str) or not value.strip() or any(char in value for char in "\r\n"):
        raise ValueError(key)
    return value.strip()


def gitlab_host(value: str) -> str:
    raw = value.strip()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("GitLab host is not an exact HTTPS authority")
    try:
        hostname, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise ValueError("GitLab host is not an exact HTTPS authority") from exc
    if hostname is None or "*" in hostname or not hostname.strip():
        raise ValueError("GitLab host is not an exact HTTPS authority")
    return f"{hostname.casefold()}:{port}" if port is not None else hostname.casefold()


def https_host(value: str) -> str | None:
    raw = value.strip()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    try:
        hostname, port = parsed.hostname, parsed.port
    except ValueError:
        return None
    if hostname is None or "*" in hostname or not hostname.strip():
        return None
    authority = hostname.casefold()
    if port is not None:
        authority = f"{authority}:{port}"
    return authority


def current_branch(git_resource: GitResourceLike) -> str:
    command = git_resource.passthrough(("symbolic-ref", "--quiet", "--short", "HEAD"))
    runner = getattr(command, "run", None)
    result = (
        cast("BranchResultLike", runner())
        if callable(runner)
        else cast("BranchResultLike", command)
    )
    if getattr(result, "returncode", 1) != 0:
        raise ContextVerificationError("current Git branch is unavailable")
    branch = getattr(result, "stdout", "")
    if not isinstance(branch, str) or not branch.strip():
        raise ContextVerificationError("current Git branch is unavailable")
    return branch.strip()


def remote_host_for_operation(args: Sequence[str], repository_url: str) -> str | None:
    remote_commands = frozenset(
        {"clone", "fetch", "pull", "push", "ls-remote", "submodule", "archive", "bundle"}
    )
    dangerous_options = (
        "-C",
        "-c",
        "--config",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "--upload-pack",
        "--receive-pack",
    )
    if any(
        argument == option
        or argument.startswith(f"{option}=")
        or (option == "-C" and argument.startswith("-C") and len(argument) > 2)
        for argument in args
        for option in dangerous_options
    ):
        raise ContextVerificationError("credentialed Git passthrough rejects command overrides")
    command = next((argument for argument in args if not argument.startswith("-")), None)
    if command not in remote_commands:
        return None
    host = https_host(repository_url)
    if host is None:
        return None
    for argument in args:
        candidate = https_host(argument) if "://" in argument else None
        if candidate is not None and candidate != host:
            raise ContextVerificationError("Git remote host is ambiguous")
    return host


def validated_project_path(override: str | None, inferred: str) -> str:
    if override is not None and override.strip() != inferred:
        raise ContextVerificationError(
            "GitLab project override does not match the verified repository"
        )
    return inferred


def askpass_command() -> str:
    return shlex.join((sys.executable, "-m", "odcli_multica.askpass"))
