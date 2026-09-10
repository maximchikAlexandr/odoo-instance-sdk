"""Private, versioned and secret-free applied-settings evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue

APPLIED_SETTINGS_VERSION = 1
type SettingsValue = (
    None
    | bool
    | int
    | float
    | str
    | bytes
    | BaseException
    | Path
    | Mapping[str, SettingsValue]
    | Sequence[SettingsValue]
)


class AppliedSettingsError(ValueError):
    """Raised when persisted applied-settings evidence is not safe or supported."""


def _canonical_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _canonical_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _safe_path(value: str | Path, *, field: str) -> str:
    projected = _secret_free(_canonical_path(value), field=field)
    if not isinstance(projected, str):
        raise AppliedSettingsError(f"{field} evidence is malformed")
    return projected


def _secret_free(value: SettingsValue, *, field: str) -> JsonValue:
    from odoo_instance_sdk.internal.proc.redaction import redacted_projection

    return redacted_projection(cast("JsonValue", value), field=field)


def _known_component(**values: JsonValue) -> dict[str, JsonValue]:
    return {"status": "known", **values}


def _unknown_component() -> dict[str, JsonValue]:
    return {"status": "unknown"}


def _normalize_python(value: Mapping[str, SettingsValue] | None) -> dict[str, JsonValue]:
    if value is None:
        return _unknown_component()
    allowed = {"selector", "path", "owned"}
    if set(value) - allowed:
        raise AppliedSettingsError("python evidence contains unsupported fields")
    selector = value.get("selector")
    path = value.get("path")
    owned = value.get("owned")
    if selector is not None and not isinstance(selector, (str, Path)):
        raise AppliedSettingsError("python selector evidence is malformed")
    if path is not None and not isinstance(path, (str, Path)):
        raise AppliedSettingsError("python path evidence is malformed")
    if not isinstance(owned, bool):
        raise AppliedSettingsError("python ownership evidence is malformed")
    selector_value = (
        _secret_free(str(selector), field="python_selector") if selector is not None else None
    )
    if selector_value is not None and not isinstance(selector_value, str):
        raise AppliedSettingsError("python selector evidence is malformed")
    return _known_component(
        selector=selector_value,
        path=_safe_path(path, field="python_path") if path is not None else None,
        owned=owned,
    )


def _normalize_dependencies(value: SettingsValue | None) -> dict[str, JsonValue]:
    if value is None:
        return _unknown_component()
    if isinstance(value, Mapping):
        entries = tuple(sorted((str(key), item) for key, item in value.items()))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        entries = tuple((str(index), item) for index, item in enumerate(value))
    else:
        raise AppliedSettingsError("dependency evidence is malformed")
    inputs: list[JsonValue] = []
    for identity, semantic_input in entries:
        projected_identity = _secret_free(identity, field="dependency_identity")
        if not isinstance(projected_identity, str):
            raise AppliedSettingsError("dependency identity evidence is malformed")
        projected = _secret_free(semantic_input, field="dependency")
        fingerprint_input: JsonValue = {"identity": projected_identity, "input": projected}
        digest = hashlib.sha256(_canonical_json(fingerprint_input).encode("utf-8")).hexdigest()
        inputs.append({"identity": projected_identity, "fingerprint": digest})
    return _known_component(inputs=inputs)


def _normalize_config(value: Mapping[str, SettingsValue] | None) -> dict[str, JsonValue]:
    if value is None:
        return _unknown_component()
    projected = _secret_free(cast("JsonValue", dict(value)), field="managed_config")
    if not isinstance(projected, dict):
        raise AppliedSettingsError("managed Odoo config evidence is malformed")
    return _known_component(values=projected)


def _normalize_addons(value: Sequence[str | Path] | None) -> dict[str, JsonValue]:
    if value is None:
        return _unknown_component()
    if isinstance(value, (str, bytes, bytearray)):
        raise AppliedSettingsError("add-on path evidence is malformed")
    canonical_paths = {_safe_path(item, field="addon_path") for item in value}
    paths: list[JsonValue] = [cast("JsonValue", path) for path in sorted(canonical_paths)]
    return _known_component(paths=paths)


def _normalize_git(value: Mapping[str, SettingsValue] | None) -> dict[str, JsonValue]:
    if value is None:
        return _unknown_component()
    allowed = {"ticket", "branch", "base"}
    if set(value) - allowed:
        raise AppliedSettingsError("Git provenance evidence is malformed")
    ticket = value.get("ticket")
    branch = value.get("branch")
    base = value.get("base")
    if not all(isinstance(item, str) for item in (ticket, branch, base)):
        raise AppliedSettingsError("Git provenance evidence is malformed")
    assert isinstance(ticket, str)
    assert isinstance(branch, str)
    assert isinstance(base, str)
    ticket_value = _secret_free(ticket, field="git_ticket")
    branch_value = _secret_free(branch, field="git_branch")
    base_value = _secret_free(base, field="git_base")
    if not all(isinstance(item, str) for item in (ticket_value, branch_value, base_value)):
        raise AppliedSettingsError("Git provenance evidence is malformed")
    return _known_component(ticket=ticket_value, branch=branch_value, base=base_value)


def _validate_python_component(component: dict[str, JsonValue]) -> None:
    if (
        not (component["selector"] is None or isinstance(component["selector"], str))
        or not (component["path"] is None or isinstance(component["path"], str))
        or not isinstance(component["owned"], bool)
    ):
        raise AppliedSettingsError("python known evidence is malformed")


def _validate_dependency_component(component: dict[str, JsonValue]) -> None:
    inputs = component["inputs"]
    if not isinstance(inputs, list):
        raise AppliedSettingsError("dependencies known evidence is malformed")
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {"identity", "fingerprint"}:
            raise AppliedSettingsError("dependencies known evidence is malformed")
        identity = item.get("identity")
        fingerprint = item.get("fingerprint")
        if not isinstance(identity, str) or not isinstance(fingerprint, str):
            raise AppliedSettingsError("dependencies known evidence is malformed")
        if len(fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            raise AppliedSettingsError("dependencies known evidence is malformed")


def _validate_config_component(component: dict[str, JsonValue]) -> None:
    if not isinstance(component["values"], dict):
        raise AppliedSettingsError("odoo known evidence is malformed")


def _validate_addons_component(component: dict[str, JsonValue]) -> None:
    paths = component["paths"]
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise AppliedSettingsError("addons known evidence is malformed")


def _validate_git_component(component: dict[str, JsonValue]) -> None:
    if not all(isinstance(component[field], str) for field in ("ticket", "branch", "base")):
        raise AppliedSettingsError("git known evidence is malformed")


def _validate_component(value: JsonValue, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or value.get("status") not in {"known", "unknown"}:
        raise AppliedSettingsError(f"{name} evidence is malformed")
    if value["status"] == "unknown" and set(value) != {"status"}:
        raise AppliedSettingsError(f"{name} unknown evidence is malformed")
    if value["status"] == "known":
        fields = {
            "python": {"status", "selector", "path", "owned"},
            "dependencies": {"status", "inputs"},
            "odoo": {"status", "values"},
            "addons": {"status", "paths"},
            "git": {"status", "ticket", "branch", "base"},
        }[name]
        if set(value) != fields:
            raise AppliedSettingsError(f"{name} known evidence is malformed")
        component = value
        validators = {
            "python": _validate_python_component,
            "dependencies": _validate_dependency_component,
            "odoo": _validate_config_component,
            "addons": _validate_addons_component,
            "git": _validate_git_component,
        }
        validators[name](component)
    return value


@dataclass(frozen=True, slots=True)
class AppliedSettingsCodec:
    """Frozen codec for the one persisted applied-settings document."""

    version: int = APPLIED_SETTINGS_VERSION

    def encode(
        self,
        *,
        python: Mapping[str, SettingsValue] | None = None,
        dependencies: SettingsValue | None = None,
        managed_config: Mapping[str, SettingsValue] | None = None,
        addons: Sequence[str | Path] | None = None,
        git: Mapping[str, SettingsValue] | None = None,
    ) -> str:
        if self.version != APPLIED_SETTINGS_VERSION:
            raise AppliedSettingsError("unsupported applied-settings version")
        components: dict[str, JsonValue] = {
            "python": _normalize_python(python),
            "dependencies": _normalize_dependencies(dependencies),
            "odoo": _normalize_config(managed_config),
            "addons": _normalize_addons(addons),
            "git": _normalize_git(git),
        }
        document: JsonValue = {
            "version": self.version,
            "components": components,
        }
        return _canonical_json(document)

    def decode(self, raw: str) -> Mapping[str, JsonValue]:
        try:
            document = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AppliedSettingsError("malformed applied-settings document") from exc
        if (
            not isinstance(document, dict)
            or type(document.get("version")) is not int
            or document.get("version") != self.version
        ):
            raise AppliedSettingsError("unsupported applied-settings version")
        components = document.get("components")
        expected = {"python", "dependencies", "odoo", "addons", "git"}
        if not isinstance(components, dict) or set(components) != expected:
            raise AppliedSettingsError("malformed applied-settings components")
        if _secret_free(document, field="applied_settings") != document:
            raise AppliedSettingsError("secret-bearing applied-settings document")
        checked: dict[str, JsonValue] = {
            name: cast("JsonValue", _validate_component(components[name], name))
            for name in sorted(expected)
        }
        normalized: JsonValue = {"version": self.version, "components": checked}
        return cast("Mapping[str, JsonValue]", normalized)

    def unknown(self) -> str:
        return self.encode()


CODEC = AppliedSettingsCodec()
LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON = CODEC.unknown()


def encode_applied_settings(
    *,
    python: Mapping[str, SettingsValue] | None = None,
    dependencies: SettingsValue | None = None,
    managed_config: Mapping[str, SettingsValue] | None = None,
    addons: Sequence[str | Path] | None = None,
    git: Mapping[str, SettingsValue] | None = None,
) -> str:
    return CODEC.encode(
        python=python,
        dependencies=dependencies,
        managed_config=managed_config,
        addons=addons,
        git=git,
    )


def decode_applied_settings(raw: str) -> Mapping[str, JsonValue]:
    return CODEC.decode(raw)


__all__ = [
    "APPLIED_SETTINGS_VERSION",
    "LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON",
    "AppliedSettingsCodec",
    "AppliedSettingsError",
    "decode_applied_settings",
    "encode_applied_settings",
]
