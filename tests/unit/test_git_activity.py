from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.git_activity import collect_git_activity
from odoo_instance_sdk.models import GitActivityState

# These are deterministic local-repository integration tests, not pure unit tests.
pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _isolated_git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep both test setup and the production collector off user Git state."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", os.devnull)


def _git(args: list[str], cwd: Path) -> str:
    """Run git, assert success, return stdout."""
    # These are real Git integration tests even though they live beside unit
    # tests.  Never inherit a developer's aliases, global config, or hooks.
    env = os.environ | {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": os.devnull,
    }
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", "main"], path)
    _git(["config", "user.email", "t@t.t"], path)
    _git(["config", "user.name", "tester"], path)
    _git(["config", "commit.gpgsign", "false"], path)


def _commit(path: Path, msg: str, files: dict[str, str | bytes] | None = None) -> str:
    if files:
        for name, content in files.items():
            f = path / name
            f.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                f.write_bytes(content)
            else:
                f.write_text(content, encoding="utf-8")
            _git(["add", "--", name], path)
    _git(["commit", "-q", "--allow-empty", "-m", msg], path)
    return _git(["rev-parse", "HEAD"], path).strip()


def test_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "init", {"a.txt": "hello\n"})
    act = collect_git_activity(repo)
    assert act.state is GitActivityState.CLEAN
    assert act.ahead == 0
    assert act.behind == 0
    assert act.diff is not None
    assert act.diff.added == 0
    assert act.diff.deleted == 0
    assert act.branch == "main"
    assert act.head_sha is not None and len(act.head_sha) == 40
    assert act.short_sha == act.head_sha[:7]


def test_ahead(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base = _commit(repo, "base", {"a.txt": "1\n"})
    _git(["checkout", "-q", "-b", "feature"], repo)
    _commit(repo, "f1", {"a.txt": "1\n2\n3\n"})
    act = collect_git_activity(repo)
    assert act.state is GitActivityState.AHEAD
    assert act.ahead == 1
    assert act.behind == 0
    assert act.branch == "feature"
    assert act.diff is not None
    assert act.diff.added == 2
    assert act.diff.deleted == 0
    # merge-base is the base commit (no behind).
    assert act.head_sha != base


def test_behind(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "base", {"a.txt": "1\n"})
    _git(["checkout", "-q", "-b", "feature"], repo)
    # main advances while feature stays put
    _git(["checkout", "-q", "main"], repo)
    _commit(repo, "main2", {"a.txt": "1\n2\n"})
    _git(["checkout", "-q", "feature"], repo)
    act = collect_git_activity(repo)
    assert act.state is GitActivityState.BEHIND
    assert act.ahead == 0
    assert act.behind == 1
    assert act.branch == "feature"


def test_diverged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "base", {"a.txt": "1\n"})
    _git(["checkout", "-q", "-b", "feature"], repo)
    _commit(repo, "f1", {"a.txt": "1\nadded\n"})
    _git(["checkout", "-q", "main"], repo)
    _commit(repo, "main2", {"a.txt": "1\nmore\n"})
    _git(["checkout", "-q", "feature"], repo)
    act = collect_git_activity(repo)
    assert act.state is GitActivityState.DIVERGED
    assert act.ahead == 1
    assert act.behind == 1
    assert act.diff is not None
    assert act.diff.added == 1
    assert act.diff.deleted == 0


def test_orphan_not_a_repo(tmp_path: Path) -> None:
    # empty dir, no .git
    repo = tmp_path / "repo"
    repo.mkdir()
    act = collect_git_activity(repo)
    assert act.state is GitActivityState.ORPHAN
    assert act.head_sha is None
    assert act.short_sha is None
    assert act.branch == "unknown"
    assert act.ahead is None
    assert act.behind is None
    assert act.diff is None


def test_orphan_no_main_no_upstream(tmp_path: Path) -> None:
    # repo where HEAD exists but neither main@{upstream} nor refs/heads/main resolve.
    # Build with a non-main default branch, then there is no `main` ref at all.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "other"], repo)
    _git(["config", "user.email", "t@t.t"], repo)
    _git(["config", "user.name", "tester"], repo)
    _commit(repo, "x", {"a.txt": "1\n"})
    act = collect_git_activity(repo)
    assert act.state is GitActivityState.ORPHAN
    # A missing canonical default branch is a Git collector failure and must
    # use the fully redacted orphan contract.
    assert act.head_sha is None
    assert act.short_sha is None
    assert act.branch == "unknown"
    assert act.ahead is None
    assert act.behind is None
    assert act.diff is None


def test_binary_files_skipped(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "base", {"a.txt": "1\n"})
    _git(["checkout", "-q", "-b", "feature"], repo)
    binary = bytes(range(256))
    _commit(repo, "bin", {"a.txt": "1\n2\n", "blob.bin": binary})
    act = collect_git_activity(repo)
    assert act.state is GitActivityState.AHEAD
    assert act.diff is not None
    # Only the text change (1 line added) counts; binary contributes 0.
    assert act.diff.added == 1
    assert act.diff.deleted == 0


