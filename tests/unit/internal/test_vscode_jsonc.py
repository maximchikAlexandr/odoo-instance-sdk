"""Tests for identity-marked VS Code JSONC edits."""

from __future__ import annotations

import json
from pathlib import Path

import json5
import pytest

from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.vscode_jsonc import apply_profile_change, plan_profile_change


def _profile(program: str = "/odoo/odoo-bin") -> dict[str, JsonValue]:
    return {
        "name": "Odoo demo",
        "type": "python",
        "request": "launch",
        "program": program,
        "justMyCode": False,
    }


def test_create_update_noop_and_conflict(tmp_path: Path) -> None:
    path = tmp_path / "launch.json"
    created = plan_profile_change(path, _profile(), "environment", "env-1")
    assert created.kind == "create"
    assert "program" in created.fields
    apply_profile_change(created)
    assert "// odcli-profile: environment:env-1" in path.read_text()

    same = plan_profile_change(path, _profile(), "environment", "env-1")
    assert same.kind == "noop"
    assert same.fields == ()
    before = path.read_bytes()
    apply_profile_change(same)
    assert path.read_bytes() == before

    updated = plan_profile_change(path, _profile("/other/odoo-bin"), "environment", "env-1")
    assert updated.kind == "update"
    assert updated.fields == ("program",)
    apply_profile_change(updated)
    assert json5.loads(path.read_text())["configurations"][0]["program"] == "/other/odoo-bin"

    text = path.read_text().replace(
        '"program": "/other/odoo-bin"', '"program": "/manual/odoo-bin"', 1
    )
    path.write_text(text)
    with pytest.raises(RuntimeError, match="field-level conflict: program"):
        plan_profile_change(path, _profile("/runtime/odoo-bin"), "environment", "env-1")
    assert path.read_text() == text


def test_apply_refuses_a_changed_source(tmp_path: Path) -> None:
    path = tmp_path / "launch.json"
    change = plan_profile_change(path, _profile(), "project", "demo")
    path.write_text("{}\n")
    with pytest.raises(RuntimeError, match="changed after the edit was planned"):
        apply_profile_change(change)
    assert path.read_text() == "{}\n"


def test_invalid_jsonc_is_not_modified(tmp_path: Path) -> None:
    path = tmp_path / "launch.json"
    path.write_text("{ this is not jsonc\n")
    original = path.read_text()
    with pytest.raises(RuntimeError, match="not valid JSONC"):
        plan_profile_change(path, _profile(), "project", "demo")
    assert path.read_text() == original


def test_duplicate_identity_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "launch.json"
    created = plan_profile_change(path, _profile(), "environment", "env-1")
    apply_profile_change(created)
    identity = "// odcli-profile: environment:env-1"
    path.write_text(path.read_text().replace(identity, identity + "\n    " + identity, 1))
    original = path.read_text()
    with pytest.raises(RuntimeError, match="more than one owned"):
        plan_profile_change(path, _profile(), "environment", "env-1")
    assert path.read_text() == original


def test_trailing_comma_stays_valid(tmp_path: Path) -> None:
    path = tmp_path / "launch.json"
    path.write_text(
        '{\n  "configurations": [\n    { "name": "foreign", "type": "python" },\n  ]\n}\n'
    )
    change = plan_profile_change(path, _profile(), "environment", "env-1")
    apply_profile_change(change)
    parsed = json5.loads(path.read_text())
    assert [item["name"] for item in parsed["configurations"]] == ["foreign", "Odoo demo"]
    assert json.loads(json.dumps(parsed["configurations"][0]))["name"] == "foreign"
