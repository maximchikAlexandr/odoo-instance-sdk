from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.git_activity import collect_git_activity
from odoo_instance_sdk.internal.proc import ProcessResult
from odoo_instance_sdk.models import GitActivityState
from odoo_instance_sdk.resources.monitor import _recorded_git_activity

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


def test_custom_upstream_base_ref_is_used_without_fetch_or_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(["init", "-q", "--bare", "-b", "main"], remote)

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "base", {"a.txt": "base\n"})
    _git(["remote", "add", "origin", str(remote)], repo)
    _git(["push", "-q", "origin", "main"], repo)
    _git(["fetch", "-q", "origin"], repo)
    _git(["checkout", "-q", "-b", "dev"], repo)
    _git(["push", "-q", "-u", "origin", "dev"], repo)

    _git(["checkout", "-q", "main"], repo)
    _commit(repo, "main-only", {"main.txt": "main\n"})
    _git(["push", "-q", "origin", "main"], repo)
    _git(["checkout", "-q", "dev"], repo)
    _commit(repo, "dev-change", {"a.txt": "base\ndev\n", "blob.bin": bytes(range(32))})
    (repo / "uncommitted.txt").write_text("ignored\n", encoding="utf-8")

    import odoo_instance_sdk.internal.git_activity as activity

    original = activity._run_git
    calls: list[tuple[str, ...]] = []

    def recording(args: list[str], cwd: Path) -> tuple[int, str, str]:
        calls.append(tuple(args))
        return original(args, cwd)

    monkeypatch.setattr(activity, "_run_git", recording)
    result = collect_git_activity(repo, base_ref="dev")

    assert result.default_branch == "dev"
    assert result.state is GitActivityState.AHEAD
    assert result.ahead == 1
    assert result.behind == 0
    assert result.diff is not None
    assert result.diff.added == 1
    assert result.diff.deleted == 0
    assert ("rev-parse", "--verify", "dev@{upstream}") in calls
    merge_base = _git(["merge-base", "dev@{upstream}", "HEAD"], repo).strip()
    assert ("diff", "--numstat", f"{merge_base}...HEAD") in calls
    assert not any(args and args[0] in {"fetch", "pull"} for args in calls)
    assert not any("main" in arg for args in calls for arg in args)


def test_custom_local_base_ref_works_without_main_and_matches_recorded(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "dev"], repo)
    _git(["config", "user.email", "t@t.t"], repo)
    _git(["config", "user.name", "tester"], repo)
    _commit(repo, "base", {"a.txt": "base\n"})
    _commit(repo, "dev-change", {"a.txt": "base\ndev\n"})
    (repo / "uncommitted.txt").write_text("ignored\n", encoding="utf-8")

    direct = collect_git_activity(repo, base_ref="dev")

    def captured(args: list[str], *, returncode: int = 0) -> ProcessResult:
        if returncode:
            stdout = ""
            stderr = "missing upstream"
        else:
            proc = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                check=False,
                env=os.environ,
            )
            assert proc.returncode == 0, proc.stderr
            stdout = proc.stdout
            stderr = proc.stderr
        return ProcessResult(
            argv=("git", "-C", str(repo), *args),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration=0.0,
            cwd=str(repo),
            environment=(),
        )

    recorded = {
        "head": captured(["rev-parse", "--verify", "HEAD"]),
        "branch": captured(["rev-parse", "--abbrev-ref", "HEAD"]),
        "upstream": captured(["rev-parse", "--verify", "dev@{upstream}"], returncode=1),
        "local_main": captured(["rev-parse", "--verify", "refs/heads/dev"]),
        "local_merge_base": captured(["merge-base", "refs/heads/dev", "HEAD"]),
        "local_ahead": captured(["rev-list", "--count", "refs/heads/dev..HEAD"]),
        "local_behind": captured(["rev-list", "--count", "HEAD..refs/heads/dev"]),
        "local_diff": captured(["diff", "--numstat", "refs/heads/dev...HEAD"]),
    }

    assert _recorded_git_activity(recorded, base_ref="dev") == direct


def test_head_base_ref_without_upstream_uses_exact_recorded_ref(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "init", {"a.txt": "hello\n"})

    result = collect_git_activity(repo, base_ref="HEAD")

    assert result.default_branch == "HEAD"
    assert result.state is GitActivityState.CLEAN
    assert result.ahead == 0
    assert result.behind == 0
    assert result.diff is not None
    assert result.diff.added == 0
    assert result.diff.deleted == 0


def test_non_branch_base_ref_uses_exact_recorded_ref(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    initial = _commit(repo, "init", {"a.txt": "hello\n"})
    _git(["tag", "v1"], repo)
    _commit(repo, "change", {"a.txt": "hello\nchanged\n"})

    result = collect_git_activity(repo, base_ref="refs/tags/v1")

    assert result.default_branch == "refs/tags/v1"
    assert result.state is GitActivityState.AHEAD
    assert result.head_sha != initial
    assert result.ahead == 1
    assert result.behind == 0
    assert result.diff is not None
    assert result.diff.added == 1
    assert result.diff.deleted == 0


def test_custom_base_ref_missing_ancestry_is_orphan_with_identity(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "main-base", {"main.txt": "main\n"})
    _git(["checkout", "-q", "--orphan", "dev"], repo)
    _git(["rm", "-q", "-rf", "."], repo)
    _commit(repo, "unrelated-dev", {"dev.txt": "dev\n"})
    _git(["checkout", "-q", "main"], repo)

    result = collect_git_activity(repo, base_ref="dev")

    assert result.default_branch == "dev"
    assert result.state is GitActivityState.ORPHAN
    assert result.head_sha is not None
    assert result.branch == "main"
    assert result.ahead is None
    assert result.behind is None
    assert result.diff is None


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
