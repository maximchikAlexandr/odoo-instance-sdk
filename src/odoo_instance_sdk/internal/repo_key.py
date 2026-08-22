from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def repo_key(repository_root: Path, git_common_dir: Path) -> str:
    name = repository_root.resolve().name or "repo"
    slug = _SAFE_SLUG_RE.sub("_", name).strip("._-") or "repo"
    digest = hashlib.sha256(str(git_common_dir.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{digest}"
