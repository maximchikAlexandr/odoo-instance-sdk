"""Load and merge scaffold settings without touching the filesystem."""

from __future__ import annotations

import json
import keyword
import math
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from odoo_instance_sdk.exceptions import PlanJsonValue, ScaffoldError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.paths import get_user_config_path
from odoo_instance_sdk.models.scaffold import (
    BASELINE_COMPONENTS,
    OPTIONAL_COMPONENTS,
    ManifestValue,
    ScaffoldConfig,
    UserConfig,
)

_MODULE_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_MANIFEST_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_REQUIRED_MANIFEST = frozenset({"name", "author", "license"})
_RESERVED_MANIFEST = frozenset({"data", "demo"})
_SECRET_PARTS = ("password", "secret", "token", "credential", "api_key", "private_key")
_JINJA_MARKERS = ("{{", "{%", "{#")
_BUILTIN_MANIFEST: dict[str, ManifestValue] = {
    "license": "LGPL-3",
    "version": "19.0.1.0.0",
    "depends": ["base"],
    "installable": True,
}
_MANIFEST_ORDER = (
    "name",
    "version",
    "summary",
    "description",
    "author",
    "website",
    "category",
    "license",
    "depends",
    "data",
    "demo",
    "installable",
    "application",
    "auto_install",
)


def user_config_path() -> Path:
    """Return the user configuration file path without creating it."""
    return get_user_config_path()


def load_user_config(path: Path | None = None) -> UserConfig:
    """Load the user scaffold file. A missing file returns empty defaults."""

    selected = path if path is not None else get_user_config_path()
    if not selected.is_file():
        return UserConfig()
    with selected.open("rb") as handle:
        loaded = tomllib.load(handle)
    if not isinstance(loaded, dict):
        raise ScaffoldError("user config must be a TOML table", code="scaffold_invalid_config")
    unknown = set(loaded) - {"scaffold"}
    if unknown:
        names = ", ".join(sorted(str(name) for name in unknown))
        raise ScaffoldError(
            f"user config only accepts [scaffold], found {names}",
            code="scaffold_invalid_config",
        )
    section = loaded.get("scaffold")
    if section is None:
        return UserConfig()
    return UserConfig(scaffold=scaffold_from_toml(section))


def scaffold_from_toml(section: JsonValue) -> ScaffoldConfig:
    """Decode one ``[scaffold]`` table."""

    if not isinstance(section, dict):
        raise ScaffoldError("scaffold section must be a table", code="scaffold_invalid_config")
    unknown = set(section) - {"include", "exclude", "omit_manifest", "manifest"}
    if unknown:
        names = ", ".join(sorted(str(name) for name in unknown))
        raise ScaffoldError(
            f"unsupported scaffold setting: {names}",
            code="scaffold_invalid_config",
        )
    manifest = section.get("manifest", {})
    if not isinstance(manifest, dict):
        raise ScaffoldError(
            "scaffold.manifest must be a table",
            code="scaffold_invalid_manifest",
        )
    decoded = {_manifest_key(str(key)): _manifest_value(value) for key, value in manifest.items()}
    return ScaffoldConfig(
        include=_component_names(_field(section, "include"), label="include"),
        exclude=_component_names(_field(section, "exclude"), label="exclude"),
        omit_manifest=_omit_names(_field(section, "omit_manifest")),
        manifest=decoded,
    )


def parse_manifest_override(raw: str) -> tuple[str, ManifestValue]:
    """Parse one ``KEY=VALUE`` manifest override."""

    if "=" not in raw:
        raise ScaffoldError(
            "manifest override must be KEY=VALUE",
            code="scaffold_invalid_manifest",
        )
    key, value = raw.split("=", 1)
    return _manifest_key(key.strip()), _parse_literal(value.strip())


def normalize_module_name(value: str) -> str:
    """Return the Odoo snake-case technical name, or fail closed."""

    spaced = re.sub(r"(?<=[^A-Z])\B([A-Z])", r" \1", value.strip())
    name = "_".join(spaced.lower().split())
    if not _MODULE_NAME.fullmatch(name) or keyword.iskeyword(name):
        raise ScaffoldError(
            f"invalid technical module name: {value!r}",
            code="scaffold_invalid_module",
            details={"module_name": value},
        )
    return name


