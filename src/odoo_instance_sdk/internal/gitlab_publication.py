"""Bounded helpers for GitLab merge-request publication."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class FrozenImage:
    """Validated upload bytes retained independently of the source path."""

    filename: str
    digest: str
    content: bytes


class FrozenDescription(str):
    """Validated text retaining the identity of its opened descriptor.

    This remains a ``str`` for compatibility with the public helper while
    carrying the descriptor snapshot needed by the publication boundary.
    """

    device: int
    inode: int
    mtime_ns: int
    size: int
    text: str
    __slots__ = ("device", "inode", "mtime_ns", "size", "text")
    __setattr__ = None  # type: ignore[misc,assignment]

    def __new__(
        cls,
        text: str,
        size: int,
        device: int,
        inode: int,
        mtime_ns: int,
    ) -> FrozenDescription:
        value = super().__new__(cls, text)
        object.__setattr__(value, "device", device)
        object.__setattr__(value, "inode", inode)
        object.__setattr__(value, "mtime_ns", mtime_ns)
        object.__setattr__(value, "size", size)
        object.__setattr__(value, "text", text)
        return value


def _read_description_descriptor(description: Path) -> tuple[bytes, os.stat_result]:
    """Read bounded bytes and identity from one descriptor."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0:
        raise GitLabPublicationError("owner-safe description reads are unavailable")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            description,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
        info = os.fstat(descriptor)
    except OSError as exc:
        raise GitLabPublicationError("merge-request description file is unavailable") from exc
    try:
        if not stat.S_ISREG(info.st_mode):
            raise GitLabPublicationError("merge-request description file must be regular")
        if info.st_size > MAX_DESCRIPTION_BYTES:
            raise GitLabPublicationError("merge-request description file is too large")
        content = bytearray()
        while len(content) < info.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, info.st_size - len(content)))
            if not chunk:
                raise GitLabPublicationError("merge-request description changed after planning")
            content.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(content) != info.st_size
            or after.st_dev != info.st_dev
            or after.st_ino != info.st_ino
            or after.st_size != info.st_size
            or after.st_mtime_ns != info.st_mtime_ns
        ):
            raise GitLabPublicationError("merge-request description changed after planning")
    except OSError as exc:
        raise GitLabPublicationError("merge-request description file is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return bytes(content), info


def read_description_file(path: Path | str) -> FrozenDescription:
    """Read one bounded UTF-8 description from one no-follow descriptor."""
    description = Path(path).expanduser()
    content, info = _read_description_descriptor(description)
    try:
        text = content.decode("utf-8")
    except UnicodeError as exc:
        raise GitLabPublicationError("merge-request description file is not valid UTF-8") from exc
    return FrozenDescription(text, info.st_size, info.st_dev, info.st_ino, info.st_mtime_ns)


def image_identity(path: Path | str) -> FrozenImage:
    """Validate once, then freeze exact image bytes for the later upload."""
    image = Path(path).expanduser()
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0:
        raise GitLabPublicationError("owner-safe image reads are unavailable")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            image,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
        info = os.fstat(descriptor)
    except OSError as exc:
        raise GitLabPublicationError("merge-request image is unavailable") from exc
    try:
        if not stat.S_ISREG(info.st_mode):
            raise GitLabPublicationError("merge-request image must be a regular file")
        if image.suffix.casefold() not in _IMAGE_SUFFIXES:
            raise GitLabPublicationError("merge-request image format is unsupported")
        if info.st_size <= 0 or info.st_size > MAX_IMAGE_BYTES:
            raise GitLabPublicationError("merge-request image is empty or too large")
        content = bytearray()
        while len(content) < info.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, info.st_size - len(content)))
            if not chunk:
                raise GitLabPublicationError("merge-request image changed after planning")
            content.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(content) != info.st_size
            or after.st_dev != info.st_dev
            or after.st_ino != info.st_ino
            or after.st_size != info.st_size
            or after.st_mtime_ns != info.st_mtime_ns
        ):
            raise GitLabPublicationError("merge-request image changed after planning")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    digest = hashlib.sha256(content).hexdigest()
    return FrozenImage(filename=image.name, digest=digest, content=bytes(content))


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
    "FrozenDescription",
    "FrozenImage",
    "existing_owned_image",
    "image_identity",
    "read_description_file",
    "replace_owned_image",
    "repository_project_path",
    "revalidate_file",
]
