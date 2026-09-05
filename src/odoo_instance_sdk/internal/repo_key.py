from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def git_common_dir(repository_root: Path) -> Path:
    """Resolve the shared Git directory from a repository's local marker."""
    root = repository_root.resolve()
    marker = root / ".git"
    if marker.is_file():
        try:
            value = marker.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value.startswith("gitdir:"):
            git_dir = Path(value.partition(":")[2].strip())
            if not git_dir.is_absolute():
                git_dir = root / git_dir
            resolved = git_dir.resolve()
            return resolved.parent.parent if resolved.parent.name == "worktrees" else resolved
    return marker.resolve()


def repo_key(repository_root: Path, git_common_dir: Path) -> str:
    name = repository_root.resolve().name or "repo"
    slug = _SAFE_SLUG_RE.sub("_", name).strip("._-") or "repo"
    digest = hashlib.sha256(str(git_common_dir.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{digest}"
