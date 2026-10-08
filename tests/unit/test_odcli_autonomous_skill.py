from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[2] / ".agents/skills/odcli-autonomous-work"
SHA = "a" * 40
MERGE_SHA = "b" * 40


def _module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codex_wrapper_uses_read_only_session_and_can_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reviewer = _module("reviewer")
    assert "report_only" in reviewer._schema("triage")["properties"]["verdict"]["enum"]
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> Any:
        calls.append(argv)
        result = Path(argv[argv.index("--output-last-message") + 1])
        result.write_text(
            json.dumps(
                {
                    "verdict": "blocked",
                    "reason": "missing",
                    "safe_workaround": "",
                    "shared_state_compatible": False,
                }
            ),
            encoding="utf-8",
        )
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '{"type":"thread.started","thread_id":"session-1"}\n',
                "stderr": "",
            },
        )()

    monkeypatch.setattr(reviewer.shutil, "which", lambda _name: "/bin/codex")
    monkeypatch.setattr(reviewer.subprocess, "run", fake_run)
    session, result = reviewer._run_codex(
        prompt="case",
        instructions="verifier instructions",
        schema=reviewer._schema("triage"),
        session_id=None,
        model=None,
    )
    assert session == "session-1" and result["verdict"] == "blocked"
    assert calls[0][0:4] == ["codex", "exec", "-s", "read-only"]
    assert any(arg.startswith("developer_instructions=") for arg in calls[0])
    assert "--output-schema" in calls[0]
    reviewer._run_codex(
        prompt="follow-up", instructions=None, schema=None, session_id=session, model=None
    )
    assert calls[1][0:4] == ["codex", "exec", "resume", "session-1"]


def test_fix_tool_requires_exact_compatibility_and_retires_only_merged_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fix_tool = _module("fix_tool")
    monkeypatch.setattr(fix_tool.Path, "home", lambda: tmp_path)
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps({"session_id": "session-1", "review": {"sha": SHA, "compatible": True}}),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> str:
        commands.append(argv)
        if argv[:3] == ["uv", "tool", "install"]:
            (tmp_path / ".local/share/odcli-fix-tools/118/bin/odcli").touch()
        return ""

    def fake_gh(argv: list[str]) -> dict[str, object]:
        if argv[:2] == ["pr", "view"]:
            return {
                "headRefOid": SHA,
                "state": "MERGED",
                "mergedAt": "2026-10-05T00:00:00Z",
                "baseRefName": "main",
                "mergeCommit": {"oid": MERGE_SHA},
                "closingIssuesReferences": [
                    {
                        "number": 118,
                        "repository": {
                            "name": "odoo-instance-sdk",
                            "owner": {"login": "maximchikAlexandr"},
                        },
                    }
                ],
            }
        if argv[:2] == ["issue", "view"]:
            return {"state": "CLOSED"}
        return {"status": "ahead"}

    monkeypatch.setattr(fix_tool, "_run", fake_run)
    monkeypatch.setattr(fix_tool, "_gh_json", fake_gh)
    with pytest.raises(RuntimeError, match="compatibility"):
        fix_tool._install(118, "c" * 40, 119, review_path, None)
    fix_tool._install(118, SHA, 119, review_path, None)
    shim = tmp_path / ".local/bin/odcli-fix-118"
    assert "WARNING:" in shim.read_text(encoding="utf-8")
    assert "shares ~/.odcli" in shim.read_text(encoding="utf-8")
    compile(shim.read_text(encoding="utf-8"), str(shim), "exec")
    fix_tool._reconcile("c" * 40)
    assert not shim.exists()
    assert not (tmp_path / ".odcli/fix-tools/118.json").exists()
    assert ["uv", "tool", "uninstall", "odoo-instance-sdk"] in commands


def test_closed_issue_alone_does_not_retire_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    fix_tool = _module("fix_tool")
    monkeypatch.setattr(
        fix_tool,
        "_gh_json",
        lambda argv: {"state": "CLOSED"} if argv[0] == "issue" else {"state": "OPEN"},
    )
    assert fix_tool._merged_into_install(118, 119, SHA) is False


def test_fix_tool_rejects_same_issue_number_in_other_repository() -> None:
    fix_tool = _module("fix_tool")
    assert not fix_tool._links_issue(
        [{"number": 118, "repository": {"name": "other", "owner": {"login": "someone"}}}],
        118,
    )


def test_update_wrapper_reconciles_only_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fix_tool = _module("fix_tool")
    reconciled: list[str] = []
    monkeypatch.setattr(fix_tool, "_reconcile", reconciled.append)
    monkeypatch.setattr(
        fix_tool,
        "_run",
        lambda _argv: json.dumps({"result": {"outcome": "updated", "final_sha": SHA}}),
    )
    fix_tool._update("main")
    assert reconciled == [SHA]
    monkeypatch.setattr(
        fix_tool,
        "_run",
        lambda _argv: json.dumps({"result": {"outcome": "rolled_back", "final_sha": SHA}}),
    )
    with pytest.raises(RuntimeError, match="did not report success"):
        fix_tool._update("main")
    assert reconciled == [SHA]
