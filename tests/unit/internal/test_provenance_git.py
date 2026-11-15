from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.dbprep.source import compare_provenance
from odoo_instance_sdk.models import BackupProvenanceStatus


@pytest.mark.parametrize(
    ("selection", "matched"),
    [
        ("tip", True),
        ("old", True),
        ("planning", True),
        ("planning-advanced", True),
        ("planning-runtime", False),
        ("origin/dev", True),
        ("refs/remotes/origin/dev", True),
        ("unrelated", False),
        ("diverged", False),
        ("missing", False),
        ("wrong-name", False),
    ],
)
def test_git_provenance_preserves_pins_and_rejects_unverified_refs(
    tmp_path: Path, selection: str, matched: bool
) -> None:
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
    if selection == "planning-runtime":
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
    if selection in ("planning-advanced", "planning-runtime"):
        git("checkout", "-b", "advanced", tip)
        git("commit", "--allow-empty", "-m", "new origin tip")
        git("update-ref", "refs/remotes/origin/dev", git("rev-parse", "HEAD"))
    # A stale local dev must not hide the fetched origin/dev.
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
    }.get(selection, selection)

    result = compare_provenance(base, "dev", repository_root=tmp_path)

    expected = BackupProvenanceStatus.MATCHED if matched else BackupProvenanceStatus.MISMATCHED
    assert result.status is expected
    assert result.expected_base_ref == base
    assert git("show-ref") == before
