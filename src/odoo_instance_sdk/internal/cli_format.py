from __future__ import annotations

import json
from typing import TYPE_CHECKING

from odoo_instance_sdk.internal.sanitize import sanitize_terminal_text

if TYPE_CHECKING:
    from rich.text import Text

    from odoo_instance_sdk.execution import JsonValue


def human_bytes(n: int) -> str:
    """Render a byte count using the CLI's compact binary-unit convention."""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            if unit == "B":
                return f"{n} {unit}"
            return f"{value:.1f} {unit}".rstrip("0").rstrip(".")
        value /= 1024
    return f"{value:.1f} TiB"


def _rich_semantic_step_lines(result: dict[str, JsonValue]) -> list[str]:
    """Append the safe execution description retained by the public plan."""
    steps = result.get("steps")
    if not isinstance(steps, list):
        return []

    lines = ["Execution:"]
    for item in steps:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind == "process":
            display = item.get("display")
            if isinstance(display, str):
                lines.append(
                    "  - process: " + sanitize_terminal_text(display, preserve_newlines=True)
                )
        elif kind == "action":
            description = item.get("description")
            action = item.get("action")
            value = description if isinstance(description, str) and description else action
            if isinstance(value, str) and value:
                lines.append("  - action: " + sanitize_terminal_text(value))
    return lines if len(lines) > 1 else []


def _rich_plan_metadata(
    result: dict[str, JsonValue], document_warnings: tuple[str, ...]
) -> list[str]:
    lines: list[str] = []
    observations = result.get("observations")
    if isinstance(observations, list) and observations:
        lines.append("observations:")
        lines.extend(
            "  - " + json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            for item in observations
        )
    warnings = result.get("warnings")
    warning_values = list(warnings) if isinstance(warnings, list) else []
    for warning in document_warnings:
        if warning not in warning_values:
            warning_values.append(warning)
    if warning_values:
        lines.append("warnings:")
        lines.extend(f"  - {warning}" for warning in warning_values)
    fingerprint = result.get("fingerprint")
    if isinstance(fingerprint, str) and fingerprint:
        lines.append(f"fingerprint: {fingerprint}")
    return lines


def rich_cell(value: JsonValue, *, style: str | None = None) -> Text:
    """Render dynamic Rich cell content as inert text while retaining style."""
    from rich.text import Text

    return Text(sanitize_terminal_text(str(value)), style=style or "")
