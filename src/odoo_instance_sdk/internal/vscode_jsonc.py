"""Insert or update one identity-marked VS Code profile without rewriting JSONC."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import json5

from odoo_instance_sdk.execution import JsonValue


@dataclass(frozen=True, slots=True)
class LaunchEdit:
    """One captured launch.json change shared by dry-run and apply."""

    path: Path
    original: str
    updated: str
    digest: str
    kind: str
    fields: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.original != self.updated


def plan_profile_change(
    path: Path,
    profile: dict[str, JsonValue],
    owner_kind: str,
    owner_id: str,
) -> LaunchEdit:
    """Plan a byte-stable insert, update, or no-op for one owned profile."""
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    updated, fields = _updated_text(original, profile, owner_kind, owner_id)
    return LaunchEdit(
        path=path,
        original=original,
        updated=updated,
        digest=_digest(original),
        kind=_kind(original, updated, path.exists()),
        fields=fields,
    )


def apply_profile_change(change: LaunchEdit) -> Path:
    """Write the captured text when the file still matches the planned digest."""
    current = change.path.read_text(encoding="utf-8") if change.path.exists() else ""
    if _digest(current) != change.digest:
        raise RuntimeError("launch.json changed after the edit was planned")
    if not change.changed:
        return change.path
    change.path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=change.path.parent, prefix=".launch-", suffix=".json"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(change.updated)
        os.replace(temporary, change.path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    return change.path


def _updated_text(
    original: str, profile: dict[str, JsonValue], owner_kind: str, owner_id: str
) -> tuple[str, tuple[str, ...]]:
    if not original:
        return _new_document(profile, owner_kind, owner_id), tuple(sorted(profile))
    try:
        parsed = json5.loads(original)
    except ValueError as exc:
        raise RuntimeError("launch.json is not valid JSONC") from exc
    configurations = parsed.get("configurations") if isinstance(parsed, dict) else None
    if type(configurations) is not list:
        raise RuntimeError("launch.json configurations must be an array")
    identity = _identity(owner_kind, owner_id)
    starts = _identity_offsets(original, identity)
    if len(starts) > 1:
        raise RuntimeError("launch.json contains more than one owned debug profile")
    block = _block(profile, owner_kind, owner_id, "    ")
    if not starts:
        return _insert(original, block), tuple(sorted(profile))
    start = original.rfind("\n", 0, starts[0])
    start = 0 if start < 0 else start + 1
    try:
        object_start, object_end = _object_after(original, starts[0] + len(identity))
        current = json5.loads(original[object_start:object_end])
    except ValueError as exc:
        raise RuntimeError("owned launch profile is not valid JSONC") from exc
    owned_object = isinstance(current, dict)
    if not owned_object:
        raise RuntimeError("owned launch profile must be an object")
    owned = cast("dict[str, JsonValue]", current)
    stamp = _comment_value(original[starts[0] : object_start], "// odcli-profile-stamp: ")
    base_text = _comment_value(original[starts[0] : object_start], "// odcli-profile-base: ")
    current_stamp = _stamp(owned)
    new_stamp = _stamp(profile)
    if current_stamp == new_stamp:
        return original, ()
    if stamp != current_stamp:
        parsed_base = json.loads(base_text) if base_text else {}
        stored = cast("dict[str, JsonValue]", parsed_base if isinstance(parsed_base, dict) else {})
        fields = _diff_fields(stored, owned)
        names = ", ".join(fields) or "profile"
        raise RuntimeError(f"owned launch profile has a field-level conflict: {names}")
    return original[:start] + block + original[object_end:], _diff_fields(owned, profile)


def _new_document(profile: dict[str, JsonValue], owner_kind: str, owner_id: str) -> str:
    block = _block(profile, owner_kind, owner_id, "    ")
    return f'{{\n  "version": "0.2.0",\n  "configurations": [\n{block}\n  ]\n}}\n'


def _insert(original: str, block: str) -> str:
    open_at, close_at = _configurations_span(original)
    body = original[open_at + 1 : close_at]
    separator = "," if _needs_separator(body) else ""
    return original[:close_at] + separator + "\n" + block + "\n" + original[close_at:]


def _block(profile: dict[str, JsonValue], owner_kind: str, owner_id: str, indent: str) -> str:
    rendered = json.dumps(profile, indent=2, ensure_ascii=False)
    lines = [indent + line if line else line for line in rendered.splitlines()]
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (
        f"{indent}{_identity(owner_kind, owner_id)}\n"
        f"{indent}// odcli-profile-stamp: {_stamp(profile)}\n"
        f"{indent}// odcli-profile-base: {canonical}\n" + "\n".join(lines)
    )


def _identity_offsets(text: str, identity: str) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        found = text.find(identity, start)
        if found < 0:
            return offsets
        line_start = text.rfind("\n", 0, found) + 1
        line_end = text.find("\n", found)
        line_end = len(text) if line_end < 0 else line_end
        prefix = text[line_start:found].strip()
        trailer = text[found + len(identity) : line_end].strip()
        if prefix == "" and trailer == "":
            offsets.append(found)
        start = found + len(identity)


def _identity(owner_kind: str, owner_id: str) -> str:
    return f"// odcli-profile: {owner_kind}:{owner_id}"


def _stamp(value: JsonValue) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _kind(original: str, updated: str, existed: bool) -> str:
    if original == updated:
        return "noop"
    if not existed:
        return "create"
    return "update"


def _needs_separator(body: str) -> bool:
    index = len(body)
    while index > 0:
        if body[index - 1] in " \t\r\n":
            index -= 1
            continue
        if index >= 2 and body[index - 2 : index] == "*/":
            start = body.rfind("/*", 0, index - 2)
            if start < 0:
                break
            index = start
            continue
        line_start = body.rfind("\n", 0, index) + 1
        comment = body.find("//", line_start, index)
        if comment >= 0 and not _inside_comment_or_string(body[:index], comment):
            index = line_start
            continue
        return body[index - 1] not in "[,"
    return False


def _comment_value(window: str, marker: str) -> str:
    start = window.find(marker)
    if start < 0:
        return ""
    return window[start + len(marker) :].splitlines()[0].strip()


def _diff_fields(left: dict[str, JsonValue], right: dict[str, JsonValue]) -> tuple[str, ...]:
    keys = sorted(set(left) | set(right))
    return tuple(key for key in keys if left.get(key) != right.get(key))


def _configurations_span(text: str) -> tuple[int, int]:
    key_at = _root_key(text, "configurations")
    if key_at < 0:
        raise RuntimeError("launch.json has no configurations array")
    index = key_at + len('"configurations"')
    while index < len(text) and text[index] in " \t\r\n:":
        index += 1
    if index >= len(text) or text[index] != "[":
        raise RuntimeError("launch.json configurations must be an array")
    return index, _matching(text, index, "[", "]")


def _root_key(text: str, name: str) -> int:
    needle = f'"{name}"'
    depth = 0
    index = 0
    in_string = False
    escape = False
    while index < len(text):
        if not in_string and depth == 1 and text.startswith(needle, index):
            return index
        index, in_string, escape, char = _advance(text, index, in_string, escape)
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
    return -1


def _object_after(text: str, start: int) -> tuple[int, int]:
    index = start
    while index < len(text):
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text[index] in " \t\r\n":
            index += 1
            continue
        if text[index] == "{":
            return index, _matching(text, index, "{", "}") + 1
        raise ValueError("owned profile is not an object")
    raise ValueError("owned profile is missing")


def _matching(text: str, open_at: int, opener: str, closer: str) -> int:
    depth = 0
    index = open_at
    in_string = False
    escape = False
    while index < len(text):
        previous = index
        index, in_string, escape, char = _advance(text, index, in_string, escape)
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return previous
    raise ValueError("unterminated JSON value")


def _inside_comment_or_string(text: str, position: int) -> bool:
    index = 0
    in_string = False
    escape = False
    while index < position:
        index, in_string, escape, _char = _advance(text, index, in_string, escape)
        if index > position:
            return True
    return in_string


def _advance(text: str, index: int, in_string: bool, escape: bool) -> tuple[int, bool, bool, str]:
    if in_string:
        return _advance_string(text, index, escape)
    comment = _comment_end(text, index)
    if comment is not None:
        return comment, False, False, ""
    char = text[index]
    if char == '"':
        return index + 1, True, False, ""
    return index + 1, False, False, char


def _advance_string(text: str, index: int, escape: bool) -> tuple[int, bool, bool, str]:
    if escape:
        return index + 1, True, False, ""
    if text[index] == "\\":
        return index + 1, True, True, ""
    if text[index] == '"':
        return index + 1, False, False, ""
    return index + 1, True, False, ""


def _comment_end(text: str, index: int) -> int | None:
    if text.startswith("//", index):
        newline = text.find("\n", index)
        return len(text) if newline < 0 else newline + 1
    if text.startswith("/*", index):
        end = text.find("*/", index + 2)
        if end < 0:
            raise ValueError("unterminated comment")
        return end + 2
    return None
