from __future__ import annotations

import subprocess
from pathlib import Path


def git_common_dir(repo_path: str | Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=str(repo_path),
        shell=False,
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout.strip()
    if not out:
        raise RuntimeError(f"git rev-parse --git-common-dir returned empty for {repo_path}")
    return Path(out).resolve()
