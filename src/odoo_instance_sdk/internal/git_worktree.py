from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from odoo_instance_sdk.exceptions import EnvironmentConflictError


class GitError(Exception):
    pass


@dataclass(slots=True, kw_only=True)
class WorktreeInfo:
    worktree: str
    head: str
    branch: str | None
    locked: bool
    prunable: bool


def _run(
    args: list[str], *, cwd: str | Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        shell=False,
        capture_output=True,
        text=True,
        check=check,
    )


def rev_parse_toplevel(path: Path) -> Path:
    proc = _run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], check=False)
    if proc.returncode != 0:
        raise GitError(f"not a git repository: {path}")
    return Path(proc.stdout.strip()).resolve()


def rev_parse_git_common_dir(path: Path) -> Path:
    proc = _run(["git", "-C", str(path), "rev-parse", "--git-common-dir"], check=False)
    if proc.returncode != 0:
        raise GitError(f"not a git repository: {path}")
    out = proc.stdout.strip()
    if not out:
        raise GitError(f"git rev-parse --git-common-dir returned empty for {path}")
    return Path(out).resolve()


def rev_parse_verify(repo_root: Path, ref: str) -> str:
    proc = _run(["git", "-C", str(repo_root), "rev-parse", "--verify", ref], check=False)
    if proc.returncode != 0:
        raise GitError(f"ref {ref!r} not found in {repo_root}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def local_branch_exists(repo_root: Path, branch: str) -> bool:
    proc = _run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", f"refs/heads/{branch}"],
        check=False,
    )
    return proc.returncode == 0


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
    repo_root: Path, worktree: Path, branch: str, *, base_ref: str | None = None
) -> None:
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if local_branch_exists(repo_root, branch):
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
    current: dict[str, object] = {}
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


def worktree_is_dirty(worktree: Path) -> bool:
    proc = _run(["git", "-C", str(worktree), "status", "--porcelain"], check=False)
    if proc.returncode != 0:
        return True
    return bool(proc.stdout.strip())
