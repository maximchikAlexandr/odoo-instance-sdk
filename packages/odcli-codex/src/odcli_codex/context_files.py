"""Select bounded, Git-tracked text inside one project."""

from __future__ import annotations

from pathlib import Path

from odcli_codex.errors import CodexWorkflowError
from odcli_codex.models import ContextFile, ContextManifest
from odoo_instance_sdk.exceptions import GitWorkflowError
from odoo_instance_sdk.resources.git_runtime import listed_files

_SECRET_NAME = (
    ".env",
    ".pem",
    ".key",
    ".p12",
)
_SECRET_TEXT = ("BEGIN PRIVATE KEY", "AKIA", "api_key=", "token=")


def select_context(
    project_root: Path,
    selections: tuple[str, ...],
    *,
    limit_bytes: int,
) -> ContextManifest:
    """Return tracked text files or fail before any model call."""
    root = project_root.resolve()
    chosen: list[ContextFile] = []
    total = 0
    for raw in selections:
        for relative in _tracked_text(root, raw):
            path = root / relative
            content = path.read_bytes()
            if b"\0" in content[:8192]:
                raise CodexWorkflowError(f"context file is binary: {relative}")
            text = content.decode("utf-8", errors="replace")
            if _secret_name(path) or any(marker in text for marker in _SECRET_TEXT):
                raise CodexWorkflowError(f"context file looks secret and was not sent: {relative}")
            size = len(content)
            if total + size > limit_bytes:
                raise CodexWorkflowError(
                    f"context exceeds {limit_bytes} bytes at {relative} ({total + size} bytes)"
                )
            total += size
            chosen.append(ContextFile(path=relative, byte_length=size))
    return ContextManifest(
        files=tuple(chosen),
        total_bytes=total,
        limit_bytes=limit_bytes,
    )


def context_payload(project_root: Path, manifest: ContextManifest) -> str:
    """Join selected file text for a prompt. Call only after ``select_context``."""
    parts: list[str] = []
    root = project_root.resolve()
    for item in manifest.files:
        parts.append(f"--- {item.path}\n{(root / item.path).read_text(encoding='utf-8')}")
    return "\n".join(parts)


def _tracked_text(root: Path, raw: str) -> tuple[str, ...]:
    requested = Path(raw)
    candidate = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise CodexWorkflowError(f"context is outside the project: {raw}") from exc
    if not candidate.exists():
        raise CodexWorkflowError(f"context path does not exist: {raw}")
    listed = _git_files(root, relative.as_posix())
    if candidate.is_file() and relative.as_posix() not in listed:
        raise CodexWorkflowError(f"context is not a tracked text file: {relative.as_posix()}")
    if candidate.is_dir() and not listed:
        raise CodexWorkflowError(
            f"context directory has no tracked text files: {relative.as_posix()}"
        )
    return listed


def _git_files(root: Path, relative: str) -> tuple[str, ...]:
    try:
        return listed_files(root, relative)
    except GitWorkflowError as exc:
        raise CodexWorkflowError(str(exc)) from exc


def _secret_name(path: Path) -> bool:
    name = path.name.casefold()
    if name == ".env" or name.startswith((".env.", "credentials", "id_rsa")):
        return True
    return name.endswith(_SECRET_NAME)