def merge_scaffold(
    *layers: ScaffoldConfig | None,
    module_name: str,
) -> tuple[tuple[str, ...], dict[str, ManifestValue]]:
    """Apply layers from lowest to highest precedence."""

    selected = set[str]()
    manifest = dict(_BUILTIN_MANIFEST)
    for layer in layers:
        if layer is None:
            continue
        _apply_components(layer, selected)
        _apply_manifest(layer, manifest)
    if not str(manifest.get("name", "")).strip():
        manifest["name"] = _display_name(module_name)
    if not str(manifest.get("license", "")).strip():
        manifest["license"] = "LGPL-3"
    author = manifest.get("author")
    if not isinstance(author, str) or not author.strip():
        raise ScaffoldError(
            "scaffold author is required in user config, project config, or --manifest",
            code="scaffold_missing_author",
        )
    manifest["author"] = author.strip()
    components = tuple(
        component
        for component in (*BASELINE_COMPONENTS, *OPTIONAL_COMPONENTS)
        if component in BASELINE_COMPONENTS or component in selected
    )
    return components, manifest


def render_manifest(fields: Mapping[str, ManifestValue]) -> str:
    """Render a deterministic Python manifest body."""

    ordered = [key for key in _MANIFEST_ORDER if key in fields]
    ordered.extend(sorted(set(fields) - set(ordered)))
    body = ",\n".join(f"    {key!r}: {_python_literal(fields[key])}" for key in ordered)
    return "{\n" + body + ",\n}\n"


def render_scaffold_toml(config: ScaffoldConfig) -> str:
    """Render a project or user ``[scaffold]`` table."""

    lines = ["[scaffold]"]
    if config.include:
        lines.append(f"include = [{_toml_list(config.include)}]")
    if config.exclude:
        lines.append(f"exclude = [{_toml_list(config.exclude)}]")
    if config.omit_manifest:
        lines.append(f"omit_manifest = [{_toml_list(config.omit_manifest)}]")
    if config.manifest:
        lines.append("")
        lines.append("[scaffold.manifest]")
        for key in sorted(config.manifest):
            lines.append(f"{key} = {_toml_value(config.manifest[key])}")
    return "\n".join(lines) + "\n"


def _apply_components(layer: ScaffoldConfig, selected: set[str]) -> None:
    overlap = set(layer.include) & set(layer.exclude)
    if overlap:
        names = ", ".join(sorted(overlap))
        raise ScaffoldError(
            f"component cannot be included and excluded together: {names}",
            code="scaffold_component_conflict",
            details={"components": cast("list[PlanJsonValue]", sorted(overlap))},
        )
    for name in layer.include:
        _known_component(name)
        if name not in BASELINE_COMPONENTS:
            selected.add(name)
    for name in layer.exclude:
        if name in BASELINE_COMPONENTS:
            raise ScaffoldError(
                f"baseline component cannot be disabled: {name}",
                code="scaffold_baseline",
                details={"component": name},
            )
        _known_component(name)
        selected.discard(name)


def _apply_manifest(layer: ScaffoldConfig, manifest: dict[str, ManifestValue]) -> None:
    for key in layer.omit_manifest:
        _reject_omitted_key(key)
        manifest.pop(key, None)
    for key, value in layer.manifest.items():
        _reject_assigned_key(key)
        _reject_secret_key(key)
        _reject_manifest_text(value)
        manifest[key] = value


def _known_component(name: str) -> None:
    if name in BASELINE_COMPONENTS or name in OPTIONAL_COMPONENTS:
        return
    raise ScaffoldError(
        f"unknown scaffold component: {name}",
        code="scaffold_unknown_component",
        details={"component": name},
    )


def _reject_omitted_key(key: str) -> None:
    if key in _REQUIRED_MANIFEST:
        raise ScaffoldError(
            f"manifest field cannot be omitted: {key}",
            code="scaffold_required_manifest",
            details={"field": key},
        )
    _reject_assigned_key(key)


