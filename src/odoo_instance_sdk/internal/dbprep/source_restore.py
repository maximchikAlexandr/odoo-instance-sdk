"""Verified backup restore inputs and materialization helpers."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import shutil
import stat
import uuid
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import msgspec

from odoo_instance_sdk.exceptions import (
    BackupUnknownFormatError,
    BackupUnsupportedFormatError,
    ConfigError,
    InstanceConfigurationError,
)
from odoo_instance_sdk.internal.db_name import validate_db_name
from odoo_instance_sdk.internal.project_runtime import uv_run_prefix
from odoo_instance_sdk.models import Backup, BackupFormat, LocalArchiveRestoreSource


@dataclass(frozen=True, slots=True)
class _RemoteRestoreSource:
    """Select the existing remote backup source."""


@dataclass(frozen=True, slots=True)
class _CatalogueRestoreSource:
    """Select one exact, already published catalogue backup."""

    backup_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class _LocalArchiveRestoreSource:
    """Select one caller-owned local Odoo ZIP for restore."""

    path: Path


_RestoreSource = _RemoteRestoreSource | _CatalogueRestoreSource | _LocalArchiveRestoreSource
_RestoreSourceInput = _RestoreSource | LocalArchiveRestoreSource | uuid.UUID | str | None


@dataclass(frozen=True, slots=True)
class ProjectRuntimeBinding:
    python_executable: str | None
    odoo_bin: str
    runtime_cwd: Path
    python_selector: str | None = None
    uv_executable: str | None = None

    @property
    def command_prefix(self) -> tuple[str, ...]:
        if self.python_selector is not None:
            return uv_run_prefix(
                self.python_selector,
                uv_executable=self.uv_executable,
                command=("python", self.odoo_bin),
            )
        if self.python_executable is None:
            raise InstanceConfigurationError("project runtime has no Python executable")
        return (self.python_executable, self.odoo_bin)


class DatabasePreparationFailureContext(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
    omit_defaults=True,
):
    """Secret-free identifiers retained after a preparation failure."""

    retained_backup_id: uuid.UUID | None = None
    retained_database: str | None = None
    backup_id: uuid.UUID | None = None
    database_confirmed: bool | None = None
    restore_state: Literal["complete", "incomplete"] | None = None
    default_switch_confirmed: bool | None = None
    restore_stage_id: str | None = None
    restore_stage_elapsed: float | None = None
    source_kind: Literal["catalogue", "local_archive"] | None = None
    source_sha256: str | None = None
    project_id: str | None = None
    effective_config: str | None = None
    managed_filestore: str | None = None
    binding_published: bool | None = None


@dataclass(frozen=True, slots=True)
class SelectedBackupRestorePayload:
    """Validated retained-backup inputs for the stopped selected environment.

    This is deliberately owned by the preparation module: replacement reuses
    the same backup validation and process construction boundary as checkout.
    The payload is only materialized while executing the captured restore
    operation, never as part of the public command projection.
    """

    archive_path: Path = field(repr=False)
    dump_path: Path = field(repr=False)
    verified_snapshot_path: Path = field(repr=False)
    filestore_members: tuple[str, ...]
    database_name: str
    file_identity: tuple[int, int, int, int]
    verified_size: int
    verified_sha256: str
    zip_entry_sizes: tuple[tuple[str, int], ...] = ()
    zip_uncompressed_bytes: int = 0
    format: BackupFormat = BackupFormat.ZIP
    source_kind: Literal["catalogue", "local_archive"] = "catalogue"


_CLASSIFICATION_PREFIX_BYTES = 8


def _verified_file(  # noqa: C901
    path: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    source_label: str = "selected backup",
) -> tuple[tuple[int, int, int, int], str, bytes]:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError(f"{source_label} is not a regular file")
        if expected_size is not None and info.st_size != expected_size:
            raise ConfigError(f"{source_label} size does not match captured evidence")
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        digest = hashlib.sha256()
        prefix = bytearray()
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                if len(prefix) < _CLASSIFICATION_PREFIX_BYTES:
                    prefix.extend(chunk[: _CLASSIFICATION_PREFIX_BYTES - len(prefix)])
            final_info = os.fstat(stream.fileno())
        final_identity = (
            final_info.st_dev,
            final_info.st_ino,
            final_info.st_size,
            final_info.st_mtime_ns,
        )
        if final_identity != identity:
            raise ConfigError(f"{source_label} changed during capture")
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ConfigError(f"{source_label} is not a regular file") from exc
        raise ConfigError(f"{source_label} file is unavailable") from exc
    except ValueError as exc:
        raise ConfigError(f"{source_label} file is unavailable") from exc
    finally:
        if descriptor != -1:
            with contextlib.suppress(OSError):
                os.close(descriptor)
    actual_digest = digest.hexdigest()
    if expected_sha256 and actual_digest != expected_sha256:
        raise ConfigError(f"{source_label} content does not match captured evidence")
    return identity, actual_digest, bytes(prefix)


def _sha256_no_follow(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        raise


def _materialize_verified_snapshot(  # noqa: C901
    payload: SelectedBackupRestorePayload,
) -> SelectedBackupRestorePayload:
    """Copy the catalogued source through one no-follow descriptor.

    The descriptor is the immutable boundary between source revalidation and
    restore.  Consumers never reopen the user-controlled catalogue path.
    """
    snapshot = payload.verified_snapshot_path
    if snapshot.exists() or snapshot.is_symlink():
        _assert_verified_snapshot_unchanged(payload)
        return payload
    try:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(snapshot.parent).free < payload.verified_size:
            raise ConfigError(  # noqa: TRY301
                "selected backup snapshot exceeds available space"
            )
        source_fd = os.open(payload.archive_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        target_fd = -1
        try:
            source_info = os.fstat(source_fd)
            if not stat.S_ISREG(source_info.st_mode):
                raise ConfigError("selected backup is not a regular file")
            source_identity = (
                source_info.st_dev,
                source_info.st_ino,
                source_info.st_size,
                source_info.st_mtime_ns,
            )
            if source_identity != payload.file_identity:
                raise ConfigError("selected backup file identity changed before restore")
            target_fd = os.open(
                snapshot,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            digest = hashlib.sha256()
            copied = 0
            with os.fdopen(source_fd, "rb") as source:
                source_fd = -1
                with os.fdopen(target_fd, "wb") as target:
                    target_fd = -1
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > payload.verified_size:
                            raise ConfigError("selected backup exceeded catalogue size")
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
            if copied != payload.verified_size:
                raise ConfigError("selected backup size changed before restore")
            if digest.hexdigest() != payload.verified_sha256:
                raise ConfigError("selected backup content changed before restore")
            if stat.S_IMODE(os.stat(snapshot, follow_symlinks=False).st_mode) != 0o600:
                raise ConfigError("selected backup snapshot permissions are unsafe")
        finally:
            if source_fd != -1:
                with contextlib.suppress(OSError):
                    os.close(source_fd)
            if target_fd != -1:
                with contextlib.suppress(OSError):
                    os.close(target_fd)
    except ConfigError:
        with contextlib.suppress(OSError):
            snapshot.unlink()
        raise
    except OSError as exc:
        with contextlib.suppress(OSError):
            snapshot.unlink()
        raise ConfigError("selected backup snapshot is unavailable") from exc
    return payload


def _assert_verified_snapshot_unchanged(payload: SelectedBackupRestorePayload) -> None:
    """Fail closed if the private verified snapshot was tampered with."""
    try:
        info = os.stat(payload.verified_snapshot_path, follow_symlinks=False)
    except OSError as exc:
        raise ConfigError("selected backup snapshot disappeared before restore") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size != payload.verified_size
    ):
        raise ConfigError("selected backup snapshot identity changed before restore")
    try:
        digest = _sha256_no_follow(payload.verified_snapshot_path)
    except OSError as exc:
        raise ConfigError("selected backup snapshot disappeared before restore") from exc
    if digest != payload.verified_sha256:
        raise ConfigError("selected backup snapshot content changed before restore")


def _open_verified_zip(path: Path) -> zipfile.ZipFile:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigError("selected backup archive is unavailable") from exc
    try:
        return zipfile.ZipFile(os.fdopen(descriptor, "rb"))
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        raise


def capture_selected_backup_restore(  # noqa: C901
    selected: Backup | LocalArchiveRestoreSource | _LocalArchiveRestoreSource,
    *,
    data_dir: Path | None = None,
    snapshot_directory: Path | None = None,
) -> SelectedBackupRestorePayload:
    """Read and validate one selected archive without creating staging files."""
    from odoo_instance_sdk.exceptions import BackupValidationUnavailableError
    from odoo_instance_sdk.internal.backup_validation import (
        classify_backup_prefix,
        raise_restore_preflight_errors,
        raise_zip_validation_error,
        validate_dump,
        validate_zip,
    )

    if isinstance(selected, Backup):
        path = Path(selected.path)
        database_name = selected.database_name
        archive_format = selected.format
        expected_size = selected.size_bytes
        expected_sha256 = selected.sha256
        token = selected.id.hex
        source_kind: Literal["catalogue", "local_archive"] = "catalogue"
        source_label = "selected backup"
    else:
        local = (
            selected
            if isinstance(selected, _LocalArchiveRestoreSource)
            else _LocalArchiveRestoreSource(Path(selected.path))
        )
        path = local.path
        database_name = None
        archive_format = BackupFormat.ZIP
        expected_size = None
        expected_sha256 = None
        token = uuid.uuid4().hex
        source_kind = "local_archive"
        source_label = "selected local archive"
        if snapshot_directory is None:
            raise ConfigError("local archive snapshot directory is required")

    file_identity, verified_sha256, classification_prefix = _verified_file(
        path,
        expected_size=expected_size,
        expected_sha256=expected_sha256,
        source_label=source_label,
    )
    if source_kind == "local_archive":
        local_format = classify_backup_prefix(classification_prefix)
        if local_format == "postgres_custom_dump":
            raise BackupUnsupportedFormatError()
        if local_format == "unknown":
            raise BackupUnknownFormatError()
    staging_directory = Path(snapshot_directory) if snapshot_directory is not None else path.parent
    snapshot_path = staging_directory / f".odcli-verified-{token}.backup"
    if archive_format == BackupFormat.DUMP:
        try:
            dump_validation = validate_dump(path, timeout=30.0, raise_if_unavailable=True)
        except BackupValidationUnavailableError as exc:
            raise ConfigError("selected native dump validation is unavailable") from exc
        if not dump_validation.valid:
            raise ConfigError("selected native dump is unavailable or invalid")
        try:
            return SelectedBackupRestorePayload(
                archive_path=path,
                dump_path=snapshot_path,
                verified_snapshot_path=snapshot_path,
                filestore_members=(),
                database_name=database_name or "",
                file_identity=file_identity,
                verified_size=file_identity[2],
                verified_sha256=verified_sha256,
                format=BackupFormat.DUMP,
                source_kind=source_kind,
            )
        except OSError as exc:
            raise ConfigError("selected native dump is unavailable or invalid") from exc
    zip_validation = validate_zip(path, data_dir=data_dir)
    if not zip_validation.valid:
        raise_zip_validation_error(zip_validation)
        raise ConfigError("selected backup archive is unavailable or invalid")  # pragma: no cover
    if not isinstance(zip_validation.db_name, str):
        raise ConfigError(f"{source_label} manifest has no database name")
    validate_db_name(zip_validation.db_name)
    if database_name is not None and zip_validation.db_name != database_name:
        raise ConfigError("selected backup database name does not match catalog metadata")
    raise_restore_preflight_errors(zip_validation.uncompressed_bytes, data_dir)
    try:
        with _open_verified_zip(path) as archive:
            archive.getinfo("dump.sql")
            files: list[str] = []
            prefix = "filestore/"
            for info in archive.infolist():
                name = info.filename
                if not name.startswith(prefix) or info.is_dir():
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ConfigError("selected backup contains a symlinked filestore path")
                parts = tuple(part for part in name.removeprefix(prefix).split("/") if part)
                if not parts or any(part in {".", ".."} for part in parts):
                    raise ConfigError("selected backup contains an unsafe filestore path")
                relative = parts[1:] if parts[0] == zip_validation.db_name else parts
                if relative:
                    files.append("/".join(relative))
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ConfigError("selected backup archive is unavailable or invalid") from exc
    if not files:
        raise ConfigError("selected backup does not contain a usable filestore")
    return SelectedBackupRestorePayload(
        archive_path=path,
        dump_path=staging_directory / f".odcli-restore-{token}.dump",
        verified_snapshot_path=snapshot_path,
        filestore_members=tuple(sorted(files)),
        database_name=zip_validation.db_name,
        file_identity=file_identity,
        verified_size=file_identity[2],
        verified_sha256=verified_sha256,
        zip_entry_sizes=zip_validation.entry_sizes,
        zip_uncompressed_bytes=zip_validation.uncompressed_bytes,
        format=BackupFormat.ZIP,
        source_kind=source_kind,
    )


def capture_local_archive_restore(
    source: LocalArchiveRestoreSource | _LocalArchiveRestoreSource,
    *,
    snapshot_directory: Path,
    data_dir: Path | None = None,
) -> SelectedBackupRestorePayload:
    """Capture local archive evidence without creating a snapshot."""
    return capture_selected_backup_restore(
        source,
        data_dir=data_dir,
        snapshot_directory=snapshot_directory,
    )


def cleanup_selected_backup_restore(
    payload: SelectedBackupRestorePayload,
    *,
    staging_paths: Sequence[Path] = (),
) -> None:
    """Remove only command-owned staging artifacts, preserving the source."""
    for path in {payload.dump_path, payload.verified_snapshot_path}:
        with contextlib.suppress(OSError):
            if path.is_symlink() or path.is_file():
                path.unlink()
    for staging in staging_paths:
        candidate = Path(staging)
        with contextlib.suppress(OSError):
            if candidate.is_symlink() or candidate.is_file():
                candidate.unlink()
            elif candidate.is_dir():
                shutil.rmtree(candidate)


def materialize_selected_backup_dump(payload: SelectedBackupRestorePayload) -> None:
    """Stream the selected dump into its captured temporary execution path."""
    if payload.format == BackupFormat.DUMP:
        _materialize_verified_snapshot(payload)
        _assert_verified_snapshot_unchanged(payload)
        return
    if payload.dump_path.exists() or payload.dump_path.is_symlink():
        raise ConfigError("selected restore dump target is not absent")
    _materialize_verified_snapshot(payload)
    _assert_verified_snapshot_unchanged(payload)
    try:
        with (
            _open_verified_zip(payload.verified_snapshot_path) as archive,
            archive.open("dump.sql") as source,
            payload.dump_path.open("wb") as target,
        ):
            dump_size = dict(payload.zip_entry_sizes).get("dump.sql")
            if dump_size is None:
                raise ConfigError("selected backup dump metadata is missing")  # noqa: TRY301
            copied = 0
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > dump_size:
                    raise ConfigError("selected backup dump exceeds its validated limit")  # noqa: TRY301
                target.write(chunk)
            if copied != dump_size:
                raise ConfigError("selected backup dump size changed during restore")  # noqa: TRY301
    except ConfigError:
        with contextlib.suppress(OSError):
            payload.dump_path.unlink()
        raise
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        with contextlib.suppress(OSError):
            payload.dump_path.unlink()
        raise ConfigError("selected backup dump is unavailable or invalid") from exc


def materialize_selected_backup_filestore(  # noqa: C901
    destination: Path, payload: SelectedBackupRestorePayload
) -> None:
    """Stream validated selected-backup files under a contained root."""
    if destination.exists() or destination.is_symlink():
        raise ConfigError("selected restore filestore target is not absent")
    _materialize_verified_snapshot(payload)
    _assert_verified_snapshot_unchanged(payload)
    try:
        if shutil.disk_usage(destination.parent).free < payload.zip_uncompressed_bytes:
            raise ConfigError("selected backup filestore exceeds available space")
    except OSError as exc:
        raise ConfigError("selected backup filestore space is unavailable") from exc
    destination.mkdir(parents=True)
    root = destination.resolve()
    try:
        with _open_verified_zip(payload.verified_snapshot_path) as archive:
            copied_total = 0
            for relative in payload.filestore_members:
                source_name = f"filestore/{payload.database_name}/{relative}"
                if source_name not in archive.namelist():
                    source_name = f"filestore/{relative}"
                candidate = (destination / relative).resolve()
                if not candidate.is_relative_to(root):
                    raise ConfigError("selected backup filestore escapes its target")  # noqa: TRY301
                candidate.parent.mkdir(parents=True, exist_ok=True)
                info = archive.getinfo(source_name)
                expected_size = dict(payload.zip_entry_sizes).get(source_name)
                if expected_size is None or info.file_size != expected_size:
                    raise ConfigError("selected backup filestore metadata changed")  # noqa: TRY301
                copied = 0
                with archive.open(info) as source, candidate.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        copied_total += len(chunk)
                        if copied > expected_size or copied_total > payload.zip_uncompressed_bytes:
                            raise ConfigError(  # noqa: TRY301
                                "selected backup filestore exceeds its validated limit"
                            )
                        target.write(chunk)
                if copied != expected_size:
                    raise ConfigError("selected backup filestore size changed during restore")  # noqa: TRY301
    except BaseException:
        with contextlib.suppress(OSError):
            shutil.rmtree(destination)
        raise
    if not destination.is_dir() or destination.is_symlink():
        raise ConfigError("selected restore filestore verification failed")
