"""Trusted dependency-lock validation and synchronization argv."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from odoo_instance_sdk.exceptions import ConfigError

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def resolve_hash_lock(
    hash_lock: str | Path | None,
    hash_lock_sha256: str | None,
    *,
    base_dir: Path | None = None,
) -> Path | None:
    """Resolve and immutably validate a caller-supplied requirements lock.

    ``None`` for both arguments deliberately keeps the legacy discovery path.
    Any partial or malformed pair fails before a checkout/sync can mutate an
    environment.
    """

    if hash_lock is None and hash_lock_sha256 is None:
        return None
    if hash_lock is None or hash_lock_sha256 is None:
        raise ConfigError("hash_lock and hash_lock_sha256 must be supplied together")
    if _SHA256.fullmatch(hash_lock_sha256) is None:
        raise ConfigError("hash_lock_sha256 must be a lowercase SHA-256 digest")

    candidate = Path(hash_lock).expanduser()
    if not candidate.is_absolute() and base_dir is not None:
        candidate = base_dir / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ConfigError(f"hash lock is unavailable: {candidate}") from exc
    if not resolved.is_file():
        raise ConfigError(f"hash lock is not a regular file: {resolved}")
    try:
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        raise ConfigError(f"hash lock is unreadable: {resolved}") from exc
    if actual != hash_lock_sha256:
        raise ConfigError(
            f"hash lock digest mismatch for {resolved}: expected {hash_lock_sha256}, got {actual}"
        )
    return resolved


def build_trusted_sync_argv(python: str | Path, hash_lock: str | Path) -> tuple[str, ...]:
    """Build the only supported argv for an owned hash-locked environment."""

    return (
        "uv",
        "pip",
        "sync",
        "--python",
        str(python),
        "--require-hashes",
        str(hash_lock),
    )


def revalidate_hash_lock(hash_lock: Path, hash_lock_sha256: str | None) -> None:
    """Reject a lock changed after command capture and before execution."""

    resolved = resolve_hash_lock(hash_lock, hash_lock_sha256)
    if resolved != hash_lock:
        raise ConfigError(f"hash lock path changed after capture: {hash_lock}")
