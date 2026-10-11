from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

SKILL = Path(__file__).resolve().parents[2] / ".agents/skills/odcli-autonomous-work"
SHA = "a" * 40
MERGE_SHA = "b" * 40


class _BootstrapRunner:
    def __init__(self, provenance: str, engine: str) -> None:
        self.provenance = provenance
        self.engine = engine
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        responses: dict[tuple[str, ...], str] = {("-I", "-c"): self.provenance}
        return responses.get(tuple(argv[1:3]), self.engine)


class _ForeignProvenanceRunner:
    """Step-keyed bootstrap double that records forbidden package execution."""

    def __init__(self, sentinel: Path) -> None:
        self.sentinel = sentinel
        self.calls: list[list[str]] = []

    def _probe(self) -> str:
        return json.dumps(
            {
                "url": "https://evil.example/foreign.git",
                "vcs_info": {"commit_id": SHA},
            }
        )

    def _package_import(self) -> str:
        self.sentinel.write_text("imported", encoding="utf-8")
        return "{}"

    def _unexpected(self) -> str:
        raise AssertionError("unexpected bootstrap step")

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        responses: dict[tuple[str, ...], Callable[[], str]] = {
            ("-I", "-c"): self._probe,
            ("-m", "odoo_instance_sdk.internal.alternate_tool"): self._package_import,
        }
        response = responses.get(tuple(argv[1:3]), self._unexpected)
        return response()


@dataclass(frozen=True)
class _BootstrapCase:
    name: str
    expected: str
    configure: Callable[[ModuleType, pytest.MonkeyPatch, Path, Path, Path], None]


def _configure_shadowed(
    fix_tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    canonical: Path,
    trusted: Path,
) -> None:
    shadow = tmp_path / "shadowed-odcli"
    shadow.write_text(f"#!{trusted}\n", encoding="utf-8")
    shadow.chmod(0o700)
    monkeypatch.setattr(fix_tool.shutil, "which", lambda _name: str(shadow))


def _configure_symlink(
    fix_tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    canonical: Path,
    _trusted: Path,
) -> None:
    link = tmp_path / "symlink-odcli"
    link.symlink_to(canonical)
    monkeypatch.setattr(fix_tool.shutil, "which", lambda _name: str(link))


def _configure_foreign(
    fix_tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    _tmp_path: Path,
    canonical: Path,
    _trusted: Path,
) -> None:
    foreign = canonical.parent.parent.parent.parent / "foreign-python"
    foreign.write_text("#!/bin/sh\n", encoding="utf-8")
    foreign.chmod(0o700)
    canonical.write_text(f"#!{foreign}\n", encoding="utf-8")
    monkeypatch.setattr(fix_tool.shutil, "which", lambda _name: str(canonical))


def _module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        name, SKILL.parents[2] / "scripts" / "odcli_agent_tools" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_user_and_developer_skill_surfaces_keep_executable_helpers_canonical() -> None:
    developer = SKILL.parents[0] / "odcli-developer-work"
    assert not list(SKILL.rglob("*.py"))
    assert not list(developer.rglob("*.py"))
    user_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    developer_text = (developer / "SKILL.md").read_text(encoding="utf-8")
    assert "numbered_tool.py" not in user_text
    assert "fix-tools" not in user_text
    assert "scripts/odcli_agent_tools/numbered_tool.py" in developer_text
    for helper in ("fix_tool.py", "reviewer.py", "numbered_tool.py"):
        assert (SKILL.parents[2] / "scripts" / "odcli_agent_tools" / helper).is_file()


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
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps({"session_id": "session-1", "review": {"sha": SHA, "compatible": True}}),
        encoding="utf-8",
    )
    engine_calls: list[list[str]] = []

    engine_responses: dict[str, dict[str, object] | list[object]] = {
        "install": {"launcher": str(tmp_path / ".local/bin/odcli-fix-118")},
        "list": [{"identifier": "118", "healthy": True}],
        "inspect": {"identifier": "118", "healthy": True, "pr": 119, "branch": None},
        "remove": {"removed": True},
    }

    def fake_engine(argv: list[str]) -> dict[str, object] | list[object]:
        engine_calls.append(argv)
        return engine_responses[argv[0]]

    gh_responses: dict[tuple[str, str], dict[str, object]] = {
        ("pr", "view"): {
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
        },
        ("issue", "view"): {"state": "CLOSED"},
    }

    def fake_gh(argv: list[str]) -> dict[str, object]:
        key = cast("tuple[str, str]", tuple(argv[:2]))
        return gh_responses.get(key, {"status": "ahead"})

    monkeypatch.setattr(fix_tool, "_gh_json", fake_gh)
    monkeypatch.setattr(fix_tool, "_engine_json", fake_engine)
    with pytest.raises(RuntimeError, match="compatibility"):
        fix_tool._install(118, "c" * 40, 119, review_path, None)
    fix_tool._install(118, SHA, 119, review_path, None)
    assert engine_calls[0][:4] == ["install", "--kind", "hotfix", "--identifier"]
    fix_tool._reconcile("c" * 40)
    assert ["remove", "--kind", "hotfix", "--identifier", "118"] in engine_calls


