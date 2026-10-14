"""Focused lineage, credential-mapping, and Git boundary policies."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from odcli_multica.models import (
    ContextVerificationError,
    GitLabCredentialMapping,
    RootCreatorContext,
    VerifiedTaskContext,
)
from odoo_instance_sdk.internal.gitlab_credentials import (
    read_gitlab_credential_mappings as _read_core_gitlab_credential_mappings,
)
from odoo_instance_sdk.internal.proc import ProcessResult, SubprocessExecutor, prepared_step

if TYPE_CHECKING:
    from multica_py import Issue, Project


class IssueStore(Protocol):
    def get(self, issue_id: str) -> Issue: ...


class ProjectStore(Protocol):
    def get(self, project_id: str) -> Project: ...


@dataclass(frozen=True, slots=True)
class _GitProbeResult:
    returncode: int
    stdout: str


def _validated_process_stdout(result: ProcessResult) -> str:
    stdout = result.stdout
    if not isinstance(stdout, str):
        raise ContextVerificationError("Git transport evidence is unavailable")
    return stdout


def _is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def selected_issue_url(server_url: str | None, explicit: str | None, issue_id: str) -> str:
    """Use the verified server origin, or an explicit public link for a local daemon."""
    if isinstance(server_url, str) and server_url.strip():
        base = server_url.strip().rstrip("/")
        parsed_base = urlsplit(base)
        try:
            valid_port = parsed_base.port is None or parsed_base.port > 0
        except ValueError:
            valid_port = False
        if (
            not parsed_base.netloc
            or parsed_base.username is not None
            or parsed_base.password is not None
            or parsed_base.query
            or parsed_base.fragment
            or parsed_base.hostname is None
            or not valid_port
        ):
            raise ContextVerificationError("Multica issue URL is unavailable")
        if parsed_base.scheme == "https":
            canonical = f"{base}/issues/{issue_id}"
            if explicit is not None and explicit.strip() != canonical:
                raise ContextVerificationError("Multica issue URL is not the verified issue")
            return canonical
        if parsed_base.scheme != "http" or not _is_loopback_host(parsed_base.hostname):
            raise ContextVerificationError("Multica issue URL is unavailable")

    if explicit is None:
        raise ContextVerificationError("Multica issue URL is unavailable")
    selected = explicit.strip()
    parsed = urlsplit(selected)
    try:
        valid_port = parsed.port is None or parsed.port > 0
    except ValueError:
        valid_port = False
    segments = parsed.path.split("/")
    if (
        len(selected) > 2048
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or _is_loopback_host(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not valid_port
        or len(segments) < 3
        or segments[-2:] != ["issues", issue_id]
        or any(not re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in segments[1:])
    ):
        raise ContextVerificationError("Multica issue URL is not the verified issue")
    return selected


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
    projects: ProjectStore,
) -> RootCreatorContext | None:
    if issue.creator_type is None and issue.creator_id is None:
        return None
    return resolve_root_creator_from_issue(
        issue,
        issue_id=issue_id,
        workspace_id=workspace_id,
        project_id=project_id,
        issues=issues,
        projects=projects,
    )


def resolve_root_creator(
    context: VerifiedTaskContext, issues: IssueStore, projects: ProjectStore
) -> RootCreatorContext:
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
        projects=projects,
    )


def resolve_root_creator_from_issue(
    first_issue: Issue,
    *,
    issue_id: str,
    workspace_id: str,
    project_id: str,
    issues: IssueStore,
    projects: ProjectStore,
) -> RootCreatorContext:
    visited: set[str] = set()
    current_id = issue_id
    issue = first_issue
    while True:
        if current_id in visited:
            raise ContextVerificationError("issue lineage is cyclic")
        visited.add(current_id)
        verify_lineage_issue(
            issue, workspace_id=workspace_id, project_id=project_id, projects=projects
        )
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


def verify_lineage_issue(
    issue: Issue, *, workspace_id: str, project_id: str, projects: ProjectStore
) -> None:
    if issue.project_id is None:
        raise ContextVerificationError("issue lineage project identity is unavailable")
    if issue.project_id != project_id:
        try:
            ancestor_project = projects.get(issue.project_id)
        except Exception as exc:
            raise ContextVerificationError("issue lineage project evidence is unavailable") from exc
        if ancestor_project.workspace_id != workspace_id:
            raise ContextVerificationError("issue lineage crosses the verified workspace")
    issue_workspace = getattr(issue, "workspace_id", None)
    if issue_workspace is not None and issue_workspace != workspace_id:
        raise ContextVerificationError("issue lineage crosses the verified workspace")


def default_mapping_path(project_root: Path) -> Path:
    return project_root.expanduser().resolve() / ".odcli" / "gitlab-credentials.toml"


def read_gitlab_credential_mappings(path: Path | str) -> tuple[GitLabCredentialMapping, ...]:
    try:
        return _read_core_gitlab_credential_mappings(
            path,
            require_owner_only_directory=False,
            validate_member_ids=False,
        )
    except Exception as exc:
        if isinstance(exc, ContextVerificationError):
            raise
        raise ContextVerificationError(str(exc)) from exc


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


def current_branch(checkout: Path) -> str:
    step = prepared_step(
        "git",
        ("-C", str(checkout), "symbolic-ref", "--quiet", "--short", "HEAD"),
        step_id="odcli.current-branch",
        timeout=15.0,
        read_only=True,
    )
    try:
        result = SubprocessExecutor().execute(step)
    except (OSError, RuntimeError) as exc:
        raise ContextVerificationError("current Git branch is unavailable") from exc
    if not isinstance(result, ProcessResult) or result.returncode != 0:
        raise ContextVerificationError("current Git branch is unavailable")
    branch = _validated_process_stdout(result).strip()
    if not branch:
        raise ContextVerificationError("current Git branch is unavailable")
    return branch


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


def _git_probe(checkout: Path, args: Sequence[str], step_id: str) -> _GitProbeResult:
    """Run a bounded, credential-free Git inspection with ambient config disabled."""
    step = prepared_step(
        "git",
        ("-C", str(checkout), *tuple(args)),
        step_id=step_id,
        cwd=checkout,
        env={
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
        environment_policy="explicit",
        timeout=15.0,
        read_only=True,
    )
    try:
        result = SubprocessExecutor().execute(step)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise ContextVerificationError("Git transport evidence is unavailable") from exc
    if not isinstance(result, ProcessResult):
        raise ContextVerificationError("Git transport evidence is unavailable")
    return _GitProbeResult(
        returncode=result.returncode,
        stdout=_validated_process_stdout(result),
    )


def _git_url_parts(value: str) -> tuple[str, str, str] | None:
    """Return scheme, authority, and normalized path for a transport URL."""
    raw = value.strip()
    if raw.startswith("git@") and ":" in raw:
        authority, path = raw[4:].split(":", 1)
        return "ssh", authority.casefold(), path.removesuffix(".git").strip("/")
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() not in {"https", "ssh"} or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    authority = parsed.hostname.casefold()
    if port is not None:
        authority = f"{authority}:{port}"
    path = parsed.path.removesuffix(".git").strip("/")
    return parsed.scheme.casefold(), authority, path


def _same_repository_url(left: str, right: str) -> bool:
    left_parts = _git_url_parts(left)
    right_parts = _git_url_parts(right)
    return left_parts is not None and left_parts == right_parts


def _submodule_authority(value: str, expected_host: str) -> bool:
    raw = value.strip()
    if not raw or raw.startswith((".", "/")) or ("://" not in raw and not raw.startswith("git@")):
        return True
    parts = _git_url_parts(raw)
    return parts is not None and parts[1] == expected_host


def _verify_git_helpers(root: Path) -> None:
    helper = _git_probe(
        root, ("config", "--local", "--get-all", "credential.helper"), "odcli.git.local-helper"
    )
    if helper.returncode == 0 and helper.stdout.strip():
        raise ContextVerificationError("credential helper is configured in the repository")
    if helper.returncode not in (0, 1):
        raise ContextVerificationError("Git credential helper evidence is unavailable")


def _verify_submodule_authorities(root: Path, expected_host: str) -> None:
    configured_submodules = _git_probe(
        root,
        ("config", "--local", "--get-regexp", r"^submodule\..*\.url$"),
        "odcli.git.local-submodule-endpoints",
    )
    if configured_submodules.returncode not in (0, 1):
        raise ContextVerificationError("submodule transport evidence is unavailable")
    for line in configured_submodules.stdout.splitlines():
        _, _, url = line.partition(" ")
        if not _submodule_authority(url, expected_host):
            raise ContextVerificationError("foreign submodule transport authority")

    modules = root / ".gitmodules"
    if modules.is_symlink():
        raise ContextVerificationError("submodule configuration is not repository-owned")
    if modules.exists():
        submodules = _git_probe(
            root,
            ("config", "--file", str(modules), "--get-regexp", r"^submodule\..*\.url$"),
            "odcli.git.submodule-endpoints",
        )
        if submodules.returncode not in (0, 1):
            raise ContextVerificationError("submodule transport evidence is unavailable")
        for line in submodules.stdout.splitlines():
            _, _, url = line.partition(" ")
            if not _submodule_authority(url, expected_host):
                raise ContextVerificationError("foreign submodule transport authority")


def _verify_submodule_update_commands(root: Path) -> None:
    """Reject repository-controlled submodule commands before issuing credentials."""
    sources: tuple[tuple[Path | None, str], ...] = ((None, "odcli.git.local-submodule-updates"),)
    modules = root / ".gitmodules"
    if modules.is_symlink():
        raise ContextVerificationError("submodule configuration is not repository-owned")
    if modules.exists():
        sources += ((modules, "odcli.git.submodule-updates"),)
    for config_file, step_id in sources:
        args: tuple[str, ...] = (
            "config",
            "--local",
            "--get-regexp",
            r"^submodule\..*\.update$",
        )
        if config_file is not None:
            args = (
                "config",
                "--file",
                str(config_file),
                "--get-regexp",
                r"^submodule\..*\.update$",
            )
        updates = _git_probe(root, args, step_id)
        if updates.returncode not in (0, 1):
            raise ContextVerificationError("submodule update evidence is unavailable")
        for line in updates.stdout.splitlines():
            _, _, value = line.partition(" ")
            if value.lstrip().startswith("!"):
                raise ContextVerificationError("custom submodule update command is configured")


def _verify_git_remotes(root: Path, args: Sequence[str], repository_url: str) -> None:
    direct_urls = tuple(argument for argument in args if _git_url_parts(argument) is not None)
    if direct_urls:
        if any(not _same_repository_url(url, repository_url) for url in direct_urls):
            raise ContextVerificationError("Git transport endpoint is changed or ambiguous")
        return

    remotes = _git_probe(root, ("remote",), "odcli.git.remotes")
    if remotes.returncode != 0:
        raise ContextVerificationError("Git remote transport evidence is unavailable")
    remote_names = tuple(value.strip() for value in remotes.stdout.splitlines() if value.strip())
    if not remote_names:
        raise ContextVerificationError("Git origin transport evidence is unavailable")
    command = next((argument for argument in args if not argument.startswith("-")), "")
    target = next(
        (
            argument
            for argument in args[1:]
            if not argument.startswith("-") and _git_url_parts(argument) is None
        ),
        None,
    )
    names = (target,) if target in remote_names else remote_names
    for name in names:
        origin = _git_probe(
            root, ("remote", "get-url", "--all", name), f"odcli.git.{command}.remote"
        )
        push_origin = _git_probe(
            root,
            ("remote", "get-url", "--push", "--all", name),
            f"odcli.git.{command}.push-remote",
        )
        if origin.returncode != 0 or push_origin.returncode != 0:
            raise ContextVerificationError("Git transport endpoint evidence is unavailable")
        endpoints = tuple(
            value.strip()
            for value in (*origin.stdout.splitlines(), *push_origin.stdout.splitlines())
            if value.strip()
        )
        if not endpoints or any(
            not _same_repository_url(value, repository_url) for value in endpoints
        ):
            raise ContextVerificationError("Git origin transport endpoint is changed or ambiguous")


def verify_git_transport(checkout: Path | str, args: Sequence[str], repository_url: str) -> None:
    """Prove the local Git transport/config boundary before issuing credentials."""
    expected_host = https_host(repository_url)
    if expected_host is None:
        return
    direct_urls = tuple(argument for argument in args if _git_url_parts(argument) is not None)
    if direct_urls and not Path(checkout).exists():
        if any(not _same_repository_url(url, repository_url) for url in direct_urls):
            raise ContextVerificationError("Git transport endpoint is changed or ambiguous")
        return
    root = canonical_checkout(Path(checkout))
    _verify_git_helpers(root)
    _verify_submodule_authorities(root, expected_host)
    _verify_submodule_update_commands(root)
    _verify_git_remotes(root, args, repository_url)


def validated_project_path(override: str | None, inferred: str) -> str:
    if override is not None and override.strip() != inferred:
        raise ContextVerificationError(
            "GitLab project override does not match the verified repository"
        )
    return inferred


def askpass_command() -> str:
    helper = Path(sys.executable).with_name(
        "odcli-multica-askpass.exe" if os.name == "nt" else "odcli-multica-askpass"
    )
    if not helper.is_file():
        raise ContextVerificationError("GitLab askpass helper is unavailable")
    return str(helper)
