"""Parse and atomically edit logical breakpoints."""

from __future__ import annotations

from pathlib import Path

from odoo_instance_sdk.exceptions import DebugError

BreakpointKey = tuple[str, int]


def breakpoint_identity(text: str, cwd: Path) -> tuple[Path, int, str | None]:
    """Parse ``FILE:LINE`` or ``FILE:LINE:CONDITION`` without touching the file."""
    parts = text.split(":", 2)
    if len(parts) < 2 or not parts[0]:
        raise DebugError("breakpoint-invalid", f"invalid breakpoint {text!r}")
    raw_path, raw_line = parts[0], parts[1]
    condition = parts[2] if len(parts) == 3 and parts[2] else None
    if not raw_line.isdigit() or int(raw_line) <= 0:
        message = f"breakpoint line must be a positive integer in {text!r}"
        raise DebugError("breakpoint-invalid", message)
    path = Path(raw_path)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve(), int(raw_line), condition


def parse_breakpoint(text: str, cwd: Path) -> tuple[Path, int, str | None]:
    """Parse a breakpoint and require the source file to exist."""
    resolved, line, condition = breakpoint_identity(text, cwd)
    if not resolved.is_file():
        raise DebugError(
            "breakpoint-invalid",
            f"breakpoint file does not exist: {resolved}",
            hint="Pass a path relative to the environment runtime directory",
        )
    return resolved, line, condition


def parse_breakpoints(
    texts: tuple[str, ...], cwd: Path
) -> tuple[tuple[Path, int, str | None], ...]:
    """Validate every breakpoint before the caller applies any of them."""
    return tuple(parse_breakpoint(text, cwd) for text in texts)
