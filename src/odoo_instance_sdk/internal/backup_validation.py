from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue

from odoo_instance_sdk.exceptions import (
    BackupCorruptError,
    BackupInsufficientDiskError,
    BackupOperatorLimitError,
    BackupUnsafeError,
    BackupValidationUnavailableError,
)

_REQUIRED_ROOT_MEMBERS = {"manifest.json", "dump.sql"}
_MAX_ZIP_ENTRIES = 4096
_MAX_ZIP_COMPRESSION_RATIO = 100
_MAX_ZIP_DIAGNOSTICS = 32
_MAX_MANIFEST_BYTES = 64 * 1024
_STREAM_BUFFER_BYTES = 65536
_DISK_RESERVE_BYTES = 1024 * 1024 * 1024
_DISK_RESERVE_FRACTION = 0.10
_SUPPORTED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}

BACKUP_CORRUPT = "backup_corrupt"
BACKUP_UNSAFE = "backup_unsafe"
BACKUP_OPERATOR_LIMIT = "backup_operator_limit"
BACKUP_INSUFFICIENT_DISK = "backup_insufficient_disk"


@dataclass(slots=True, kw_only=True, frozen=True)
class ZipValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    error_code: str | None = None
    db_name: str | None = None
    db_version: str | None = None
    entry_sizes: tuple[tuple[str, int], ...] = ()
    uncompressed_bytes: int = 0


@dataclass(slots=True, kw_only=True, frozen=True)
class DumpValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    unavailable: bool = False


@dataclass(slots=True, kw_only=True, frozen=True)
class RestoreDiskPreflight:
    ok: bool
    error_code: str | None = None
    measured_bytes: int = 0
    available_bytes: int = 0
    reserve_bytes: int = 0


@dataclass(slots=True, kw_only=True, frozen=True)
class OperatorLimitPreflight:
    ok: bool
    error_code: str | None = None
    measured_bytes: int = 0
    allowed_bytes: int | None = None
    limit_source: str = "unset"


def _is_unsafe_path(name: str) -> bool:
    if not name or name.startswith(("/", "\\")):
        return True
    if name[1:2] == ":" and name[0].isalpha():
        return True
    parts = name.replace("\\", "/").split("/")
    return any(part in {".", ".."} for part in parts)


def _stream_test_crc(
    zf: zipfile.ZipFile, *, max_uncompressed_bytes: int
) -> tuple[str | None, bool]:
    """CRC-test every member with a bounded 65536-byte streaming buffer.

    ``zipfile.ZipFile.testzip`` uses a 1 MiB chunk and offers no buffer knob;
    this streaming loop keeps the validation memory ceiling explicit and
    never reads ``dump.sql`` or filestore members wholly into memory.
    """
    consumed = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        try:
            with zf.open(info, "r") as stream:
                while True:
                    chunk = stream.read(_STREAM_BUFFER_BYTES)
                    if not chunk:
                        break
                    consumed += len(chunk)
                    if consumed > max_uncompressed_bytes:
                        return None, True
        except (zipfile.BadZipFile, OSError, EOFError):
            return info.filename, False
    return None, False


def _manifest_db_version(manifest: JsonValue) -> str | None:
    if not isinstance(manifest, dict):
        return None
    version = manifest.get("version")
    if isinstance(version, str) and version:
        return version
    major = manifest.get("major_version")
    if isinstance(major, (str, int, float)) and major not in ("", None):
        return f"{major}.0"
    return None


