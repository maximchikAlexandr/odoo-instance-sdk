from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote

_FALLBACK_FILENAME = "odoo_backup.zip"


def open_directory_path_without_symlinks(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", -1) | getattr(os, "O_NOFOLLOW", -1)
    if not path.is_absolute() or flags < 0 or os.open not in os.supports_dir_fd:
        raise OSError("filestore cleanup requires descriptor-relative directory operations")
    fd = os.open(os.sep, flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
    except BaseException:
        os.close(fd)
        raise
    return fd


def extract_server_filename(content_disposition: str | None) -> str | None:
    if content_disposition is None:
        return None
    match_star = re.search(
        r"filename\*\s*=\s*(?:[\w-]+'')?([^;\s]+)",
        content_disposition,
        re.IGNORECASE,
    )
    if match_star:
        return unquote(match_star.group(1))
    match_plain = re.search(
        r'filename\s*=\s*(?:"([^"]+)"|([^;\s]+))',
        content_disposition,
        re.IGNORECASE,
    )
    if match_plain:
        return match_plain.group(1) or match_plain.group(2)
    return None


def _is_safe_filename(name: str) -> bool:
    return "/" not in name and "\\" not in name and ".." not in name


def make_download_filename(backup_id: str, server_name: str | None) -> str:
    """Build '<backup_id>_<safe-content-disposition-filename>'."""
    name = server_name or _FALLBACK_FILENAME
    if not _is_safe_filename(name):
        name = _FALLBACK_FILENAME
    return f"{backup_id}_{name}"


def ensure_destination(base_dir: Path, filename: str) -> Path:
    base = base_dir.resolve()
    dest = (base / filename).resolve()
    if not dest.is_relative_to(base):
        raise ValueError(f"Filename {filename} would escape backup directory")
    return dest