def _reject_assigned_key(key: str) -> None:
    if key in _RESERVED_MANIFEST:
        raise ScaffoldError(
            f"manifest field is derived from components: {key}",
            code="scaffold_reserved_manifest",
            details={"field": key},
        )


def _field(section: dict[str, JsonValue], key: str) -> JsonValue:
    if key not in section:
        return []
    return section[key]


def _component_names(value: JsonValue, *, label: str) -> tuple[str, ...]:
    names = _string_items(value, f"scaffold.{label} must be an array")
    if len(names) != len(set(names)):
        raise ScaffoldError(
            f"duplicate scaffold {label}",
            code="scaffold_component_conflict",
        )
    return names


def _omit_names(value: JsonValue) -> tuple[str, ...]:
    names = tuple(
        _manifest_key(item)
        for item in _string_items(value, "scaffold.omit_manifest must be an array")
    )
    if len(names) != len(set(names)):
        raise ScaffoldError(
            "duplicate scaffold omit_manifest entry",
            code="scaffold_invalid_manifest",
        )
    return names


def _string_items(value: JsonValue, message: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ScaffoldError(message, code="scaffold_invalid_config")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ScaffoldError(f"{message} of strings", code="scaffold_invalid_config")
        names.append(item)
    return tuple(names)


def _reject_manifest_text(value: ManifestValue) -> None:
    if isinstance(value, str):
        _reject_jinja(value)
        return
    if isinstance(value, list):
        for item in value:
            _reject_manifest_text(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _manifest_key(key)
            _reject_manifest_text(item)


def _manifest_key(key: str) -> str:
    if not _MANIFEST_KEY.fullmatch(key):
        raise ScaffoldError(
            f"invalid manifest field: {key!r}",
            code="scaffold_invalid_manifest",
        )
    _reject_secret_key(key)
    return key


def _reject_secret_key(key: str) -> None:
    lowered = key.lower()
    if any(part in lowered for part in _SECRET_PARTS):
        raise ScaffoldError(
            "scaffold config must not contain secrets",
            code="scaffold_secret",
            details={"field": key},
        )


def _manifest_value(value: JsonValue) -> ManifestValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        _reject_jinja(value)
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ScaffoldError(
                "manifest values must be finite",
                code="scaffold_invalid_manifest",
            )
        return value
    if isinstance(value, list):
        return [_manifest_value(item) for item in value]
    if isinstance(value, dict):
        return {_manifest_key(str(key)): _manifest_value(item) for key, item in value.items()}
    raise ScaffoldError(
        "manifest values must be strings, booleans, numbers, lists, or mappings",
        code="scaffold_invalid_manifest",
    )


def _parse_literal(raw: str) -> ManifestValue:
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw[:1] in "[{":
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ScaffoldError(
                "manifest value is not valid JSON",
                code="scaffold_invalid_manifest",
            ) from exc
        return _manifest_value(loaded)
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return _manifest_value(raw[1:-1])
    return _manifest_value(raw)


def _reject_jinja(value: str) -> None:
    if any(marker in value for marker in _JINJA_MARKERS) or "\x00" in value:
        raise ScaffoldError(
            "manifest strings cannot contain template markers",
            code="scaffold_invalid_manifest",
        )


def _display_name(module_name: str) -> str:
    return " ".join(part.capitalize() for part in module_name.split("_") if part)


def _python_literal(value: ManifestValue) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str | int):
        return repr(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_python_literal(item) for item in value) + "]"
    inner = ", ".join(f"{key!r}: {_python_literal(item)}" for key, item in value.items())
    return "{" + inner + "}"


def _toml_list(values: tuple[str, ...]) -> str:
    return ", ".join(_toml_string(value) for value in values)


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_value(value: ManifestValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    inner = ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items())
    return "{ " + inner + " }"


__all__ = [
    "load_user_config",
    "merge_scaffold",
    "normalize_module_name",
    "parse_manifest_override",
    "render_manifest",
    "render_scaffold_toml",
    "scaffold_from_toml",
]
