"""VS Code launch command shared by dry-run and apply."""

from __future__ import annotations

from pathlib import Path

from odoo_instance_sdk.commands.output import action_command
from odoo_instance_sdk.execution import Command, JsonValue
from odoo_instance_sdk.internal.vscode_jsonc import apply_profile_change, plan_profile_change


def launch_generate_command(
    profile: dict[str, JsonValue],
    launch_path: Path,
    owner_kind: str,
    owner_id: str,
    write_file: bool,
) -> Command[dict[str, JsonValue]]:
    """Plan one launch.json edit and apply that same captured edit."""
    change = plan_profile_change(launch_path, profile, owner_kind, owner_id) if write_file else None

    def operation() -> dict[str, JsonValue]:
        if change is None:
            return {"profile": profile}
        written = apply_profile_change(change)
        return {
            "profile": profile,
            "written": str(written),
            "change": change.kind,
            "fields": list(change.fields),
        }

    details: JsonValue = (
        {"change": change.kind, "fields": list(change.fields)} if change is not None else None
    )
    return action_command(
        "vscode.generate",
        operation,
        description="Generate VS Code launch configuration",
        mutating=write_file,
        details=details,
    )
