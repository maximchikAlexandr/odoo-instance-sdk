from __future__ import annotations

import contextlib
import errno
import os
import stat
import tempfile
import uuid
from pathlib import Path

from odoo_instance_sdk.project import ProjectConfig

_MANIFEST_DIR = ".odcli"
_MANIFEST_FILE = "project.toml"
_PROJECT_ENV_IGNORE = ".odcli/.env"

_SECRET_KEYS = frozenset(
    {
        "admin_passwd",
        "master_password",
        "master_pwd",
        "db_password",
        "password",
        "secret",
        "token",
        "api_key",
    }
)


def manifest_path(project_path: str | Path) -> Path:
    return Path(project_path) / _MANIFEST_DIR / _MANIFEST_FILE


def write_manifest(project_path: str | Path, config: ProjectConfig) -> Path:
    ensure_project_ignore(project_path)
    dest = manifest_path(project_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = config.to_manifest()
    assert_no_secrets(content)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp", prefix=_MANIFEST_FILE)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, dest)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return dest


def ensure_project_ignore(project_path: str | Path) -> Path:
    """Ensure init's project-local dotenv is explicitly ignored by Git."""
    root = Path(project_path).resolve(strict=True)
    ignore = root / ".gitignore"
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
    root_fd = os.open(root, directory_flags)
    temporary_name: str | None = None
    try:
        root_stat = os.fstat(root_fd)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise OSError(errno.ENOTDIR, f"project root is not a directory: {root}")
        content, target_stat = _read_regular_ignore(root_fd, ignore)
        if _PROJECT_ENV_IGNORE in content.splitlines():
            return ignore
        prefix = "" if not content or content.endswith("\n") else "\n"
        updated = f"{content}{prefix}{_PROJECT_ENV_IGNORE}\n".encode()

        temporary_name = _write_ignore_temp(root_fd, updated)
        _assert_ignore_unchanged(root_fd, target_stat, ignore)
        os.replace(temporary_name, ".gitignore", src_dir_fd=root_fd, dst_dir_fd=root_fd)
        temporary_name = None
        replaced_stat = os.stat(".gitignore", dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISREG(replaced_stat.st_mode):
            raise OSError(errno.ELOOP, f"refusing non-regular .gitignore target: {ignore}")
        return ignore
    finally:
        if temporary_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary_name, dir_fd=root_fd)
        os.close(root_fd)


def _read_regular_ignore(root_fd: int, display_path: Path) -> tuple[str, os.stat_result | None]:
    """Read the directory entry itself, never a symlink target."""
    try:
        target_stat = os.stat(".gitignore", dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return "", None
    _require_regular(target_stat, display_path)
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd = os.open(".gitignore", flags, dir_fd=root_fd)
    try:
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode) or (
            opened_stat.st_dev,
            opened_stat.st_ino,
        ) != (target_stat.st_dev, target_stat.st_ino):
            raise OSError(
                errno.EAGAIN, f".gitignore changed while it was being read: {display_path}"
            )
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            try:
                return stream.read().decode("utf-8"), target_stat
            except UnicodeDecodeError as exc:
                raise OSError(errno.EILSEQ, f"invalid UTF-8 in {display_path}") from exc
    finally:
        if fd != -1:
            os.close(fd)


def _write_ignore_temp(root_fd: int, content: bytes) -> str:
    """Create a regular temporary file under the already-open project root."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(10):
        name = f".gitignore.{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(name, flags, 0o600, dir_fd=root_fd)
        except FileExistsError:
            continue
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError(errno.EINVAL, f"temporary .gitignore target is not regular: {name}")
            with os.fdopen(fd, "wb") as stream:
                fd = -1
                stream.write(content)
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
            return name
        finally:
            if fd != -1:
                os.close(fd)
    raise OSError(errno.EEXIST, "could not allocate a temporary .gitignore file")


def _assert_ignore_unchanged(
    root_fd: int, expected: os.stat_result | None, display_path: Path
) -> None:
    """Reject check/write races before the atomic directory-relative replace."""
    try:
        current = os.stat(".gitignore", dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        current = None
    if expected is None:
        if current is not None:
            raise OSError(
                errno.EAGAIN, f".gitignore appeared while it was being updated: {display_path}"
            )
        return
    if current is None or (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise OSError(
            errno.EAGAIN, f".gitignore changed while it was being updated: {display_path}"
        )


def _require_regular(metadata: os.stat_result, display_path: Path) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(
            errno.ELOOP, f"refusing symlink/non-regular .gitignore target: {display_path}"
        )


def assert_no_secrets(content: str) -> None:
    lowered = content.lower()
    for key in _SECRET_KEYS:
        if key in lowered:
            raise ValueError(f"Refusing to write secret-like key {key!r} to project manifest")
