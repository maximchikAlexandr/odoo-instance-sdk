"""Build-isolation-safe process policy shared by the PEP-517 backend."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass(frozen=True, slots=True)
class BuildProcessPolicy:
    """Explicit policy for one deterministic build-tool invocation."""

    timeout_seconds: float = 300.0


_PLATFORM_ENVIRONMENT = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
    }
)


@contextmanager
def _build_environment() -> Iterator[dict[str, str]]:
    """Yield a minimal platform environment with build-owned user paths."""
    with TemporaryDirectory(prefix="odoo-instance-sdk-build-") as isolated:
        isolated_root = Path(isolated)
        home = isolated_root / "home"
        tmp = isolated_root / "tmp"
        npm_cache = isolated_root / "npm-cache"
        home.mkdir()
        tmp.mkdir()
        npm_cache.mkdir()
        environment = {
            key: value for key, value in os.environ.items() if key in _PLATFORM_ENVIRONMENT
        }
        environment.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "TMPDIR": str(tmp),
                "TMP": str(tmp),
                "TEMP": str(tmp),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_STATE_HOME": str(home / ".local" / "state"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "NPM_CONFIG_CACHE": str(npm_cache),
                "npm_config_cache": str(npm_cache),
            }
        )
        try:
            yield environment
        finally:
            shutil.rmtree(isolated_root, ignore_errors=True)


def run_build_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    policy: BuildProcessPolicy = BuildProcessPolicy(),
) -> None:
    """Run one non-interactive build command under the shared build policy."""
    if not argv:
        raise ValueError("build command cannot be empty")
    with _build_environment() as environment:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=policy.timeout_seconds,
            check=False,
            shell=False,
        )
    if result.returncode != 0:
        raise RuntimeError(f"build command failed: {argv[0]}")


__all__ = ["BuildProcessPolicy", "run_build_process"]
