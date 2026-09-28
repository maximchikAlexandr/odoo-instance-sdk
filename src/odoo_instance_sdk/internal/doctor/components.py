"""Applied-settings component helpers used by doctor manifest checks."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.applied_settings import (
    SettingsValue,
    decode_applied_settings,
    encode_applied_settings,
)


def python_artifact_available(path: Path, owned: bool) -> bool:
    candidate = (
        path / ("Scripts/python.exe" if os.name == "nt" else "bin/python") if owned else path
    )
    try:
        return candidate.is_file() and os.access(candidate, os.R_OK | os.X_OK)
    except OSError:
        return False


def component_from_codec(
    *,
    python: Mapping[str, JsonValue] | None = None,
    dependencies: SettingsValue | None = None,
    managed_config: Mapping[str, JsonValue] | None = None,
    addons: tuple[str, ...] | None = None,
) -> dict[str, JsonValue]:
    document = decode_applied_settings(
        encode_applied_settings(
            python=python,
            dependencies=dependencies,
            managed_config=managed_config,
            addons=addons,
        )
    )
    components = document["components"]
    return dict(components) if isinstance(components, dict) else {}


def is_known(value: JsonValue) -> bool:
    return isinstance(value, dict) and value.get("status") == "known"


def paired_component(source: JsonValue, artifact: JsonValue) -> JsonValue | None:
    return artifact if is_known(source) and is_known(artifact) else None
