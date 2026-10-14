"""Credential-free generic Git repository resolution and revalidation."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from odoo_instance_sdk.exceptions import GitRepositoryChangedError, GitRepositoryError
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, SubprocessExecutor
from odoo_instance_sdk.models import GitRepositorySnapshot

_PROJECT_PART = re.compile(r"^[^/\\?#]+$")


def _probe(root: Path, args: Sequence[str], step_id: str) -> ProcessResult:
    step = PreparedStep(
        step_id=step_id,
        argv=("git", "-C", str(root), *tuple(args)),
        cwd=str(root),
        environment=(
            ("GIT_CONFIG_GLOBAL", os.devnull),
            ("GIT_CONFIG_NOSYSTEM", "1"),
            ("GIT_CONFIG_SYSTEM", os.devnull),
        ),
        environment_policy="explicit",
        read_only=True,
        text=True,
        timeout=15.0,
    )
    try:
        result = SubprocessExecutor().execute(step)
    except Exception as exc:
        raise GitRepositoryError(f"Git repository evidence is unavailable: {step_id}") from exc
    if not isinstance(result, ProcessResult):
        raise GitRepositoryError(f"Git repository evidence is unavailable: {step_id}")
    return result


def _stdout(result: ProcessResult, label: str) -> str:
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise GitRepositoryError(f"Git repository {label} is unavailable")
    return result.stdout


def _normalize_origin(value: str) -> tuple[str, str, str]:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or not parsed.path.strip("/")
    ):
        raise GitRepositoryError("Git repository origin must be an absolute HTTPS GitLab URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise GitRepositoryError("Git repository origin is invalid") from exc
    host = parsed.hostname.casefold()
    authority = f"{host}:{port}" if port is not None else host
    project = parsed.path.strip("/")
    if project.endswith(".git"):
        project = project[:-4]
    parts = project.split("/")
    if not project or any(
        not _PROJECT_PART.fullmatch(part) or part in {".", ".."} for part in parts
    ):
        raise GitRepositoryError("Git repository project path is invalid")
    return f"https://{authority}/{project}", authority, project


def normalize_repository_url(value: str) -> tuple[str, str, str]:
    """Validate and normalize one credential-free GitLab HTTPS repository URL."""
    return _normalize_origin(value)


def repository_project_path(origin: str) -> tuple[str, str]:
    """Return normalized host/project identity from an HTTPS GitLab URL."""
    _canonical, host, project = _normalize_origin(origin)
    return host, project


def _staged_paths(root: Path) -> tuple[str, ...]:
    result = _probe(root, ("diff", "--cached", "--name-only", "-z"), "git.repository.staged")
    value = _stdout(result, "staged state")
    return tuple(path for path in value.split("\0") if path)


def _index_tree(root: Path) -> str:
    result = _probe(root, ("write-tree",), "git.repository.index-tree")
    return _stdout(result, "index tree").strip()


def _common_dir(root: Path) -> str:
    value = _stdout(
        _probe(root, ("rev-parse", "--git-common-dir"), "git.repository.common-dir"),
        "common directory",
    ).strip()
    path = Path(value)
    return str((root / path if not path.is_absolute() else path).resolve())


def resolve_repository(path: Path | str) -> GitRepositorySnapshot:
    """Capture canonical root, origin, branch, staged state, and Odoo class."""
    requested = Path(path).expanduser().resolve()
    if not requested.exists() or not requested.is_dir():
        raise GitRepositoryError("Git repository root is unavailable")
    root = Path(
        _stdout(
            _probe(requested, ("rev-parse", "--show-toplevel"), "git.repository.root"), "root"
        ).strip()
    ).resolve()
    origin_result = _probe(root, ("remote", "get-url", "--all", "origin"), "git.repository.origin")
    origins = tuple(
        line.strip() for line in _stdout(origin_result, "origin").splitlines() if line.strip()
    )
    if len(origins) != 1:
        raise GitRepositoryError("Git repository origin is absent or ambiguous")
    origin, host, project = _normalize_origin(origins[0])
    branch_result = _probe(
        root, ("symbolic-ref", "--quiet", "--short", "HEAD"), "git.repository.branch"
    )
    branch = (
        branch_result.stdout.strip()
        if branch_result.returncode == 0 and isinstance(branch_result.stdout, str)
        else None
    )
    status = _probe(root, ("status", "--porcelain=v1"), "git.repository.status")
    dirty = bool(_stdout(status, "worktree state").strip())
    staged = _staged_paths(root)
    return GitRepositorySnapshot(
        canonical_root=str(root),
        git_common_dir=_common_dir(root),
        origin=origin,
        host=host,
        project=project,
        branch=branch or None,
        staged_paths=staged,
        worktree_dirty=dirty,
        initialized_odoo=(root / ".odcli" / "project.toml").is_file(),
        index_tree=_index_tree(root),
    )


resolve_generic_repository = resolve_repository


def revalidate_repository(snapshot: GitRepositorySnapshot) -> GitRepositorySnapshot:
    current = resolve_repository(snapshot.canonical_root)
    if current != snapshot:
        raise GitRepositoryChangedError("repository evidence changed after planning")
    return current


def _configured_values(root: Path, key_pattern: str, step_id: str) -> tuple[str, ...]:
    result = _probe(root, ("config", "--local", "--get-regexp", key_pattern), step_id)
    if result.returncode not in (0, 1) or not isinstance(result.stdout, str):
        raise GitRepositoryError("repository Git configuration is unavailable")
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def submodule_urls(root: Path | str) -> tuple[tuple[str, str, str], ...]:
    """Return ``(path, normalized URL, host)`` for repository-owned submodules.

    This reads only committed ``.gitmodules`` data and never asks Git to resolve
    or fetch a credentialed URL. Relative URLs are resolved against the
    superproject origin so host policy is applied before a child is started.
    """
    checkout = Path(root).expanduser().resolve()
    modules = checkout / ".gitmodules"
    if modules.is_symlink():
        raise GitRepositoryError("submodule configuration is not repository-owned")
    if not modules.is_file():
        return ()
    origin, _host, _project = _normalize_origin(
        _stdout(
            _probe(checkout, ("remote", "get-url", "origin"), "git.submodule.origin"), "origin"
        ).strip()
    )
    url_result = _probe(
        checkout,
        (
            "config",
            "--file",
            str(modules),
            "--get-regexp",
            r"^submodule\..*\.url$",
        ),
        "git.submodule.urls",
    )
    if url_result.returncode not in (0, 1) or not isinstance(url_result.stdout, str):
        raise GitRepositoryError("repository submodule URL configuration is unavailable")
    values = tuple(line.strip() for line in url_result.stdout.splitlines() if line.strip())
    result: list[tuple[str, str, str]] = []
    for line in values:
        key, separator, raw_url = line.partition(" ")
        if not separator or not raw_url.strip():
            raise GitRepositoryError("submodule URL configuration is malformed")
        path = key.removeprefix("submodule.").removesuffix(".url")
        candidate = urljoin(origin.rsplit("/", 1)[0] + "/", raw_url.strip())
        normalized, host, _project = _normalize_origin(candidate)
        result.append((path, normalized, host))
    return tuple(result)


def validate_submodule_configuration(
    root: Path | str,
    *,
    allowed_hosts: Sequence[str],
) -> tuple[tuple[str, str, str], ...]:
    """Reject repository-controlled credential and command-routing overrides."""
    checkout = Path(root).expanduser().resolve()
    dangerous = _configured_values(
        checkout,
        r"^(credential\.helper|core\.hooksPath|submodule\..*\.update|protocol\..*|url\..*\.insteadOf)$",
        "git.submodule.policy",
    )
    if dangerous:
        raise GitRepositoryError("repository-controlled submodule transport is unsafe")
    values = submodule_urls(checkout)
    allowed = frozenset(allowed_hosts)
    if any(host not in allowed for _path, _url, host in values):
        raise GitRepositoryError("submodule host has no selected-member credential mapping")
    return values


def missing_submodules(root: Path | str) -> tuple[str, ...]:
    """Return only unpopulated submodule paths from Git's recursive status."""
    checkout = Path(root).expanduser().resolve()
    result = _probe(checkout, ("submodule", "status", "--recursive"), "git.submodule.status")
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise GitRepositoryError("submodule status is unavailable")
    missing: list[str] = []
    for line in result.stdout.splitlines():
        if not line.startswith("-"):
            continue
        fields = line[1:].strip().split(maxsplit=1)
        if len(fields) == 2:
            missing.append(fields[1].split(" ", 1)[0])
    return tuple(dict.fromkeys(missing))


__all__ = [
    "missing_submodules",
    "normalize_repository_url",
    "repository_project_path",
    "resolve_generic_repository",
    "resolve_repository",
    "revalidate_repository",
    "submodule_urls",
    "validate_submodule_configuration",
]