def validate_zip(path: Path, *, data_dir: Path | None = None) -> ZipValidationResult:  # noqa: C901
    errors: list[str] = []
    unsafe = False
    corrupt = False

    def add_error(message: str) -> None:
        if len(errors) < _MAX_ZIP_DIAGNOSTICS:
            errors.append(message)

    db_name: str | None = None
    db_version: str | None = None

    if not zipfile.is_zipfile(path):
        return ZipValidationResult(
            valid=False,
            errors=("Not a valid ZIP file",),
            error_code=BACKUP_CORRUPT,
        )

    entry_sizes: list[tuple[str, int]] = []
    uncompressed_bytes = 0
    preflight_code: str | None = None
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ZIP_ENTRIES:
                return ZipValidationResult(
                    valid=False,
                    errors=("ZIP contains too many members",),
                    error_code=BACKUP_UNSAFE,
                )
            names: set[str] = set()
            for info in infos:
                if _is_unsafe_path(info.filename):
                    add_error(f"Unsafe ZIP member path: {info.filename}")
                    unsafe = True
                if info.filename in names:
                    add_error(f"Duplicate ZIP member: {info.filename}")
                    unsafe = True
                names.add(info.filename)
                if info.flag_bits & 0x1:
                    add_error(f"Encrypted ZIP member: {info.filename}")
                    unsafe = True
                if info.compress_type not in _SUPPORTED_ZIP_COMPRESSION:
                    add_error(f"Unsupported ZIP compression: {info.filename}")
                    unsafe = True
                if (
                    info.file_size
                    and (
                        info.compress_size == 0
                        or info.file_size > info.compress_size * _MAX_ZIP_COMPRESSION_RATIO
                    )
                    and not info.filename.endswith("dump.sql")
                ):
                    add_error(f"ZIP member compression ratio is unsafe: {info.filename}")
                    unsafe = True
                uncompressed_bytes += info.file_size
                entry_sizes.append((info.filename, info.file_size))

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
                            db_version = _manifest_db_version(manifest)
                        else:
                            add_error("manifest.json is not a JSON object")
                except json.JSONDecodeError as e:
                    add_error(f"Invalid manifest.json: {e}")

            if not errors:
                operator = enforce_operator_uncompressed_limit(uncompressed_bytes)
                disk = preflight_restore_disk_space(
                    uncompressed_bytes, data_dir if data_dir is not None else path.parent
                )
                crc_limit = disk.available_bytes - disk.reserve_bytes
                if operator.allowed_bytes is not None:
                    crc_limit = min(crc_limit, operator.allowed_bytes)
                if not operator.ok:
                    add_error("ZIP exceeds the configured operator uncompressed limit")
                    unsafe = True
                    preflight_code = BACKUP_OPERATOR_LIMIT
                elif not disk.ok:
                    add_error("ZIP exceeds the available restore disk bound")
                    unsafe = True
                    preflight_code = BACKUP_INSUFFICIENT_DISK
                bad, exceeded = (
                    _stream_test_crc(
                        zf,
                        max_uncompressed_bytes=max(0, crc_limit),
                    )
                    if preflight_code is None
                    else (None, False)
                )
                if exceeded:
                    add_error("ZIP CRC work exceeded the finite restore bound")
                    unsafe = True
                    preflight_code = (
                        BACKUP_OPERATOR_LIMIT
                        if operator.allowed_bytes is not None
                        else BACKUP_INSUFFICIENT_DISK
                    )
                if bad is not None:
                    add_error(f"CRC corruption in: {bad}")
                    corrupt = True
    except zipfile.BadZipFile as e:
        add_error(str(e))
        corrupt = True

    if errors and not corrupt and not unsafe:
        error_code: str | None = None
    elif corrupt:
        error_code = BACKUP_CORRUPT
    elif preflight_code is not None:
        error_code = preflight_code
    elif unsafe:
        error_code = BACKUP_UNSAFE
    else:
        error_code = None

    return ZipValidationResult(
        valid=len(errors) == 0,
        errors=tuple(errors),
        error_code=error_code,
        db_name=db_name,
        db_version=db_version,
        entry_sizes=tuple(entry_sizes),
        uncompressed_bytes=uncompressed_bytes,
    )


