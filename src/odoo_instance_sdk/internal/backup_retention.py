"""User-level backup retention policy stored in the existing user TOML."""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import tomllib
from pathlib import Path

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.models import BackupRetentionPolicy

_RETENTION_KEYS = ("retention_days", "auto_prune")
_BACKUP_HEADER = re.compile(r"^\s*\[backup\]\s*(?:#.*)?$")
_SECTION_HEADER = re.compile(r"^\s*\[[^\[].*\]\s*(?:#.*)?$")
_KEY_LINE = {key: re.compile(rf"^(\s*){key}(\s*=).*$") for key in _RETENTION_KEYS}


def retention_path() -> Path:
    from odoo_instance_sdk.internal.paths import get_config_root

    return (get_config_root(ensure_exists=False) / "user.toml").resolve(strict=False)


def _validate_days(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError("backup.retention_days must be a positive integer")
    return value


def _validate_auto_prune(value: object) -> bool:
    if type(value) is not bool:
        raise ConfigError("backup.auto_prune must be a boolean")
    return value


def read_retention_policy(path: Path | None = None) -> BackupRetentionPolicy:
    destination = (path or retention_path()).resolve(strict=False)
    if not destination.exists():
        return BackupRetentionPolicy(path=str(destination))
    try:
        data = tomllib.loads(destination.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"unable to read backup retention settings: {destination}") from exc
    section = data.get("backup", {})
    if not isinstance(section, dict):
        raise ConfigError("backup settings must be a TOML table")
    days = _validate_days(section["retention_days"]) if "retention_days" in section else 14
    auto = _validate_auto_prune(section["auto_prune"]) if "auto_prune" in section else False
    return BackupRetentionPolicy(retention_days=days, auto_prune=auto, path=str(destination))


def _patch_backup_section(content: str, *, retention_days: int, auto_prune: bool) -> str:
    values = {"retention_days": str(retention_days), "auto_prune": str(auto_prune).lower()}
    lines = content.splitlines(keepends=True)
    backup_start: int | None = None
    backup_end = len(lines)
    for index, line in enumerate(lines):
        if _BACKUP_HEADER.match(line.rstrip("\r\n")):
            backup_start = index
            continue
        if backup_start is not None and _SECTION_HEADER.match(line.rstrip("\r\n")):
            backup_end = index
            break
    if backup_start is None:
        prefix = content if not content or content.endswith(("\n", "\r")) else content + "\n"
        return (
            prefix
            + "[backup]\nretention_days = "
            + values["retention_days"]
            + "\nauto_prune = "
            + values["auto_prune"]
            + "\n"
        )

    seen: set[str] = set()
    for index in range(backup_start + 1, backup_end):
        for key, pattern in _KEY_LINE.items():
            if pattern.match(lines[index].rstrip("\r\n")):
                ending = "\r\n" if lines[index].endswith("\r\n") else "\n"
                match = pattern.match(lines[index].rstrip("\r\n"))
                assert match is not None
                lines[index] = f"{match.group(1)}{key} = {values[key]}{ending}"
                seen.add(key)
    insert_at = backup_end
    additions = [f"{key} = {values[key]}\n" for key in _RETENTION_KEYS if key not in seen]
    if (
        additions
        and lines[backup_end - 1 : backup_end]
        and not lines[backup_end - 1].endswith(("\n", "\r"))
    ):
        lines[backup_end - 1] += "\n"
    lines[insert_at:insert_at] = additions
    return "".join(lines)


def write_retention_policy(policy: BackupRetentionPolicy) -> bool:
    destination = Path(policy.path).resolve(strict=False)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    original = destination.read_text(encoding="utf-8") if destination.exists() else ""
    updated = _patch_backup_section(
        original, retention_days=policy.retention_days, auto_prune=policy.auto_prune
    )
    if updated == original and destination.exists():
        os.chmod(destination, 0o600)
        return False
    fd, temporary = tempfile.mkstemp(
        dir=str(destination.parent), prefix=".user.toml.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(updated)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    return True
