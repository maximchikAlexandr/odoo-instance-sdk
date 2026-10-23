from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from odoo_instance_sdk.exceptions import EnvironmentConflictError

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue


class GitError(Exception):
    pass


class GitRepositoryNotFoundError(GitError):
    """Git proved that a path is outside an enclosing repository."""


class GitRepositoryIdentityError(GitError):
    """Git remote syntax cannot be converted to a safe repository identity."""


@dataclass(slots=True, kw_only=True)
class WorktreeInfo:
    worktree: str
    head: str
    branch: str | None
    locked: bool
    prunable: bool


def _run(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    check: bool = True,
    step_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    from odoo_instance_sdk.internal.proc import (
        ProcessExecutionError,
        ProcessResult,
        active_context,
        run_captured,
    )

    try:
        context = active_context()
        if context is not None and step_id is not None:
            from odoo_instance_sdk.exceptions import UnplannedStepError

            captured = context.prepared(step_id)
            if captured.argv != tuple(args) or captured.cwd != (
                str(cwd) if cwd is not None else None
            ):
                raise UnplannedStepError(step_id, reason="Git inputs changed after planning")
            result = cast("ProcessResult", context.process(step_id))
        else:
            result = run_captured(args, cwd=cwd, timeout=30.0, text=True)
    except ProcessExecutionError as error:
        if check:
            raise subprocess.CalledProcessError(1, args, stderr=str(error)) from error
        return subprocess.CompletedProcess(args, -1, "", str(error))
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            args,
            output=result.stdout if isinstance(result.stdout, str) else "",
            stderr=result.stderr if isinstance(result.stderr, str) else "",
        )
    return subprocess.CompletedProcess(
        args,
        result.returncode,
        result.stdout if isinstance(result.stdout, str) else "",
        result.stderr if isinstance(result.stderr, str) else "",
    )


def rev_parse_toplevel(path: Path, *, step_id: str | None = None) -> Path:
    proc = _run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"], check=False, step_id=step_id
    )
    if proc.returncode != 0:
        if proc.returncode == 128 and "not a git repository" in proc.stderr.lower():
            raise GitRepositoryNotFoundError(f"not a git repository: {path}")
        raise GitError(f"not a git repository: {path}")
    return Path(proc.stdout.strip()).resolve()


def rev_parse_git_common_dir(path: Path) -> Path:
    proc = _run(["git", "-C", str(path), "rev-parse", "--git-common-dir"], check=False)
    if proc.returncode != 0:
        raise GitError(f"not a git repository: {path}")
    out = proc.stdout.strip()
    if not out:
        raise GitError(f"git rev-parse --git-common-dir returned empty for {path}")
    common_dir = Path(out)
    if not common_dir.is_absolute():
        common_dir = path.resolve() / common_dir
    return common_dir.resolve()


