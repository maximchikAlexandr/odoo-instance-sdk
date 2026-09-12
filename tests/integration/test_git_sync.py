from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import GitSyncError
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    ProcessResult,
    StepObserver,
    SubprocessExecutor,
)
from odoo_instance_sdk.resources.git import GitResource
from odoo_instance_sdk.resources.instance import OdooInstance

pytestmark = pytest.mark.integration


def _resource(instance: SimpleNamespace) -> GitResource:
    return GitResource(cast("OdooInstance", instance))


def _has_authoritative_step(observation: object) -> bool:
    if not isinstance(observation, dict):
        return False
    step_ids = observation.get("step_ids")
    return isinstance(step_ids, list) and "git.sync.authoritative-remote-sha" in step_ids


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _sync_repo(tmp_path: Path, *, branch: str = "feature") -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    local = tmp_path / "repo"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")
    local.mkdir()
    _git(local, "init", "-q", "-b", "main")
    _git(local, "config", "user.email", "tests@example.test")
    _git(local, "config", "user.name", "tests")
    (local / "base.txt").write_text("base\n", encoding="utf-8")
    _git(local, "add", "base.txt")
    _git(local, "commit", "-qm", "base")
    _git(local, "remote", "add", "origin", str(remote))
    _git(local, "push", "-qu", "origin", "main")
    if branch != "main":
        _git(local, "checkout", "-qb", branch)
    return remote, local


def test_sync_publishes_same_name_branch_without_local_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote.git"
    local = tmp_path / "repo"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")
    local.mkdir()
    _git(local, "init", "-q", "-b", "main")
    _git(local, "config", "user.email", "tests@example.test")
    _git(local, "config", "user.name", "tests")
    (local / "base.txt").write_text("base\n", encoding="utf-8")
    _git(local, "add", "base.txt")
    _git(local, "commit", "-qm", "base")
    _git(local, "remote", "add", "origin", str(remote))
    _git(local, "push", "-qu", "origin", "main")
    _git(local, "checkout", "-qb", "feature")
    (local / "change.txt").write_text("change\n", encoding="utf-8")
    _git(local, "add", "change.txt")
    _git(local, "commit", "-qm", "[IMP] repo: feature change")
    _git(local, "push", "-q", "origin", "feature")
    (local / "follow-up.txt").write_text("follow-up\n", encoding="utf-8")
    _git(local, "add", "follow-up.txt")
    _git(local, "commit", "-qm", "[IMP] repo: follow-up")

    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=local)
    )
    monkeypatch.setattr("odoo_instance_sdk.resources.git._ssh_remote", lambda _: True)

    result = _resource(instance).sync(base="main", push=True)

    assert result.pushed is True
    assert result.fetched_sha is not None
    assert (
        _git(remote, "rev-parse", "refs/heads/feature").strip()
        == _git(local, "rev-parse", "HEAD").strip()
    )


def test_sync_rebases_same_name_branch_without_publication(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    local = tmp_path / "repo"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")
    local.mkdir()
    _git(local, "init", "-q", "-b", "main")
    _git(local, "config", "user.email", "tests@example.test")
    _git(local, "config", "user.name", "tests")
    (local / "base.txt").write_text("base\n", encoding="utf-8")
    _git(local, "add", "base.txt")
    _git(local, "commit", "-qm", "base")
    _git(local, "remote", "add", "origin", str(remote))
    _git(local, "push", "-qu", "origin", "main")
    _git(local, "checkout", "-qb", "feature")
    (local / "change.txt").write_text("change\n", encoding="utf-8")
    _git(local, "add", "change.txt")
    _git(local, "commit", "-qm", "[IMP] repo: feature change")
    _git(local, "push", "-qu", "origin", "feature")

    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=local)
    )
    resource = _resource(instance)
    command = resource.sync_command(base="main", push=True)
    step_ids = [step.step_id for step in command.plan.process_steps]
    assert "git.sync.fetch" in step_ids
    assert all(step.argv[0] == "git" for step in command.plan.process_steps)

    result = resource.sync(base="main")

    assert result.branch == "feature"
    assert result.base == "main"
    assert result.rebased is True
    assert result.pushed is False
    assert result.fetched_sha is not None


