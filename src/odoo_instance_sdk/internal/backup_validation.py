from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from odoo_instance_sdk.exceptions import BackupValidationUnavailableError

_REQUIRED_ROOT_MEMBERS = {"manifest.json", "dump.sql"}


@dataclass(slots=True, kw_only=True, frozen=True)
class ZipValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    db_name: str | None = None
    db_version: str | None = None


@dataclass(slots=True, kw_only=True, frozen=True)
class DumpValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    unavailable: bool = False


def validate_zip(path: Path) -> ZipValidationResult:
    errors: list[str] = []
    db_name: str | None = None
    db_version: str | None = None

    if not zipfile.is_zipfile(path):
        return ZipValidationResult(valid=False, errors=("Not a valid ZIP file",))

    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad is not None:
                errors.append(f"CRC corruption in: {bad}")

            names = set(zf.namelist())
            root_members = {n for n in names if "/" not in n}
            missing = _REQUIRED_ROOT_MEMBERS - root_members
            if missing:
                errors.append(f"Missing required members: {', '.join(sorted(missing))}")

            if "manifest.json" in names:
                try:
                    manifest = json.loads(zf.read("manifest.json"))
                    if isinstance(manifest, dict):
                        db_name = manifest.get("db_name")
                        db_version = manifest.get("db_version")
                    else:
                        errors.append("manifest.json is not a JSON object")
                except json.JSONDecodeError as e:
                    errors.append(f"Invalid manifest.json: {e}")
    except zipfile.BadZipFile as e:
        errors.append(str(e))

    return ZipValidationResult(
        valid=len(errors) == 0,
        errors=tuple(errors),
        db_name=db_name,
        db_version=db_version,
    )


def validate_dump(
    path: Path,
    *,
    timeout: float = 60.0,
    raise_if_unavailable: bool = False,
) -> DumpValidationResult:
    exe = shutil.which("pg_restore")
    if exe is None:
        if raise_if_unavailable:
            raise BackupValidationUnavailableError("pg_restore not found in PATH")
        return DumpValidationResult(valid=False, unavailable=True)

    try:
        result = subprocess.run(
            [exe, "--list", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            return DumpValidationResult(
                valid=False,
                errors=(result.stderr.strip() or "pg_restore exited with non-zero status",),
            )
        return DumpValidationResult(valid=True)
    except subprocess.TimeoutExpired:
        return DumpValidationResult(valid=False, errors=("pg_restore timed out",))
    except FileNotFoundError:
        if raise_if_unavailable:
            raise BackupValidationUnavailableError(f"pg_restore not found at {exe}")
        return DumpValidationResult(valid=False, unavailable=True)
