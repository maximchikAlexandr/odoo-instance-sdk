"""Non-failing doctor facts for the optional Odoo logging section."""

from __future__ import annotations

import tomllib
from pathlib import Path

from odoo_instance_sdk.exceptions import ConfigError, ProjectManifestNotFoundError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.project import ProjectConfig


def odoo_logging_check(project_root: Path) -> tuple[str, dict[str, JsonValue]] | None:
    """Return the logging detail and facts, or none when the manifest cannot be read."""
    try:
        config = ProjectConfig.load(project_root).odoo_logging
    except (ProjectManifestNotFoundError, ConfigError, tomllib.TOMLDecodeError):
        return None
    structured = config is not None and config.structured
    detail = "structured Odoo logging" if structured else "structured Odoo logging disabled"
    facts: dict[str, JsonValue] = {
        "structured": structured,
        "level": None if config is None else config.level,
        "handlers": [] if config is None else list(config.handlers),
    }
    return detail, facts
