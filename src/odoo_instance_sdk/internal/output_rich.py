"""Pure Rich projection for immutable CLI execution documents."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from odoo_instance_sdk.internal.cli_format import _rich_plan_metadata, _rich_semantic_step_lines

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue


def rich_plan_projection(result: JsonValue, *, command: str, warnings: tuple[str, ...] = ()) -> str:
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    lines = [f"Plan: {command}"]
    plans = _nested_plans(result)
    semantic = _semantic_plan_projection(result, command=command, document_warnings=warnings)
    if semantic is not None and len(plans) == 1 and plans[0] is result:
        return semantic
    number = 0
    for plan in plans:
        steps = plan.get("steps")
        if isinstance(steps, list):
            for item in steps:
                if isinstance(item, dict):
                    number += 1
                    lines.extend(_rich_step_lines(number, item))
        lines.extend(_rich_plan_metadata(plan, warnings if plan is result else ()))
    return "\n".join(lines)


def _nested_plans(value: JsonValue) -> list[dict[str, JsonValue]]:
    plans: list[dict[str, JsonValue]] = []
    if isinstance(value, dict):
        if isinstance(value.get("steps"), list):
            plans.append(value)
        else:
            for child in value.values():
                plans.extend(_nested_plans(child))
    elif isinstance(value, list):
        for child in value:
            plans.extend(_nested_plans(child))
    return plans


def _semantic_plan_projection(
    result: dict[str, JsonValue],
    *,
    command: str,
    document_warnings: tuple[str, ...] = (),
) -> str | None:
    observations = result.get("observations")
    if not isinstance(observations, list):
        return None
    semantic = next(
        (
            item
            for item in observations
            if isinstance(item, dict) and item.get("kind") == "semantic"
        ),
        None,
    )
    if not isinstance(semantic, dict):
        return None
    lines = [f"Plan: {command}", f"Goal: {semantic.get('goal', '')}"]
    for field, label in (("targets", "Targets"), ("mutations", "Mutations")):
        values = semantic.get(field)
        if isinstance(values, list) and values:
            lines.append(f"{label}:")
            lines.extend(f"  - {value}" for value in values)
    preconditions = semantic.get("preconditions")
    if isinstance(preconditions, list) and preconditions:
        lines.append("Preconditions:")
        for item in preconditions:
            if isinstance(item, dict):
                lines.append(
                    f"  - {item.get('name', 'precondition')}: "
                    f"{item.get('status', 'unknown')} — {item.get('detail', '')}"
                )
    sessions = semantic.get("active_sessions")
    if isinstance(sessions, list) and sessions:
        lines.extend(_rich_active_session_lines(sessions))
    warnings = semantic.get("warnings")
    warning_values = list(warnings) if isinstance(warnings, list) else []
    warning_values.extend(warning for warning in document_warnings if warning not in warning_values)
    if warning_values:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in warning_values)
    lines.extend(_rich_semantic_step_lines(result))
    return "\n".join(lines)


def _rich_active_session_lines(sessions: list[JsonValue]) -> list[str]:
    lines = ["Active sessions:"]
    for session in sessions:
        if isinstance(session, dict):
            identity = ", ".join(
                f"{key}={session[key]}"
                for key in ("pid", "user", "client", "application")
                if session.get(key) is not None
            )
            lines.append(f"  - {identity}")
    return lines


def _rich_step_lines(number: int, item: dict[str, JsonValue]) -> list[str]:
    kind = str(item.get("kind", "step"))
    step_id = str(item.get("step_id", "<unnamed>"))
    flags = tuple(
        name
        for name, enabled in (
            ("mutating", item.get("mutating")),
            ("interactive", item.get("interactive")),
            ("long-running", item.get("long_running")),
            ("read-only", item.get("read_only")),
        )
        if enabled is True
    )
    classification = ", ".join(flags) or "bounded"
    lines = [
        f"{number}. {kind} {step_id} [{classification}]",
        f"   classification: {classification}",
    ]
    if kind == "process":
        return lines + _rich_process_lines(item)
    if "description" in item:
        lines.append(f"   action: {item.get('description')}")
    return lines


def _rich_process_lines(item: dict[str, JsonValue]) -> list[str]:
    lines: list[str] = []
    display = item.get("display")
    if isinstance(display, str) and display:
        lines.append(f"   command: {display}")
    argv = item.get("argv")
    if isinstance(argv, list):
        lines.append("   argv: " + json.dumps(argv, ensure_ascii=False, separators=(", ", ": ")))
    for field, label in (
        ("executable", "executable"),
        ("cwd", "cwd"),
        ("mode", "mode"),
        ("timeout", "timeout"),
    ):
        value = item.get(field)
        if value is not None:
            lines.append(f"   {label}: {value}")
    environment = item.get("environment_overrides")
    if isinstance(environment, list) and environment:
        lines.append(
            "   environment: "
            + json.dumps(environment, ensure_ascii=False, separators=(", ", ": "))
        )
    stdin = item.get("input_preview")
    if isinstance(stdin, str):
        lines.append("   stdin: |")
        lines.extend(f"     {line}" for line in (stdin.splitlines() or [""]))
    return lines
