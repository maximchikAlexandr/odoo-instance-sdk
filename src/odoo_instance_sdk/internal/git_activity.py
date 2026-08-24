from __future__ import annotations

import subprocess
import time
from pathlib import Path

from odoo_instance_sdk.models import GitActivity, GitActivityState, GitDiff

_DEFAULT_BRANCH = "main"
_GIT_TIMEOUT = 10.0

# ponytail: module-level in-memory cache, not persistent; cleared on process exit.
# Bounded by (worktree_resolved, head_sha, default_tip_sha) — at most one entry per
# distinct (worktree, HEAD, tip) tuple observed, evicted lazily on TTL miss.
_git_cache: dict[tuple[Path, str, str], tuple[float, GitActivity]] = {}
_CACHE_TTL = 15.0


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run ``git -C <cwd> <args>`` with timeout. Returns (rc, stdout, stderr).

    On timeout or OSError (git not in PATH) returns (-1, "", "").
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return -1, "", ""
    return proc.returncode, proc.stdout, proc.stderr


def _orphan_full() -> GitActivity:
    """Total git failure: not a repo / no HEAD. head_sha unknown."""
    return GitActivity(
        default_branch=_DEFAULT_BRANCH,
        head_sha=None,
        short_sha=None,
        branch="unknown",
        ahead=None,
        behind=None,
        diff=None,
        state=GitActivityState.ORPHAN,
    )


def _orphan_partial(head_sha: str, short_sha: str, branch: str) -> GitActivity:
    # ponytail: HEAD known but no usable default tip / merge-base — keep identity,
    # drop ahead/behind/diff. Distinguishes "in a repo, off the rails" from "not a repo".
    return GitActivity(
        default_branch=_DEFAULT_BRANCH,
        head_sha=head_sha,
        short_sha=short_sha,
        branch=branch,
        ahead=None,
        behind=None,
        diff=None,
        state=GitActivityState.ORPHAN,
    )


def _sum_numstat(numstat: str) -> GitDiff:
    added = 0
    deleted = 0
    for line in numstat.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d = parts[0], parts[1]
        # Binary files report "-" for added/deleted — skip (contribute 0).
        if a == "-" or d == "-":
            continue
        try:
            added += int(a)
            deleted += int(d)
        except ValueError:
            continue
    return GitDiff(added=added, deleted=deleted)


def _resolve_counts(
    worktree: Path, default_tip: str, merge_base: str
) -> tuple[int, int, GitDiff] | None:
    """Compute (ahead, behind, diff).

    ahead/behind use the default-tip (commits between tip and HEAD); merge-base is
    only used for the three-dot diff. ponytail: spec D6 wrote ``<merge-base>..HEAD``
    for both, but merge-base is an ancestor of HEAD so ``HEAD..<merge-base>`` is
    always empty — the counts must use the tip to ever detect BEHIND/DIVERGED.
    """
    rc, ahead_out, _ = _run_git(["rev-list", "--count", f"{default_tip}..HEAD"], worktree)
    if rc != 0 or not ahead_out.strip().isdigit():
        return None
    ahead = int(ahead_out.strip())

    rc, behind_out, _ = _run_git(["rev-list", "--count", f"HEAD..{default_tip}"], worktree)
    if rc != 0 or not behind_out.strip().isdigit():
        return None
    behind = int(behind_out.strip())

    rc, numstat_out, _ = _run_git(["diff", "--numstat", f"{merge_base}...HEAD"], worktree)
    if rc != 0:
        return None
    return ahead, behind, _sum_numstat(numstat_out)


