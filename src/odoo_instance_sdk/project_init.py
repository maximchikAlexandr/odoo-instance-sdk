"""Public project initialization primitives."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.internal.project_init import (
    manifest_dict,
    register_initialized_project,
    write_project_generated_config,
)
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue


def init_project_command(
    project_path: Path,
    config: ProjectConfig,
    *,
    postgres_allocated: bool,
) -> Command[dict[str, JsonValue]]:
    """Capture one immutable init command for preview and execution."""
    from odoo_instance_sdk.commands.output import action_command

    return cast(
        "Command[dict[str, JsonValue]]",
        action_command(
            "init",
            lambda: init_project(project_path, config, postgres_allocated=postgres_allocated),
            description="Write project manifest",
            mutating=True,
        ),
    )


def init_project(
    project_path: Path,
    config: ProjectConfig,
    *,
    postgres_allocated: bool,
) -> dict[str, JsonValue]:
    """Write init artifacts and register the canonical project."""
    write_manifest(project_path, config)
    if config.postgres is not None and config.postgres.mode == "compose":
        write_project_generated_config(project_path, config)
    register_initialized_project(project_path)
    return manifest_dict(config, postgres_allocated=postgres_allocated)