def test_stale_local_main_falls_back_to_upstream(tmp_path: Path) -> None:
    # No upstream configured → `main@{upstream}` fails → fall back to refs/heads/main.
    # Local main is STALE (behind origin/main after a fetch), but the fallback uses
    # the local ref, not origin/main. Prove it: if origin/main were used, the branch
    # would be DIVERGED; with the stale-local fallback it's AHEAD.
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(["init", "-q", "--bare", "-b", "main"], remote)

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "base", {"a.txt": "1\n"})
    _git(["remote", "add", "origin", str(remote)], repo)
    _git(["push", "-q", "origin", "main"], repo)
    # Populate refs/remotes/origin/main (push alone doesn't create it locally).
    _git(["fetch", "-q", "origin"], repo)

    # Advance origin/main via a second clone; do NOT fetch back into `repo`.
    other = tmp_path / "other"
    _git(["clone", "-q", str(remote), str(other)], tmp_path)
    _git(["config", "user.email", "t@t.t"], other)
    _git(["config", "user.name", "tester"], other)
    _commit(other, "remote2", {"a.txt": "1\n2\n3\n"})
    _git(["push", "-q", "origin", "main"], other)
    _git(["fetch", "-q", "origin"], repo)  # now origin/main is ahead of local main

    # feature off the STALE local main; no upstream set on main.
    _git(["checkout", "-q", "-b", "feature"], repo)
    _commit(repo, "f1", {"a.txt": "1\nfeat\n"})

    act = collect_git_activity(repo)
    # Fallback to refs/heads/main (stale) → ahead=1, behind=0 → AHEAD.
    # If origin/main had been used → DIVERGED. AHEAD proves the fallback path.
    assert act.state is GitActivityState.AHEAD
    assert act.ahead == 1
    assert act.behind == 0
    assert act.diff is not None
    assert act.diff.added == 1


def test_upstream_used_when_configured(tmp_path: Path) -> None:
    # Upstream IS configured and ahead of local main → BEHIND (proves upstream
    # path, not the stale-local fallback).
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(["init", "-q", "--bare", "-b", "main"], remote)

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "base", {"a.txt": "1\n"})
    _git(["remote", "add", "origin", str(remote)], repo)
    _git(["push", "-q", "origin", "main"], repo)
    _git(["fetch", "-q", "origin"], repo)
    _git(["branch", "--set-upstream-to", "origin/main", "main"], repo)

    # Advance origin/main via a second clone; fetch back.
    other = tmp_path / "other"
    _git(["clone", "-q", str(remote), str(other)], tmp_path)
    _git(["config", "user.email", "t@t.t"], other)
    _git(["config", "user.name", "tester"], other)
    _commit(other, "remote2", {"a.txt": "1\n2\n3\n"})
    _git(["push", "-q", "origin", "main"], other)
    _git(["fetch", "-q", "origin"], repo)

    act = collect_git_activity(repo)
    # origin/main is 1 ahead of local main (still on main) → BEHIND.
    assert act.state is GitActivityState.BEHIND
    assert act.ahead == 0
    assert act.behind == 1


def test_collection_is_stateless(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "init", {"a.txt": "hi\n"})
    first = collect_git_activity(repo)
    second = collect_git_activity(repo)
    assert first == second
    assert first is not second


def test_cache_invalidated_on_head_change(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "init", {"a.txt": "hi\n"})
    _git(["checkout", "-q", "-b", "feature"], repo)
    first = collect_git_activity(repo)
    assert first.state is GitActivityState.CLEAN
    _commit(repo, "two", {"a.txt": "hi\nmore\n"})
    second = collect_git_activity(repo)
    assert second.state is GitActivityState.AHEAD
    assert second.ahead == 1


@pytest.mark.parametrize("failed_command", ["branch", "merge-base", "counts"])
def test_unexpected_git_failures_are_fully_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_command: str
) -> None:
    """Only merge-base rc=1 is the documented partial/orphan exception."""
    import odoo_instance_sdk.internal.git_activity as activity

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "base")
    original = activity._run_git

    def failing(args: list[str], cwd: Path) -> tuple[int, str, str]:
        if (
            (failed_command == "branch" and args[:2] == ["rev-parse", "--abbrev-ref"])
            or (failed_command == "merge-base" and args[0] == "merge-base")
            or (failed_command == "counts" and args[:2] == ["rev-list", "--count"])
        ):
            return 2, "", "unexpected"
        return original(args, cwd)

    monkeypatch.setattr(activity, "_run_git", failing)
    result = activity.collect_git_activity(repo)
    assert result.state is GitActivityState.ORPHAN
    assert (
        result.head_sha,
        result.short_sha,
        result.branch,
        result.ahead,
        result.behind,
        result.diff,
    ) == (None, None, "unknown", None, None, None)


def test_no_common_ancestor_keeps_known_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import odoo_instance_sdk.internal.git_activity as activity

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "base")
    original = activity._run_git

    def no_merge_base(args: list[str], cwd: Path) -> tuple[int, str, str]:
        if args[0] == "merge-base":
            return 1, "", ""
        return original(args, cwd)

    monkeypatch.setattr(activity, "_run_git", no_merge_base)
    result = activity.collect_git_activity(repo)
    assert result.state is GitActivityState.ORPHAN
    assert result.head_sha is not None and result.branch == "main"
