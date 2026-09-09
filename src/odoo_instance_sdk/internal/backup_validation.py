from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from odoo_instance_sdk.exceptions import BackupValidationUnavailableError

_REQUIRED_ROOT_MEMBERS = {"manifest.json", "dump.sql"}
_MAX_ZIP_ENTRIES = 4096
_MAX_ZIP_ENTRY_BYTES = 512 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ZIP_COMPRESSION_RATIO = 100
_MAX_ZIP_DIAGNOSTICS = 32
_MAX_MANIFEST_BYTES = 64 * 1024
_SUPPORTED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


@dataclass(slots=True, kw_only=True, frozen=True)
class ZipValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    db_name: str | None = None
    db_version: str | None = None
    entry_sizes: tuple[tuple[str, int], ...] = ()
    uncompressed_bytes: int = 0


@dataclass(slots=True, kw_only=True, frozen=True)
class DumpValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    unavailable: bool = False


def validate_zip(path: Path) -> ZipValidationResult:  # noqa: C901
    errors: list[str] = []

    def add_error(message: str) -> None:
        if len(errors) < _MAX_ZIP_DIAGNOSTICS:
            errors.append(message)

    db_name: str | None = None
    db_version: str | None = None

    if not zipfile.is_zipfile(path):
        return ZipValidationResult(valid=False, errors=("Not a valid ZIP file",))

    entry_sizes: list[tuple[str, int]] = []
    uncompressed_bytes = 0
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ZIP_ENTRIES:
                return ZipValidationResult(
                    valid=False,
                    errors=("ZIP contains too many members",),
                )
            names: set[str] = set()
            for info in infos:
                if info.filename in names:
                    add_error(f"Duplicate ZIP member: {info.filename}")
                names.add(info.filename)
                if info.flag_bits & 0x1:
                    add_error(f"Encrypted ZIP member: {info.filename}")
                if info.compress_type not in _SUPPORTED_ZIP_COMPRESSION:
                    add_error(f"Unsupported ZIP compression: {info.filename}")
                if info.file_size > _MAX_ZIP_ENTRY_BYTES:
                    add_error(f"ZIP member is too large: {info.filename}")
                if info.file_size and (
                    info.compress_size == 0
                    or info.file_size > info.compress_size * _MAX_ZIP_COMPRESSION_RATIO
                ):
                    add_error(f"ZIP member compression ratio is unsafe: {info.filename}")
                uncompressed_bytes += info.file_size
                if uncompressed_bytes > _MAX_ZIP_TOTAL_BYTES:
                    add_error("ZIP uncompressed size is too large")
                    break
                entry_sizes.append((info.filename, info.file_size))
            try:
                free_bytes = shutil.disk_usage(path.parent).free
            except OSError:
                free_bytes = 0
            if uncompressed_bytes > free_bytes:
                add_error("ZIP requires more space than is available")
            if not errors:
                bad = zf.testzip()
                if bad is not None:
                    add_error(f"CRC corruption in: {bad}")

            root_members = {n for n in names if "/" not in n}
            missing = _REQUIRED_ROOT_MEMBERS - root_members
            if missing:
                add_error(f"Missing required members: {', '.join(sorted(missing))}")

            if "manifest.json" in names:
                try:
                    with zf.open("manifest.json") as stream:
                        manifest_bytes = bytearray()
                        while chunk := stream.read(min(64 * 1024, _MAX_MANIFEST_BYTES + 1)):
                            manifest_bytes.extend(chunk)
                            if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
                                break
                    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
                        add_error("manifest.json is too large")
                    else:
                        manifest = json.loads(manifest_bytes)
                        if isinstance(manifest, dict):
                            db_name = manifest.get("db_name")
                            db_version = manifest.get("db_version")
                        else:
                            add_error("manifest.json is not a JSON object")
                except json.JSONDecodeError as e:
                    add_error(f"Invalid manifest.json: {e}")
    except zipfile.BadZipFile as e:
        add_error(str(e))

    return ZipValidationResult(
        valid=len(errors) == 0,
        errors=tuple(errors),
        db_name=db_name,
        db_version=db_version,
        entry_sizes=tuple(entry_sizes),
        uncompressed_bytes=uncompressed_bytes,
    )


def validate_dump(
    path: Path,
    *,
    timeout: float = 60.0,
    raise_if_unavailable: bool = False,
    step_id: str | None = None,
) -> DumpValidationResult:
    exe = shutil.which("pg_restore")
    if exe is None:
        if raise_if_unavailable:
            raise BackupValidationUnavailableError("pg_restore not found in PATH")
        return DumpValidationResult(valid=False, unavailable=True)

    try:
        from odoo_instance_sdk.internal.proc import ProcessExecutionError, run_captured

        result = run_captured(
            [exe, "--list", str(path)],
            timeout=timeout,
            text=True,
            step_id=step_id or "backup.validate",
            read_only=True,
        )
        if result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else ""
            return DumpValidationResult(
                valid=False,
                errors=(stderr.strip() or "pg_restore exited with non-zero status",),
            )
        return DumpValidationResult(valid=True)
    except subprocess.TimeoutExpired:
        return DumpValidationResult(valid=False, errors=("pg_restore timed out",))
    except ProcessExecutionError as exc:
        if exc.__class__.__name__ == "ProcessTimeoutError":
            return DumpValidationResult(valid=False, errors=("pg_restore timed out",))
        return DumpValidationResult(valid=False, unavailable=True)
    except FileNotFoundError:
        if raise_if_unavailable:
            raise BackupValidationUnavailableError(f"pg_restore not found at {exe}")
        return DumpValidationResult(valid=False, unavailable=True)