@pytest.mark.parametrize("tracking", ["absent", "stale"])
def test_sync_uses_authoritative_same_name_remote_head(
    tmp_path: Path, tracking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote, local = _sync_repo(tmp_path)
    base_sha = _git(local, "rev-parse", "main").strip()
    (local / "remote-feature.txt").write_text("remote\n", encoding="utf-8")
    _git(local, "add", "remote-feature.txt")
    _git(local, "commit", "-qm", "[IMP] repo: remote feature")
    _git(local, "push", "-q", "origin", "feature")
    remote_sha = _git(remote, "rev-parse", "refs/heads/feature").strip()
    if tracking == "absent":
        _git(local, "update-ref", "-d", "refs/remotes/origin/feature")
    else:
        _git(local, "update-ref", "refs/remotes/origin/feature", base_sha)
    (local / "local-feature.txt").write_text("local\n", encoding="utf-8")
    _git(local, "add", "local-feature.txt")
    _git(local, "commit", "-qm", "[IMP] repo: local feature")

    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=local)
    )
    monkeypatch.setattr("odoo_instance_sdk.resources.git._ssh_remote", lambda _: True)

    resource = _resource(instance)
    command = resource.sync_command(base="main")
    authoritative = next(
        step
        for step in command.plan.process_steps
        if step.step_id == "git.sync.authoritative-remote-sha"
    )
    assert authoritative.argv[-1] == "refs/heads/feature"
    assert any(
        isinstance(observation, dict)
        and observation.get("executed_during_planning") is True
        and _has_authoritative_step(observation)
        for observation in command.plan.observations
    )

    result = resource.sync(base="main")

    assert result.fetched_sha == remote_sha
    assert result.rebased is True


def test_sync_handles_a_new_unpublished_branch_without_remote_integration(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    local = tmp_path / "repo"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")
    local.mkdir()
    _git(local, "init", "-q", "-b", "main")
    _git(local, "config", "user.email", "tests@example.test")
    _git(local, "config", "user.name", "tests")
    (local / "base.txt").write_text("base\n", encoding="utf-8")
    _git(local, "add", "base.txt")
    _git(local, "commit", "-qm", "base")
    _git(local, "remote", "add", "origin", str(remote))
    _git(local, "push", "-qu", "origin", "main")
    _git(local, "checkout", "-qb", "new-feature")
    (local / "change.txt").write_text("change\n", encoding="utf-8")
    _git(local, "add", "change.txt")
    _git(local, "commit", "-qm", "[IMP] repo: new feature")

    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=local)
    )

    result = _resource(instance).sync(base="main")

    assert result.branch == "new-feature"
    assert result.fetched_sha is None
    assert result.pushed is False


def test_sync_missing_origin_is_plannable_but_fails_before_fetch(tmp_path: Path) -> None:
    local = tmp_path / "repo"
    local.mkdir()
    _git(local, "init", "-q", "-b", "main")
    _git(local, "config", "user.email", "tests@example.test")
    _git(local, "config", "user.name", "tests")
    (local / "base.txt").write_text("base\n", encoding="utf-8")
    _git(local, "add", "base.txt")
    _git(local, "commit", "-qm", "base")
    _git(local, "checkout", "-qb", "feature")
    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=local)
    )
    resource = _resource(instance)

    command = resource.sync_command(base="main")
    assert command.plan.process_steps
    with pytest.raises(GitSyncError, match="origin remote is unavailable"):
        command.run()
    assert not (local / ".git" / "FETCH_HEAD").exists()


@pytest.mark.parametrize("mode", ["dirty", "protected", "https", "upstream"])
def test_sync_public_guard_matrix(tmp_path: Path, mode: str) -> None:
    _, local = _sync_repo(tmp_path, branch="main" if mode == "protected" else "feature")
    if mode == "dirty":
        (local / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    elif mode == "https":
        _git(local, "remote", "set-url", "origin", "https://example.test/repo.git")
    elif mode == "upstream":
        _git(local, "branch", "--set-upstream-to=origin/main", "feature")
    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=local)
    )
    resource = _resource(instance)

    with pytest.raises(GitSyncError) as failure:
        resource.sync(base="main", push=mode == "https")
    message = str(failure.value)
    expected = {
        "dirty": "clean worktree",
        "protected": "protected",
        "https": "SSH origin",
        "upstream": "upstream must be origin/feature",
    }[mode]
    assert expected in message


def test_sync_rejects_post_plan_remote_race_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote, local = _sync_repo(tmp_path)
    (local / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(local, "add", "feature.txt")
    _git(local, "commit", "-qm", "[IMP] repo: feature")
    _git(local, "push", "-q", "origin", "feature")
    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=local)
    )
    monkeypatch.setattr("odoo_instance_sdk.resources.git._ssh_remote", lambda _: True)
    command = _resource(instance).sync_command(base="main", push=True)

    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    _git(other, "config", "user.email", "tests@example.test")
    _git(other, "config", "user.name", "tests")
    _git(other, "checkout", "-q", "feature")
    (other / "race.txt").write_text("race\n", encoding="utf-8")
    _git(other, "add", "race.txt")
    _git(other, "commit", "-qm", "[IMP] repo: race")
    _git(other, "push", "-q", "origin", "feature")

    with pytest.raises(GitSyncError, match="changed after sync planning"):
        command.run()


