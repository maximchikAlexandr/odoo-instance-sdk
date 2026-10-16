"""Dashboard asset materialization shared by the isolated build backend."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from build_process import run_build_process


@cache
def ensure_dashboard_assets(web_root: Path, dist: Path) -> None:
    """Always rebuild once per backend process, including over stale ignored dist."""
    assets = dist / "assets"
    run_build_process(("npm", "ci"), cwd=web_root)
    run_build_process(("npm", "run", "build"), cwd=web_root)
    if not (dist / "index.html").is_file() or not assets.is_dir() or not any(assets.iterdir()):
        raise RuntimeError("dashboard build did not produce distributable SPA assets")


__all__ = ["ensure_dashboard_assets"]
