"""Trusted provenance for prepared project virtual environments."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from odoo_instance_sdk.internal.paths import get_user_root

_ORIGIN_DIRECTORY = "venv-origins"
_ORIGIN_FILENAME = "primary-venv-origin.json"
type OriginRecord = dict[str, str | None]


@dataclass(frozen=True, slots=True)
class OriginStage:
    directory: Path
    staged_name: str
    final_name: str = _ORIGIN_FILENAME


def normalize_requested_python(project_root: Path, requested: str | Path) -> Path | None:
    """Normalize a request without resolving venv launcher symlinks."""
    raw = str(requested)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        if raw.startswith("."):
            path = project_root / path
        else:
            selected = shutil.which(raw)
            if selected is None:
                return None
            path = Path(selected)
    try:
        return Path(os.path.abspath(path))
    except (OSError, RuntimeError):
        return None


def origin_record_path(project_root: Path) -> Path:
    canonical_project = project_root.resolve(strict=False)
    digest = hashlib.sha256(str(canonical_project).encode("utf-8")).hexdigest()
    return get_user_root(ensure_exists=False) / _ORIGIN_DIRECTORY / f"{digest}.json"


def read_origin_record(project_root: Path) -> OriginRecord | None:
    path = origin_record_path(project_root)
    try:
        with _opened_regular(path, os.O_RDONLY) as stream:
            record = json.load(stream)
        if not isinstance(record, dict):
            return None
        if not all(
            isinstance(key, str) and (value is None or isinstance(value, str))
            for key, value in record.items()
        ):
            return None
        typed_record = cast("OriginRecord", record)
        return (
            typed_record
            if typed_record.get("project_root") == str(project_root.resolve(strict=True))
            else None
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def stage_origin_record(
    project_root: Path,
    *,
    venv_root: Path,
    requested: str | Path,
    interpreter: str | Path,
    manifest_bytes: bytes,
) -> OriginStage:
    requested_path = normalize_requested_python(project_root, requested)
    candidate = _candidate_identity(Path(interpreter), venv_root)
    record = {
        "project_root": str(project_root.resolve(strict=True)),
        "venv_root": str(venv_root.resolve(strict=False)),
        "requested": str(requested),
        "requested_path": str(requested_path) if requested_path is not None else None,
        "manifest_digest": _digest_bytes(manifest_bytes),
        **candidate,
    }
    return _stage_bytes(origin_record_path(project_root), json.dumps(record, sort_keys=True) + "\n")


def stage_origin_bytes(project_root: Path, content: bytes) -> OriginStage:
    return _stage_bytes(origin_record_path(project_root), content)


def publish_origin(stage: OriginStage) -> None:
    if os.name == "nt":
        _publish_origin_windows(stage)
        return
    directory_fd = _open_origin_directory(stage.directory)
    try:
        _validate_final_target(directory_fd, stage.final_name)
        os.replace(
            stage.staged_name,
            stage.final_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def discard_origin_stage(stage: OriginStage) -> None:
    if os.name == "nt":
        _discard_origin_stage_windows(stage)
        return
    with contextlib.suppress(FileNotFoundError):
        directory_fd = _open_origin_directory(stage.directory)
        try:
            os.unlink(stage.staged_name, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)


def read_origin_bytes(project_root: Path) -> bytes | None:
    path = origin_record_path(project_root)
    if os.name == "nt":
        try:
            with _opened_regular_windows(path) as stream:
                return stream.read()
        except FileNotFoundError:
            return None
    try:
        with _opened_regular(path, os.O_RDONLY) as stream:
            return stream.read()
    except FileNotFoundError:
        return None


def restore_origin_bytes(project_root: Path, content: bytes | None) -> None:
    path = origin_record_path(project_root)
    if content is not None:
        publish_origin(stage_origin_bytes(project_root, content))
        return
    if os.name == "nt":
        _remove_origin_windows(path)
        return
    directory_fd = _open_origin_directory(path.parent)
    try:
        _validate_final_target(directory_fd, path.name)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def restore_persisted_prepare_state(
    project_root: Path,
    *,
    manifest_path: Path,
    manifest_before: bytes,
    lock_path: Path,
    lock_before: bytes | None,
    origin_before: bytes | None,
) -> None:
    """Restore prepare outputs after a failure during final publication."""
    _restore_local_file(manifest_path, manifest_before)
    _restore_local_file(lock_path, lock_before)
    restore_origin_bytes(project_root, origin_before)


def remove_origin_record(project_root: Path) -> None:
    path = origin_record_path(project_root)
    if os.name == "nt":
        _remove_origin_windows(path)
        return
    directory_fd = _open_origin_directory(path.parent)
    try:
        _validate_final_target(directory_fd, path.name)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _origin_directory() -> Path:
    root = get_user_root(ensure_exists=True).resolve(strict=True)
    directory = root / _ORIGIN_DIRECTORY
    try:
        metadata = os.lstat(directory)
    except FileNotFoundError:
        directory.mkdir(mode=0o700)
        metadata = os.lstat(directory)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError(f"unsafe venv origin directory: {directory}")
    if not directory.resolve(strict=True).is_relative_to(root):
        raise OSError(f"venv origin directory escapes SDK root: {directory}")
    os.chmod(directory, 0o700)
    return directory


def _open_origin_directory(directory: Path) -> int:
    trusted = _origin_directory()
    if directory.resolve(strict=True) != trusted:
        raise OSError(f"unexpected venv origin directory: {directory}")
    return os.open(
        trusted,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW,
    )


def _stage_bytes(destination: Path, content: str | bytes) -> OriginStage:
    if os.name == "nt":
        return _stage_bytes_windows(destination, content)
    directory_fd = _open_origin_directory(destination.parent)
    staged_name = f".{destination.name}.{uuid.uuid4().hex}.prepare"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = -1
    try:
        _validate_final_target(directory_fd, destination.name)
        fd = os.open(staged_name, flags, 0o600, dir_fd=directory_fd)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(content.encode("utf-8") if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        return OriginStage(
            directory=destination.parent,
            staged_name=staged_name,
            final_name=destination.name,
        )
    except BaseException:
        if fd != -1:
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(staged_name, dir_fd=directory_fd)
        raise
    finally:
        os.close(directory_fd)


def _opened_regular(path: Path, flags: int) -> BinaryIO:
    if os.name == "nt":
        return _opened_regular_windows(path)
    fd = os.open(path, flags | os.O_NOFOLLOW)
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(fd)
        raise OSError(f"unsafe venv origin record: {path}")
    return os.fdopen(fd, "rb")


def _validate_final_target(directory_fd: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"unsafe venv origin target: {name}")


def _restore_local_file(path: Path, content: bytes | None) -> None:
    if content is None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.restore")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def origin_candidate_matches(
    project_root: Path,
    candidate: Path,
    record: OriginRecord,
    *,
    manifest_bytes: bytes,
) -> bool:
    """Verify current candidate and project identity against trusted origin."""
    try:
        if record.get("project_root") != str(project_root.resolve(strict=True)):
            return False
    except (OSError, RuntimeError):
        return False
    if record.get("manifest_digest") != _digest_bytes(manifest_bytes):
        return False
    try:
        expected = {
            key: record[key]
            for key in (
                "interpreter",
                "candidate_kind",
                "candidate_target",
                "candidate_target_path",
                "candidate_digest",
                "venv_cfg_digest",
            )
        }
        actual = _candidate_identity(candidate, Path(str(record["venv_root"])))
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return False
    return all(actual.get(key) == value for key, value in expected.items())


def _candidate_identity(candidate: Path, venv_root: Path) -> OriginRecord:
    candidate_path = Path(os.path.abspath(candidate))
    root = venv_root.resolve(strict=True)
    candidate_stat = os.lstat(candidate_path)
    if _is_reparse(candidate_stat):
        if not stat.S_ISLNK(candidate_stat.st_mode):
            raise OSError(f"unsafe prepared interpreter reparse point: {candidate_path}")
        target = os.readlink(candidate_path)
        target_path = Path(os.path.realpath(candidate_path))
        digest = _digest_file(target_path)
        kind = "symlink"
        target_path_value = str(target_path)
        target_value = str(target)
    elif stat.S_ISREG(candidate_stat.st_mode):
        kind = "regular"
        target_value = None
        target_path_value = None
        digest = _digest_file(candidate_path)
    else:
        raise OSError(f"unsafe prepared interpreter: {candidate_path}")
    return {
        "interpreter": str(candidate_path),
        "candidate_kind": kind,
        "candidate_target": target_value,
        "candidate_target_path": target_path_value,
        "candidate_digest": digest,
        "venv_cfg_digest": _digest_file(root / "pyvenv.cfg"),
    }


def _digest_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse(metadata: os.stat_result) -> bool:
    # Windows exposes reparse points through st_file_attributes; POSIX uses
    # the symlink bit. Both checks are part of the trust boundary.
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )


def _windows_origin_directory(directory: Path) -> Path:
    root = get_user_root(ensure_exists=True).resolve(strict=True)
    trusted = root / _ORIGIN_DIRECTORY
    try:
        metadata = os.lstat(trusted)
    except FileNotFoundError:
        trusted.mkdir(mode=0o700)
        metadata = os.lstat(trusted)
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(metadata):
        raise OSError(f"unsafe venv origin directory: {trusted}")
    if trusted.resolve(strict=True) != directory.resolve(strict=True):
        raise OSError(f"unexpected venv origin directory: {directory}")
    return trusted


def _windows_target(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode) or _is_reparse(metadata):
        raise OSError(f"unsafe venv origin target: {path}")


def _stage_bytes_windows(destination: Path, content: str | bytes) -> OriginStage:
    directory = _windows_origin_directory(destination.parent)
    _windows_target(destination)
    staged = directory / f".{destination.name}.{uuid.uuid4().hex}.prepare"
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8") if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        return OriginStage(
            directory=directory, staged_name=staged.name, final_name=destination.name
        )
    except BaseException:
        with contextlib.suppress(OSError):
            staged.unlink()
        raise


def _publish_origin_windows(stage: OriginStage) -> None:
    directory = _windows_origin_directory(stage.directory)
    staged = directory / stage.staged_name
    final = directory / stage.final_name
    _windows_target(staged)
    _windows_target(final)
    os.replace(staged, final)
    _windows_target(final)


def _discard_origin_stage_windows(stage: OriginStage) -> None:
    directory = _windows_origin_directory(stage.directory)
    with contextlib.suppress(FileNotFoundError):
        (directory / stage.staged_name).unlink()


def _opened_regular_windows(path: Path) -> BinaryIO:
    _windows_target(path)
    stream = path.open("rb")
    metadata = os.fstat(stream.fileno())
    if not stat.S_ISREG(metadata.st_mode) or _is_reparse(metadata):
        stream.close()
        raise OSError(f"unsafe venv origin record: {path}")
    return stream


def _remove_origin_windows(path: Path) -> None:
    _windows_origin_directory(path.parent)
    _windows_target(path)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
