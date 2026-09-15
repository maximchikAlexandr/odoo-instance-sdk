#!/usr/bin/env python3
"""Reject manually maintained production Python files over 1000 physical lines."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "odoo_instance_sdk"
MAX_LINES = 1000

EXCLUDED_PARTS = (
    "/web/",
    "/catalog_migrations/",
    "/.venv/",
    "/__pycache__/",
)


def is_excluded(path: Path) -> bool:
    text = path.as_posix()
    return any(part in text for part in EXCLUDED_PARTS)


def main() -> int:
    """Run the production line-limit gate."""
    violations: list[tuple[int, Path]] = []
    for path in sorted(SRC.rglob("*.py")):
        if is_excluded(path):
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > MAX_LINES:
            violations.append((line_count, path))
    if not violations:
        return 0
    for line_count, path in violations:
        relative = path.relative_to(ROOT)
        print(f"{relative}: {line_count} lines (limit {MAX_LINES})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
