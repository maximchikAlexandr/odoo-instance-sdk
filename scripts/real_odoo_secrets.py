"""Owner-only secret registry and evidence scanning helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from collections.abc import Iterable
from pathlib import Path


def secret_variants(secret: str) -> tuple[str, ...]:
    """Return raw, reversible, and digest forms of a runtime secret."""
    return (
        secret,
        hashlib.sha256(secret.encode()).hexdigest(),
        base64.b64encode(secret.encode()).decode(),
        urllib.parse.quote(secret, safe=""),
    )


def write_secret_registry(path: Path, secrets: Iterable[str]) -> None:
    """Persist generated secrets outside the upload root with owner-only mode."""
    values = {value for value in secrets if value}
    if path.exists():
        values.update(read_secret_registry(path))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps({"secrets": sorted(values)}) + "\n", encoding="utf-8")
    path.chmod(0o600)


def read_secret_registry(path: Path) -> tuple[str, ...]:
    """Load and validate an owner-only registry; malformed state fails closed."""
    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError(f"secret registry is not owner-only: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    secrets = value.get("secrets") if isinstance(value, dict) else None
    if not isinstance(secrets, list) or not all(isinstance(item, str) and item for item in secrets):
        raise ValueError("secret registry must contain a non-empty string list")
    return tuple(secrets)


def leaked_variants(content: bytes, secrets: Iterable[str]) -> tuple[str, ...]:
    text = content.decode("utf-8", errors="replace")
    return tuple(
        variant
        for secret in secrets
        for variant in secret_variants(secret)
        if variant and variant in text
    )


def assert_secret_free(content: bytes, secrets: Iterable[str]) -> None:
    if leaked_variants(content, secrets):
        raise ValueError("runtime secret detected in evidence")


__all__ = [
    "assert_secret_free",
    "leaked_variants",
    "read_secret_registry",
    "secret_variants",
    "write_secret_registry",
]
