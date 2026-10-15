"""Bounded helpers for GitLab merge-request publication."""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit

from odoo_instance_sdk.exceptions import GitLabPublicationError

MAX_DESCRIPTION_BYTES = 1_048_576
MAX_IMAGE_BYTES = 10 * 1_048_576
IMAGE_MARKER = "odcli:image"
_IMAGE_BLOCK = re.compile(
    r"<!-- odcli:image sha256=([0-9a-f]{64}) -->\n"
    r"(?P<link>[^\n]+)\n"
    r"<!-- /odcli:image -->",
    re.IGNORECASE,
)
_IMAGE_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})


def read_description_file(path: Path | str) -> str:
    """Read one regular, non-symlink UTF-8 description within the bound."""
    description = Path(path).expanduser()
    try:
        info = description.stat()
    except OSError as exc:
        raise GitLabPublicationError("merge-request description file is unavailable") from exc
    if description.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise GitLabPublicationError("merge-request description file must be regular")
    if info.st_size > MAX_DESCRIPTION_BYTES:
        raise GitLabPublicationError("merge-request description file is too large")
    try:
        return description.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GitLabPublicationError("merge-request description file is not valid UTF-8") from exc


def image_identity(path: Path | str) -> tuple[Path, str, int, int, int, int]:
    """Validate and fingerprint a bounded regular image without following links."""
    image = Path(path).expanduser()
    try:
        info = image.stat()
    except OSError as exc:
        raise GitLabPublicationError("merge-request image is unavailable") from exc
    if image.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise GitLabPublicationError("merge-request image must be a regular file")
    if image.suffix.casefold() not in _IMAGE_SUFFIXES:
        raise GitLabPublicationError("merge-request image format is unsupported")
    if info.st_size <= 0 or info.st_size > MAX_IMAGE_BYTES:
        raise GitLabPublicationError("merge-request image is empty or too large")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    return image, digest, info.st_size, info.st_dev, info.st_ino, info.st_mtime_ns


def replace_owned_image(description: str, *, digest: str, markdown: str) -> str:
    """Replace exactly the owned image block while retaining user Markdown."""
    body = _IMAGE_BLOCK.sub("", description).rstrip()
    block = f"<!-- {IMAGE_MARKER} sha256={digest} -->\n{markdown}\n<!-- /{IMAGE_MARKER} -->"
    return f"{body}\n\n{block}" if body else block


def existing_owned_image(description: str) -> tuple[str, str] | None:
    match = _IMAGE_BLOCK.search(description)
    if match is None:
        return None
    return match.group(1).lower(), match.group("link")


def repository_project_path(repository_url: str) -> tuple[str, str]:
    """Return the exact HTTPS host and project path from a GitLab remote."""
    parsed = urlsplit(repository_url)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or not parsed.path
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GitLabPublicationError("GitLab repository URL must be HTTPS")
    project = parsed.path.strip("/")
    if project.endswith(".git"):
        project = project[:-4]
    if not project or "//" in project or any(part in {".", ".."} for part in project.split("/")):
        raise GitLabPublicationError("GitLab project path is invalid")
    host = parsed.hostname.casefold()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return host, project


def revalidate_file(
    path: Path,
    *,
    size: int,
    device: int,
    inode: int,
    mtime_ns: int,
) -> None:
    """Reject a local input that changed after command construction."""
    try:
        info = path.stat()
    except OSError as exc:
        raise GitLabPublicationError("merge-request input changed after planning") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size != size
        or info.st_dev != device
        or info.st_ino != inode
        or info.st_mtime_ns != mtime_ns
    ):
        raise GitLabPublicationError("merge-request input changed after planning")


__all__ = [
    "IMAGE_MARKER",
    "MAX_DESCRIPTION_BYTES",
    "MAX_IMAGE_BYTES",
    "existing_owned_image",
    "image_identity",
    "read_description_file",
    "replace_owned_image",
    "repository_project_path",
    "revalidate_file",
]