def rev_parse_verify(repo_root: Path, ref: str) -> str:
    proc = _run(["git", "-C", str(repo_root), "rev-parse", "--verify", ref], check=False)
    if proc.returncode != 0:
        raise GitError(f"ref {ref!r} not found in {repo_root}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def rev_parse_branch(repo_root: Path) -> str:
    """Return the current local branch, rejecting detached HEADs."""
    proc = _run(
        ["git", "-C", str(repo_root), "symbolic-ref", "--quiet", "--short", "HEAD"],
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise EnvironmentConflictError(
            "detached_checkout", f"checkout is detached; a local branch is required: {repo_root}"
        )
    return proc.stdout.strip()


def remote_url(repo_root: Path, name: str = "origin") -> str | None:
    """Return a configured remote URL without treating it as a credential."""
    proc = _run(
        ["git", "-C", str(repo_root), "remote", "get-url", name],
        check=False,
    )
    value = proc.stdout.strip()
    return value or None


def canonical_repository_identity(source: str) -> str:  # noqa: C901 -- strict transport allowlist
    """Return one transport-aware, secret-free identity for a Git remote."""
    if not source or any(char.isspace() or ord(char) < 32 for char in source):
        raise GitRepositoryIdentityError("repository origin contains unsafe characters")
    if "?" in source or "#" in source or source.startswith(("!", "ext::", "git+")):
        raise GitRepositoryIdentityError("repository origin contains unsafe transport syntax")

    scp_match = re.fullmatch(r"(?P<user>[^@/:]+)@(?P<host>[^:/]+):(?P<path>[^?#]+)", source)
    if scp_match is not None:
        if scp_match.group("user") != "git":
            raise GitRepositoryIdentityError("repository origin contains scp credentials")
        return f"ssh://git@{scp_match.group('host').lower()}/{scp_match.group('path')}"

    try:
        parsed = urlsplit(source)
    except ValueError:
        raise GitRepositoryIdentityError("repository origin has an invalid authority") from None
    if parsed.scheme:
        scheme = parsed.scheme.lower()
        if scheme not in {"https", "ssh"} or not parsed.path:
            raise GitRepositoryIdentityError("repository origin uses an unsupported transport")
        if (
            parsed.username is not None
            and (scheme != "ssh" or parsed.username != "git" or parsed.password is not None)
        ) or (parsed.password is not None and parsed.username is None):
            raise GitRepositoryIdentityError("repository origin contains credentials")
        if parsed.query or parsed.fragment:
            raise GitRepositoryIdentityError("repository origin contains query or fragment data")
        try:
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            raise GitRepositoryIdentityError("repository origin has an invalid authority") from None
        if hostname is None:
            raise GitRepositoryIdentityError("repository origin has no host")
        host = f"[{hostname.lower()}]" if ":" in hostname else hostname.lower()
        authority = host if port is None else f"{host}:{port}"
        user = "git@" if scheme == "ssh" and parsed.username == "git" else ""
        return f"{scheme}://{user}{authority}{parsed.path}"

    path = Path(source)
    if path.is_absolute():
        return str(path.resolve())
    raise GitRepositoryIdentityError("repository origin is not a canonical supported identity")


def _remote_ref_sha(repo_root: Path, remote: str, ref: str) -> str | None:
    """Read one exact ref from a configured remote, failing closed on ambiguity."""
    proc = _run(
        ["git", "-C", str(repo_root), "ls-remote", "--exit-code", remote, ref],
        check=False,
    )
    if proc.returncode != 0:
        return None
    lines = proc.stdout.splitlines()
    if len(lines) != 1:
        return None
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != ref:
        return None
    return fields[0]


def is_tracked_path(path: Path, *, step_prefix: str | None = None) -> bool:
    """Return whether an enclosing Git index owns this exact path."""
    candidate = Path(path).absolute()
    probe = candidate.parent
    while not os.path.lexists(str(probe)) and probe != probe.parent:
        probe = probe.parent
    try:
        repo_root = rev_parse_toplevel(
            probe, step_id=f"{step_prefix}.toplevel" if step_prefix is not None else None
        )
    except GitRepositoryNotFoundError:
        if _has_git_marker(candidate.parent):
            raise
        return False
    if step_prefix is not None:
        from odoo_instance_sdk.internal.proc import active_context

        context = active_context()
        if context is not None:
            tracked = context.prepared(f"{step_prefix}.tracked")
            if repo_root != Path(tracked.argv[2]):
                raise GitError("Git root changed while checking runtime config tracking")
    try:
        relative = candidate.relative_to(repo_root)
    except ValueError:
        return False
    proc = _run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "--cached",
            "--error-unmatch",
            "--",
            relative.as_posix(),
        ],
        check=False,
        step_id=f"{step_prefix}.tracked" if step_prefix is not None else None,
    )
    if proc.returncode == 1:
        return False
    if proc.returncode != 0:
        raise GitError("git ls-files could not verify the project runtime config")
    return relative.as_posix() in proc.stdout.splitlines()


def _has_git_marker(path: Path) -> bool:
    """Detect a repository marker without following it or masking errors."""
    try:
        return any(os.path.lexists(str(parent / ".git")) for parent in (path, *path.parents))
    except OSError:
        return True


def local_branch_exists(repo_root: Path, branch: str) -> bool:
    proc = _run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", f"refs/heads/{branch}"],
        check=False,
    )
    return proc.returncode == 0


def local_branch_names(repo_root: Path) -> tuple[str, ...]:
    """Return every local branch from the current repository refs."""
    proc = _run(
        [
            "git",
            "-C",
            str(repo_root),
            "for-each-ref",
            "--format=%(refname:strip=2)",
            "refs/heads",
        ],
        check=False,
    )
    if proc.returncode != 0:
        raise GitError(f"git could not enumerate local branches: {proc.stderr.strip()}")
    return tuple(sorted({line.strip() for line in proc.stdout.splitlines() if line.strip()}))