def preflight_restore_disk_space(
    uncompressed_bytes: int, data_dir: Path | None
) -> RestoreDiskPreflight:
    """Compare declared uncompressed size to local free space minus a reserve.

    The base is ``Path(data_dir).resolve()`` when ``data_dir`` is set, else
    ``get_backups_dir()``.  The reserve is ``max(1 GiB, 10% of free)``.
    """
    from odoo_instance_sdk.internal.paths import get_backups_dir

    base = Path(data_dir).resolve() if data_dir is not None else get_backups_dir()
    try:
        usage = shutil.disk_usage(base)
    except OSError:
        return RestoreDiskPreflight(
            ok=False,
            error_code=BACKUP_INSUFFICIENT_DISK,
            measured_bytes=uncompressed_bytes,
            available_bytes=0,
            reserve_bytes=0,
        )
    reserve = max(_DISK_RESERVE_BYTES, int(usage.free * _DISK_RESERVE_FRACTION))
    available = usage.free - reserve
    if uncompressed_bytes <= available:
        return RestoreDiskPreflight(
            ok=True,
            measured_bytes=uncompressed_bytes,
            available_bytes=usage.free,
            reserve_bytes=reserve,
        )
    return RestoreDiskPreflight(
        ok=False,
        error_code=BACKUP_INSUFFICIENT_DISK,
        measured_bytes=uncompressed_bytes,
        available_bytes=usage.free,
        reserve_bytes=reserve,
    )


def read_operator_max_uncompressed_bytes() -> tuple[int | None, str]:
    """Return ``(max_bytes, limit_source)`` from ``get_config_root()/user.toml``.

    Absent key means no ceiling (``limit_source='unset'``).
    """
    from odoo_instance_sdk.internal.paths import get_config_root

    path = get_config_root(ensure_exists=False) / "user.toml"
    if not path.is_file():
        return None, "unset"
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None, "unset"
    section = data.get("backup") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return None, "unset"
    value = section.get("max_uncompressed_bytes")
    if isinstance(value, bool) or not isinstance(value, int):
        return None, "unset"
    if value <= 0:
        return None, "unset"
    return value, "user.toml"


def enforce_operator_uncompressed_limit(uncompressed_bytes: int) -> OperatorLimitPreflight:
    allowed, source = read_operator_max_uncompressed_bytes()
    if allowed is None:
        return OperatorLimitPreflight(
            ok=True,
            measured_bytes=uncompressed_bytes,
            allowed_bytes=None,
            limit_source=source,
        )
    if uncompressed_bytes <= allowed:
        return OperatorLimitPreflight(
            ok=True,
            measured_bytes=uncompressed_bytes,
            allowed_bytes=allowed,
            limit_source=source,
        )
    return OperatorLimitPreflight(
        ok=False,
        error_code=BACKUP_OPERATOR_LIMIT,
        measured_bytes=uncompressed_bytes,
        allowed_bytes=allowed,
        limit_source=source,
    )


def raise_restore_preflight_errors(uncompressed_bytes: int, data_dir: Path | None) -> None:
    """Run the restore preflight and raise typed errors for CLI rendering."""
    operator = enforce_operator_uncompressed_limit(uncompressed_bytes)
    if not operator.ok:
        raise BackupOperatorLimitError(
            "backup exceeds operator maximum uncompressed size",
            details={
                "measured_bytes": operator.measured_bytes,
                "allowed_bytes": operator.allowed_bytes,
                "limit_source": operator.limit_source,
            },
        )
    disk = preflight_restore_disk_space(uncompressed_bytes, data_dir)
    if not disk.ok:
        raise BackupInsufficientDiskError(
            "insufficient local disk space for restore",
            details={
                "measured_bytes": disk.measured_bytes,
                "available_bytes": disk.available_bytes,
                "reserve_bytes": disk.reserve_bytes,
            },
        )


def raise_zip_validation_error(result: ZipValidationResult) -> None:
    """Translate a structural validation failure into a typed error."""
    if result.error_code == BACKUP_CORRUPT:
        raise BackupCorruptError(
            "backup archive is corrupt",
            details={"errors": list(result.errors)},
        )
    if result.error_code == BACKUP_UNSAFE:
        raise BackupUnsafeError(
            "backup archive structure is unsafe",
            details={"errors": list(result.errors)},
        )
    if result.error_code == BACKUP_OPERATOR_LIMIT:
        raise BackupOperatorLimitError(
            "backup exceeds operator maximum uncompressed size",
            details={"errors": list(result.errors)},
        )
    if result.error_code == BACKUP_INSUFFICIENT_DISK:
        raise BackupInsufficientDiskError(
            "insufficient local disk space for restore",
            details={"errors": list(result.errors)},
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
