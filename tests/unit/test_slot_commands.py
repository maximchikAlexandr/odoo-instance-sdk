from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.commands.slot import slot_group
from odoo_instance_sdk.internal.alternate_tool import AlternateToolError

SHA = "a" * 40


def _document(result: Any) -> dict[str, Any]:
    assert result.exit_code in (0, 1), result.output
    return cast("dict[str, Any]", json.loads(result.stdout))


def test_slot_help_states_isolation_boundary() -> None:
    result = CliRunner().invoke(slot_group, ["--help"])
    assert result.exit_code == 0, result.output
    help_text = " ".join(result.output.split())
    assert (
        "Numbered slots isolate only OdCLI-managed user state (SQLite catalogue, "
        "configuration, backups, environment artifacts, project runtime state, locks, "
        "bug reports, and migration/update journals). They do not isolate project "
        "checkouts, Odoo processes, PostgreSQL clusters or databases, filestores, "
        "Docker resources, or ports; use separate project copies or disjoint "
        "external-resource configuration before concurrent use."
    ) in help_text


@pytest.mark.parametrize("number", ["0", "-1", "01", " 1", "1 ", "one"])
def test_install_rejects_invalid_number_before_shared_engine(
    monkeypatch: pytest.MonkeyPatch, number: str
) -> None:
    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("shared lifecycle must not run for an invalid identity")

    monkeypatch.setattr("odoo_instance_sdk.commands.slot.install_alternate_tool", unexpected)
    result = CliRunner().invoke(
        slot_group,
        ["install", number, SHA, "--format", "json"],
    )
    assert result.exit_code == 1
    document = _document(result)
    assert document["ok"] is False
    assert "positive decimal" in document["error"]["message"]


@pytest.mark.parametrize(
    ("replace", "args"),
    [
        (False, ["install", "2", SHA, "--format", "json"]),
        (True, ["install", "2", SHA, "--replace", "--format", "json"]),
    ],
)
def test_install_delegates_exact_sha_and_replace_intent(
    monkeypatch: pytest.MonkeyPatch,
    replace: bool,
    args: list[str],
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, bool, Path]] = []

    class Manifest:
        def to_json(self) -> dict[str, object]:
            return {
                "kind": "numbered",
                "identifier": "2",
                "sha": SHA,
                "launcher": str(tmp_path / ".local/bin/odcli-2"),
            }

    def install(identity: Any, sha: str, *, replace: bool = False) -> Manifest:
        calls.append((identity.kind, identity.identifier, replace, identity.home))
        return Manifest()

    monkeypatch.setattr("odoo_instance_sdk.commands.slot.install_alternate_tool", install)
    result = CliRunner().invoke(slot_group, args, env={"HOME": str(tmp_path)})
    assert result.exit_code == 0, result.output
    assert calls == [("numbered", "2", replace, tmp_path.resolve())]
    assert _document(result)["result"]["sha"] == SHA


def test_list_is_bounded_and_format_parity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rows = (
        {
            "kind": "numbered",
            "identifier": "2",
            "sha": SHA,
            "launcher": str(tmp_path / ".local/bin/odcli-2"),
            "tool": str(tmp_path / ".local/share/odcli-slots/2/tool"),
            "state": str(tmp_path / ".odcli-2"),
            "healthy": True,
        },
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.slot.list_alternate_tools", lambda *_args: rows)
    runner = CliRunner()
    json_result = runner.invoke(slot_group, ["list", "--format", "json"])
    toon_result = runner.invoke(slot_group, ["list", "--format", "toon"])
    assert json_result.exit_code == toon_result.exit_code == 0
    assert _document(json_result)["result"]["slots"] == list(rows)
    assert "2" in toon_result.stdout
    assert "No arbitrary home scan" not in toon_result.stdout


def test_numbered_context_rejects_management_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODCLI_LAUNCHER_KIND", "numbered")
    monkeypatch.setenv("ODCLI_LAUNCHER_ID", "2")

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("numbered context must not inspect slot state")

    monkeypatch.setattr("odoo_instance_sdk.commands.slot.list_alternate_tools", unexpected)
    result = CliRunner().invoke(slot_group, ["list", "--format", "json"])
    assert result.exit_code == 1
    document = _document(result)
    assert document["error"]["message"] == (
        "slot management is available only through canonical odcli"
    )


def test_remove_preserves_typed_failure_from_shared_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.slot.remove_alternate_tool",
        lambda _identity: (_ for _ in ()).throw(
            AlternateToolError("alternate launcher is still running: odcli-2")
        ),
    )
    result = CliRunner().invoke(
        slot_group,
        ["remove", "2", "--format", "json"],
    )
    assert result.exit_code == 1
    document = _document(result)
    assert document["error"]["message"] == "alternate launcher is still running: odcli-2"
