from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from odoo_instance_sdk.internal.dbprep.source import compare_provenance
from odoo_instance_sdk.models import BackupProvenanceStatus


class ProvenanceCase(NamedTuple):
    selection: str
    matched: bool
    runtime_change: bool = False
    advanced_origin: bool = False


@pytest.fixture
def provenance_repository(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[str, bool, str]:
    case: ProvenanceCase = request.param

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "dev")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    git("commit", "--allow-empty", "-m", "initial")
    old = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-m", "branch tip")
    tip = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/dev", tip)
    git("branch", "wrong-name", tip)
    git("checkout", "-b", "planning")
    (tmp_path / "openspec").mkdir()
    (tmp_path / "openspec" / "proposal.md").write_text("Planning only\n")
    git("add", "openspec")
    if case.runtime_change:
        (tmp_path / "module.py").write_text("runtime_change = True\n")
        git("add", "module.py")
    git("commit", "--allow-empty", "-m", "planning")
    planning = git("rev-parse", "HEAD")
    git("checkout", "-b", "diverged", old)
    git("commit", "--allow-empty", "-m", "diverged")
    diverged = git("rev-parse", "HEAD")
    git("checkout", "--orphan", "unrelated")
    git("commit", "--allow-empty", "-m", "unrelated")
    unrelated = git("rev-parse", "HEAD")
    if case.advanced_origin:
        git("checkout", "-b", "advanced", tip)
        git("commit", "--allow-empty", "-m", "new origin tip")
        git("update-ref", "refs/remotes/origin/dev", git("rev-parse", "HEAD"))
    git("branch", "-f", "dev", unrelated)
    before = git("show-ref")
    base = {
        "tip": tip,
        "old": old,
        "planning": planning,
        "planning-advanced": planning,
        "planning-runtime": planning,
        "unrelated": unrelated,
        "diverged": diverged,
        "missing": "0" * 40,
    }.get(case.selection, case.selection)
    return base, case.matched, before


@pytest.mark.parametrize(
    "provenance_repository",
    [
        pytest.param(ProvenanceCase("tip", True), id="tip"),
        pytest.param(ProvenanceCase("old", True), id="old"),
        pytest.param(ProvenanceCase("planning", True), id="planning"),
        pytest.param(
            ProvenanceCase("planning-advanced", True, advanced_origin=True), id="planning-advanced"
        ),
        pytest.param(
            ProvenanceCase("planning-runtime", False, runtime_change=True, advanced_origin=True),
            id="planning-runtime",
        ),
        pytest.param(ProvenanceCase("origin/dev", True), id="origin-dev"),
        pytest.param(ProvenanceCase("refs/remotes/origin/dev", True), id="remote-origin-dev"),
        pytest.param(ProvenanceCase("unrelated", False), id="unrelated"),
        pytest.param(ProvenanceCase("diverged", False), id="diverged"),
        pytest.param(ProvenanceCase("missing", False), id="missing"),
        pytest.param(ProvenanceCase("wrong-name", False), id="wrong-name"),
    ],
    indirect=True,
)
def test_git_provenance_preserves_pins_and_rejects_unverified_refs(
    tmp_path: Path, provenance_repository: tuple[str, bool, str]
) -> None:
    base, matched, before = provenance_repository

    result = compare_provenance(base, "dev", repository_root=tmp_path)

    expected = BackupProvenanceStatus.MATCHED if matched else BackupProvenanceStatus.MISMATCHED
    assert result.status is expected
    assert result.expected_base_ref == base
    assert (
        subprocess.run(
            ["git", "-C", str(tmp_path), "show-ref"], check=True, capture_output=True, text=True
        ).stdout.strip()
        == before
    )
