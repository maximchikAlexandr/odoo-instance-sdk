from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def parse_git_common_dir(repository_root: Path) -> Path | None:
    """Return a validated common Git directory without invoking Git."""
    root = repository_root.resolve()
    marker = root / ".git"
    try:
        if marker.is_dir():
            return marker.resolve()
        if marker.is_file():
            value = marker.read_text(encoding="utf-8").strip()
            if not value.startswith("gitdir:"):
                return None
            raw_target = value.partition(":")[2].strip()
            if not raw_target or "\n" in raw_target or "\r" in raw_target:
                return None
            target = Path(raw_target)
            if not target.is_absolute():
                target = root / target
            target = target.resolve()
            if not target.is_dir():
                return None
            return target.parent.parent if target.parent.name == "worktrees" else target
    except (OSError, UnicodeError, RuntimeError):
        return None
    return None


def git_common_dir(repository_root: Path) -> Path:
    """Resolve the shared Git directory from a repository's local marker.

    The historical fallback for missing or malformed markers is intentional:
    callers use this helper as a stable project identity key.  New safety-
    sensitive callers should use :func:`parse_git_common_dir` instead.
    """
    root = repository_root.resolve()
    common = parse_git_common_dir(root)
    if common is not None:
        return common
    marker = root / ".git"
    try:
        return marker.resolve()
    except OSError:
        return Path(os.path.abspath(marker))


def repo_key(repository_root: Path, git_common_dir: Path) -> str:
    name = repository_root.resolve().name or "repo"
    slug = _SAFE_SLUG_RE.sub("_", name).strip("._-") or "repo"
    digest = hashlib.sha256(str(git_common_dir.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{digest}"