def test_fix_tool_bootstraps_shared_engine_with_validated_canonical_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fix_tool = _module("fix_tool")
    home = tmp_path / "home"
    tool_root = home / ".local" / "share" / "uv" / "tools" / "odoo-instance-sdk"
    tool_root.mkdir(mode=0o700, parents=True)
    interpreter = tool_root / "bin" / "python"
    interpreter.parent.mkdir(mode=0o700)
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o700)
    odcli = home / ".local" / "bin" / "odcli"
    odcli.parent.mkdir(mode=0o700, parents=True)
    odcli.write_text(f"#!{interpreter} -s\n", encoding="utf-8")
    odcli.chmod(0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(fix_tool.shutil, "which", lambda _name: str(odcli))
    runner = _BootstrapRunner(
        json.dumps(
            {
                "url": "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
                "vcs_info": {"commit_id": SHA},
            }
        ),
        json.dumps({"launcher": str(tmp_path / "launcher")}),
    )
    monkeypatch.setattr(fix_tool, "_run", runner)
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps({"session_id": "session", "review": {"sha": SHA, "compatible": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fix_tool,
        "_gh_json",
        lambda _argv: {
            "headRefOid": SHA,
            "closingIssuesReferences": [
                {
                    "number": 42,
                    "repository": {
                        "name": "odoo-instance-sdk",
                        "owner": {"login": "maximchikAlexandr"},
                    },
                }
            ],
        },
    )

    assert fix_tool._canonical_interpreter() == interpreter
    fix_tool._install(42, SHA, 119, review_path, None)
    assert runner.calls == [
        [
            str(interpreter),
            "-I",
            "-c",
            fix_tool._PROVENANCE_PROBE,
        ],
        [
            str(interpreter),
            "-m",
            "odoo_instance_sdk.internal.alternate_tool",
            "install",
            "--kind",
            "hotfix",
            "--identifier",
            "42",
            "--sha",
            SHA,
            "--pr",
            "119",
            "--review-sha",
            SHA,
            "--review-session",
            "session",
        ],
    ]


def test_fix_tool_rejects_foreign_provenance_before_package_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fix_tool = _module("fix_tool")
    home = tmp_path / "home"
    tool_root = home / ".local" / "share" / "uv" / "tools" / "odoo-instance-sdk"
    tool_root.mkdir(mode=0o700, parents=True)
    interpreter = tool_root / "bin" / "python"
    interpreter.parent.mkdir(mode=0o700)
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o700)
    odcli = home / ".local" / "bin" / "odcli"
    odcli.parent.mkdir(mode=0o700, parents=True)
    odcli.write_text(f"#!{interpreter}\n", encoding="utf-8")
    odcli.chmod(0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(fix_tool.shutil, "which", lambda _name: str(odcli))
    sentinel = tmp_path / "foreign-module-imported"
    runner = _ForeignProvenanceRunner(sentinel)
    monkeypatch.setattr(fix_tool, "_run", runner)
    with pytest.raises(RuntimeError, match="trusted repository"):
        fix_tool._engine_json(["list"])
    assert not sentinel.exists()
    assert runner.calls == [[str(interpreter), "-I", "-c", fix_tool._PROVENANCE_PROBE]]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _BootstrapCase("shadowed", r"trusted user installation", _configure_shadowed),
            id="shadowed",
        ),
        pytest.param(
            _BootstrapCase("symlink", r"trusted user installation", _configure_symlink),
            id="symlink",
        ),
        pytest.param(
            _BootstrapCase("foreign-interpreter", r"absolute executable", _configure_foreign),
            id="foreign-interpreter",
        ),
    ],
)
def test_fix_tool_bootstrap_rejects_untrusted_canonical_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: _BootstrapCase
) -> None:
    fix_tool = _module("fix_tool")
    home = tmp_path / "home"
    trusted_root = home / ".local" / "share" / "uv" / "tools" / "odoo-instance-sdk"
    trusted_root.mkdir(mode=0o700, parents=True)
    trusted_python = trusted_root / "bin" / "python"
    trusted_python.parent.mkdir(mode=0o700)
    trusted_python.write_text("#!/bin/sh\n", encoding="utf-8")
    trusted_python.chmod(0o700)
    canonical = home / ".local" / "bin" / "odcli"
    canonical.parent.mkdir(mode=0o700, parents=True)
    canonical.write_text(f"#!{trusted_python}\n", encoding="utf-8")
    canonical.chmod(0o700)
    monkeypatch.setenv("HOME", str(home))

    case.configure(fix_tool, monkeypatch, tmp_path, canonical, trusted_python)
    with pytest.raises(RuntimeError, match=case.expected):
        fix_tool._canonical_interpreter()


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
