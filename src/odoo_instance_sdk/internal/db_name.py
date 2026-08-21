from __future__ import annotations

import re
from pathlib import Path

from odoo_instance_sdk.exceptions import ConfigError

_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_MAX_DB_NAME_BYTES = 63


def validate_db_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ConfigError(f"Invalid database name: {name!r} (empty or non-string)")
    if name in (".", ".."):
        raise ConfigError(f"Invalid database name: {name!r} (path component)")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ConfigError(f"Invalid database name: {name!r} (contains slash/backslash/NUL)")
    encoded = name.encode("utf-8")
    if len(encoded) > _MAX_DB_NAME_BYTES:
        raise ConfigError(
            f"Invalid database name: {name!r} (UTF-8 length {len(encoded)} > {_MAX_DB_NAME_BYTES})"
        )
    if not _DB_NAME_RE.match(name):
        raise ConfigError(f"Invalid database name: {name!r} (regex mismatch)")
    if name.startswith("."):
        raise ConfigError(f"Invalid database name: {name!r} (leading dot)")


def validate_filestore_containment(data_dir: Path, db_name: str) -> Path:
    validate_db_name(db_name)
    root = (data_dir / "filestore").resolve()
    candidate = (root / db_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ConfigError(
            f"Filestore path {candidate} escapes resolved filestore root {root}"
        ) from None
    return candidate