def _compute_git_activity(worktree: Path) -> GitActivity:
    """Pure compute (no cache): three-dot git activity against the default branch tip.

    Resolves HEAD SHA, branch name, default-tip SHA (upstream then local main),
    merge-base, and ahead/behind/numstat. Any git failure degrades to an orphan
    shape (full or partial). The monitor owns instance-level caching; this is the
    non-caching core shared with ``collect_git_activity``.
    """
    head_sha, short_sha, branch, default_tip = _resolve_identity(worktree)
    if not head_sha:
        return _orphan_full()
    if default_tip is None:
        return _orphan_partial(head_sha, short_sha, branch)

    rc, mb_out, _ = _run_git(["merge-base", default_tip, "HEAD"], worktree)
    if rc != 0:
        return _orphan_partial(head_sha, short_sha, branch)

    counts = _resolve_counts(worktree, default_tip, mb_out.strip())
    if counts is None:
        return _orphan_partial(head_sha, short_sha, branch)
    ahead, behind, diff = counts

    if ahead == 0 and behind == 0:
        state = GitActivityState.CLEAN
    elif behind == 0:
        state = GitActivityState.AHEAD
    elif ahead == 0:
        state = GitActivityState.BEHIND
    else:
        state = GitActivityState.DIVERGED

    return GitActivity(
        default_branch=_DEFAULT_BRANCH,
        head_sha=head_sha,
        short_sha=short_sha,
        branch=branch,
        ahead=ahead,
        behind=behind,
        diff=diff,
        state=state,
    )


def _resolve_identity(worktree: Path) -> tuple[str, str, str, str | None]:
    """Cheap rev-parse pass: (head_sha, short_sha, branch, default_tip_sha).

    default_tip_sha is None when no upstream/local main tip resolves (orphan path).
    """
    rc, head_out, _ = _run_git(["rev-parse", "--verify", "HEAD"], worktree)
    if rc != 0:
        return "", "", "unknown", None
    head_sha = head_out.strip()
    short_sha = head_sha[:7]
    rc, branch_out, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], worktree)
    branch = branch_out.strip() if rc == 0 and branch_out.strip() else "unknown"
    rc, tip_out, _ = _run_git(["rev-parse", "--verify", "main@{upstream}"], worktree)
    if rc != 0:
        rc, tip_out, _ = _run_git(["rev-parse", "--verify", "refs/heads/main"], worktree)
    default_tip = tip_out.strip() if rc == 0 else None
    return head_sha, short_sha, branch, default_tip


def collect_git_activity(worktree: Path) -> GitActivity:
    """Collect three-dot git activity for a worktree against the default branch tip.

    Cache key: (worktree.resolve(), head_sha, default_tip_sha), TTL 15s.
    The expensive part (rev-list / diff) only runs on cache miss; rev-parse for the
    key is cheap and runs every call.
    """
    head_sha, short_sha, branch, default_tip = _resolve_identity(worktree)
    if not head_sha:
        return _orphan_full()
    if default_tip is None:
        return _orphan_partial(head_sha, short_sha, branch)

    key = (worktree.resolve(), head_sha, default_tip)
    cached = _git_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_TTL:
        return cached[1]

    rc, mb_out, _ = _run_git(["merge-base", default_tip, "HEAD"], worktree)
    if rc != 0:
        result = _orphan_partial(head_sha, short_sha, branch)
        _git_cache[key] = (time.monotonic(), result)
        return result

    counts = _resolve_counts(worktree, default_tip, mb_out.strip())
    if counts is None:
        result = _orphan_partial(head_sha, short_sha, branch)
        _git_cache[key] = (time.monotonic(), result)
        return result
    ahead, behind, diff = counts

    if ahead == 0 and behind == 0:
        state = GitActivityState.CLEAN
    elif behind == 0:
        state = GitActivityState.AHEAD
    elif ahead == 0:
        state = GitActivityState.BEHIND
    else:
        state = GitActivityState.DIVERGED

    result = GitActivity(
        default_branch=_DEFAULT_BRANCH,
        head_sha=head_sha,
        short_sha=short_sha,
        branch=branch,
        ahead=ahead,
        behind=behind,
        diff=diff,
        state=state,
    )
    _git_cache[key] = (time.monotonic(), result)
    return result