def test_sync_publishes_rewritten_head_with_exact_authoritative_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote, local = _sync_repo(tmp_path)
    (local / "base-feature.txt").write_text("base\n", encoding="utf-8")
    _git(local, "add", "base-feature.txt")
    _git(local, "commit", "-qm", "[IMP] repo: feature base")
    _git(local, "push", "-q", "origin", "feature")
    authoritative = _git(remote, "rev-parse", "refs/heads/feature").strip()
    (local / "local-feature.txt").write_text("local\n", encoding="utf-8")
    _git(local, "add", "local-feature.txt")
    _git(local, "commit", "-qm", "[IMP] repo: rewritten local")
    calls: list[tuple[str, ...]] = []

    class ForceLeaseExecutor(SubprocessExecutor):
        def execute(
            self,
            step: PreparedProcess,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> ProcessResult:
            calls.append(tuple(step.argv))
            result = super().execute(step, observer=observer, observe_output=observe_output)
            if step.step_id == "git.sync.integrate":
                _git(local, "update-ref", "-d", "refs/remotes/origin/feature")
            return result

    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", ForceLeaseExecutor)
    monkeypatch.setattr("odoo_instance_sdk.resources.git._ssh_remote", lambda _: True)
    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=local)
    )

    result = _resource(instance).sync(base="main", push=True)

    lease_calls = [argv for argv in calls if "--force-with-lease" in " ".join(argv)]
    assert len(lease_calls) == 1
    assert f"--force-with-lease=refs/heads/feature:{authoritative}" in lease_calls[0]
    assert (
        _git(remote, "rev-parse", "refs/heads/feature").strip()
        == _git(local, "rev-parse", "HEAD").strip()
    )
    assert result.pushed is True


def test_sync_rejects_post_fetch_remote_lease_race_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote, local = _sync_repo(tmp_path)
    (local / "base-feature.txt").write_text("base\n", encoding="utf-8")
    _git(local, "add", "base-feature.txt")
    _git(local, "commit", "-qm", "[IMP] repo: feature base")
    _git(local, "push", "-q", "origin", "feature")
    other = tmp_path / "race"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    _git(other, "config", "user.email", "tests@example.test")
    _git(other, "config", "user.name", "tests")
    _git(other, "checkout", "-q", "feature")
    (other / "race.txt").write_text("race\n", encoding="utf-8")
    _git(other, "add", "race.txt")
    _git(other, "commit", "-qm", "[IMP] repo: race")
    (local / "local-feature.txt").write_text("local\n", encoding="utf-8")
    _git(local, "add", "local-feature.txt")
    _git(local, "commit", "-qm", "[IMP] repo: local")
    calls: list[tuple[str, ...]] = []

    class RaceExecutor(SubprocessExecutor):
        def execute(
            self,
            step: PreparedProcess,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> ProcessResult:
            calls.append(tuple(step.argv))
            result = super().execute(step, observer=observer, observe_output=observe_output)
            if step.step_id == "git.sync.fetch":
                _git(other, "push", "-q", "origin", "feature")
            elif step.step_id == "git.sync.integrate":
                _git(local, "update-ref", "-d", "refs/remotes/origin/feature")
            return result

    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", RaceExecutor)
    monkeypatch.setattr("odoo_instance_sdk.resources.git._ssh_remote", lambda _: True)
    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=local)
    )

    with pytest.raises(GitSyncError, match="stale lease was not retried"):
        _resource(instance).sync(base="main", push=True)

    lease_calls = [argv for argv in calls if "--force-with-lease" in " ".join(argv)]
    assert len(lease_calls) == 1
    assert (
        _git(remote, "rev-parse", "refs/heads/feature").strip()
        == _git(other, "rev-parse", "HEAD").strip()
    )


def test_sync_conflict_preserves_state_and_recovery_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote, local = _sync_repo(tmp_path)
    (local / "conflict.txt").write_text("base\n", encoding="utf-8")
    _git(local, "add", "conflict.txt")
    _git(local, "commit", "-qm", "[IMP] repo: feature base")
    _git(local, "push", "-q", "origin", "feature")
    (local / "conflict.txt").write_text("local\n", encoding="utf-8")
    _git(local, "add", "conflict.txt")
    _git(local, "commit", "-qm", "[IMP] repo: local conflict")
    other = tmp_path / "conflict-remote"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    _git(other, "config", "user.email", "tests@example.test")
    _git(other, "config", "user.name", "tests")
    _git(other, "checkout", "-q", "feature")
    (other / "conflict.txt").write_text("remote\n", encoding="utf-8")
    _git(other, "add", "conflict.txt")
    _git(other, "commit", "-qm", "[IMP] repo: remote conflict")
    _git(other, "push", "-q", "origin", "feature")
    monkeypatch.setattr("odoo_instance_sdk.resources.git._ssh_remote", lambda _: True)
    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=local)
    )

    with pytest.raises(GitSyncError) as failure:
        _resource(instance).sync(base="main")

    message = str(failure.value)
    assert "git rebase --continue" in message
    assert "git rebase --abort" in message
    assert "UU conflict.txt" in _git(local, "status", "--porcelain")
