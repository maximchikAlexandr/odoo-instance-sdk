"""Bound and redact debug snapshot fragments."""

from __future__ import annotations

import re
from pathlib import Path

from odoo_instance_sdk.internal.debug.limits import (
    MAX_CHILDREN,
    MAX_LOCALS,
    MAX_OUTPUT_BYTES,
    MAX_SOURCE_LINES,
    MAX_STACK,
    MAX_STRING,
)
from odoo_instance_sdk.internal.proc.redaction import REDACTION_MARKER, is_secret_key_name
from odoo_instance_sdk.models.debug import (
    DebugChild,
    DebugFrame,
    DebugLocal,
    DebugOutput,
    DebugSource,
)

_SECRET_TEXT = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|credential|private[_-]?key)\s*[:=]\s*\S+"
)
_SECRET_PATH = re.compile(r"(?i)(^|[\\/])(\.env|odoo\.conf|.*secret.*|.*credential.*)$")


def redact_text(name: str, value: str) -> str:
    """Redact credential-shaped names and assignments, then cap the string."""
    if is_secret_key_name(name) or _SECRET_TEXT.search(value):
        return REDACTION_MARKER
    if len(value) > MAX_STRING:
        return value[:MAX_STRING]
    return value


def display_path(path: str, root: Path) -> str:
    """Return an environment-relative path, never a secret or config path."""
    resolved = Path(path)
    if _SECRET_PATH.search(resolved.name) or _SECRET_PATH.search(str(resolved)):
        return REDACTION_MARKER
    try:
        return str(resolved.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(resolved)


def bound_locals(rows: tuple[tuple[str, str, str], ...]) -> tuple[tuple[DebugLocal, ...], bool]:
    """Keep at most the fixed local count, redacting each value."""
    truncated = len(rows) > MAX_LOCALS
    locals_ = tuple(
        DebugLocal(name=name, value=redact_text(name, value), type=type_name)
        for name, value, type_name in rows[:MAX_LOCALS]
    )
    return locals_, truncated


def bound_stack(frames: tuple[DebugFrame, ...]) -> tuple[tuple[DebugFrame, ...], bool]:
    """Keep the top frames up to the fixed stack limit."""
    return frames[:MAX_STACK], len(frames) > MAX_STACK


def source_window(path: Path, line: int) -> DebugSource:
    """Read at most 15 lines centered on ``line``."""
    try:
        rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return DebugSource(start_line=line, lines=(), truncated=True)
    start = max(1, line - 7)
    end = min(len(rows), start + MAX_SOURCE_LINES - 1)
    start = max(1, end - MAX_SOURCE_LINES + 1)
    selected = tuple(redact_text("", rows[index]) for index in range(start - 1, end))
    return DebugSource(
        start_line=start,
        lines=selected,
        truncated=len(rows) > MAX_SOURCE_LINES and (start > 1 or end < len(rows)),
    )


def bound_output(stdout: str, stderr: str) -> DebugOutput:
    """Keep a combined stdout and stderr tail of at most 64 KiB."""
    combined = len(stdout.encode()) + len(stderr.encode())
    truncated = combined > MAX_OUTPUT_BYTES
    budget = MAX_OUTPUT_BYTES
    out = _tail(stdout, budget // 2)
    err = _tail(stderr, budget // 2)
    return DebugOutput(
        stdout=tuple(out.splitlines()),
        stderr=tuple(err.splitlines()),
        truncated=truncated,
    )


def bound_children(
    rows: tuple[tuple[str, str, str, tuple[DebugChild, ...]], ...],
) -> tuple[tuple[DebugChild, ...], bool]:
    """Cap one expansion level at the fixed child count."""
    truncated = len(rows) > MAX_CHILDREN
    children = tuple(
        DebugChild(name=name, value=redact_text(name, value), type=type_name, children=nested)
        for name, value, type_name, nested in rows[:MAX_CHILDREN]
    )
    return children, truncated


def _tail(text: str, budget: int) -> str:
    encoded = text.encode()
    if len(encoded) <= budget:
        return text
    return encoded[-budget:].decode(errors="ignore")