def remote_branch_names(repo_root: Path, ticket: str) -> tuple[str, ...]:
    """Return exact ticket heads from origin without fetching or changing refs."""
    proc = _run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-remote",
            "--heads",
            "origin",
            ticket,
            f"{ticket}_*",
        ],
        check=False,
    )
    if proc.returncode != 0:
        raise GitError(f"git could not enumerate origin branches: {proc.stderr.strip()}")
    branches: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            branch = parts[1][len("refs/heads/") :].strip()
            if branch == ticket or branch.startswith(f"{ticket}_"):
                branches.add(branch)
    return tuple(sorted(branches))


def remote_branches(repo_root: Path, branch: str) -> list[str]:
    proc = _run(
        ["git", "-C", str(repo_root), "ls-remote", "--heads", "origin", branch],
        check=False,
    )
    if proc.returncode != 0:
        return []
    refs: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            refs.append(parts[1])
    return refs


def worktree_add(
    repo_root: Path,
    worktree: Path,
    branch: str,
    *,
    base_ref: str | None = None,
    prepared_argv: Sequence[str] | None = None,
) -> None:
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if prepared_argv is not None:
        proc = _run(list(prepared_argv), check=False)
    elif local_branch_exists(repo_root, branch):
        proc = _run(
            ["git", "-C", str(repo_root), "worktree", "add", str(worktree), branch], check=False
        )
    elif remote_branches(repo_root, branch):
        proc = _run(
            [
                "git",
                "-C",
                str(repo_root),
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree),
                f"refs/remotes/origin/{branch}",
            ],
            check=False,
        )
        if proc.returncode != 0:
            proc = _run(
                ["git", "-C", str(repo_root), "worktree", "add", str(worktree), f"origin/{branch}"],
                check=False,
            )
    else:
        ref = base_ref or "HEAD"
        rev_parse_verify(repo_root, ref)
        proc = _run(
            ["git", "-C", str(repo_root), "worktree", "add", "-b", branch, str(worktree), ref],
            check=False,
        )
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if "is already checked out at" in stderr or "already used by worktree" in stderr:
            raise EnvironmentConflictError(
                "branch_checked_out_elsewhere",
                f"Branch {branch!r} is already checked out in another worktree",
                details={"branch": branch, "stderr": stderr},
            )
        raise GitError(f"git worktree add failed: {stderr}")


def worktree_list_porcelain(repo_root: Path) -> list[WorktreeInfo]:
    proc = _run(
        ["git", "-C", str(repo_root), "worktree", "list", "--porcelain", "-z"],
        check=False,
    )
    if proc.returncode != 0:
        return []
    result: list[WorktreeInfo] = []
    current: dict[str, JsonValue] = {}
    for raw in proc.stdout.split("\x00"):
        if not raw:
            if current:
                result.append(
                    WorktreeInfo(
                        worktree=str(current.get("worktree", "")),
                        head=str(current.get("HEAD", "")),
                        branch=cast("str | None", current.get("branch")),
                        locked="locked" in current,
                        prunable="prunable" in current,
                    )
                )
                current = {}
            continue
        if raw.startswith("worktree "):
            current["worktree"] = raw[len("worktree ") :]
        elif raw.startswith("HEAD "):
            current["HEAD"] = raw[len("HEAD ") :]
        elif raw.startswith("branch "):
            current["branch"] = raw[len("branch ") :]
        elif raw == "locked":
            current["locked"] = True
        elif raw == "prunable":
            current["prunable"] = True
    return result


def worktree_remove(repo_root: Path, worktree: Path) -> None:
    proc = _run(["git", "-C", str(repo_root), "worktree", "remove", str(worktree)], check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if "not a working tree" in stderr or "does not exist" in stderr:
            return
        raise GitError(f"git worktree remove failed: {stderr}")


def project_git_status_argv(worktree: Path) -> tuple[str, ...]:
    """Build the repository-independent status probe used by project removal."""
    return (
        "git",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-C",
        str(worktree),
        "status",
        "--porcelain",
        "--untracked-files=all",
    )


def _git_status_argv(worktree: Path, *, hardened: bool) -> list[str]:
    if hardened:
        return list(project_git_status_argv(worktree))
    return ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all"]


def worktree_is_dirty(
    worktree: Path,
    *,
    step_id: str | None = None,
    hardened: bool = False,
) -> bool:
    proc = _run(
        _git_status_argv(worktree, hardened=hardened),
        check=False,
        step_id=step_id,
    )
    if proc.returncode != 0:
        return True
    return bool(proc.stdout.strip())
