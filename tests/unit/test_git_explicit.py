from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    GitRepositoryChangedError,
    GitRepositoryError,
    GitSyncError,
    PlanValidationError,
)
from odoo_instance_sdk.internal import git_frontier, proc
from odoo_instance_sdk.models import (
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    GitLabIdentity,
    StartConfig,
)
from odoo_instance_sdk.resources.git import GitResource
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.module import ModuleResource

MEMBER = "11111111-1111-4111-8111-111111111111"
HOST = "gitlab.example"


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


def _repo(root: Path, *, origin: str = "https://gitlab.example/team/repo") -> None:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "feature")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "tests")
    _git(root, "remote", "add", "origin", origin)
    (root / "README.md").write_text("base\\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "base")


def _instance(root: Path) -> OdooInstance:
    config = InstanceConfig(
        base_url="http://127.0.0.1:8069",
        default_cwd=root,
        start_config=StartConfig(addons_path=["addons"]),
    )
    instance = cast("OdooInstance", SimpleNamespace(config=config))
    instance.modules = ModuleResource(instance)
    return instance


def _credential(
    host: str = HOST, *, login: str = "alice", token: str = "token"
) -> tuple[GitLabCredentialContext, GitLabIdentity]:
    credential = GitLabCredentialContext(
        identity=GitLabCredentialIdentity(
            user_id=MEMBER,
            host=host,
            login=login,
            token_key="ODCLI_GITLAB_TOKEN_TEST",
        ),
        _token=token,
    )
    identity = GitLabIdentity(
        user_id=7, username=login, name="Alice Example", email="alice@example.test", host=host
    )
    return credential, identity


def test_initialized_explicit_sync_allows_selected_https_push_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(root, "add", ".odcli/project.toml")
    _git(root, "commit", "-qm", "project config")
    _git(root, "branch", "main")
    credential, _identity = _credential()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_explicit.resolve_credentials",
        lambda *_args, **_kwargs: (credential, _identity),
    )
    remote_sha = "a" * 40
    calls: list[str] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            stdout = ""
            if step_id == "git.sync.branch":
                stdout = "feature\n"
            elif step_id == "git.sync.remote":
                stdout = "https://gitlab.example/team/repo\n"
            elif step_id == "git.sync.authoritative-remote-sha":
                stdout = f"{remote_sha}\trefs/heads/feature\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=stdout,
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    command = GitResource(_instance(root)).sync_command(
        repository=root,
        member_id=MEMBER,
        base="main",
        push=True,
    )

    assert "git.sync.authoritative-remote-sha" in calls
    assert any(step.step_id == "git.sync.push-lease" for step in command.plan.process_steps)
    private_steps = cast("proc.PreparedStep", command._prepared().steps[0])
    assert "credential.helper=" in private_steps.argv
    assert "core.fsmonitor=false" in private_steps.argv
    assert "core.hooksPath=/dev/null" in private_steps.argv
    assert all(
        isinstance(step, proc.PreparedStep) and step.runtime_environment_factory is not None
        for step in command._prepared().steps
    )


def test_initialized_explicit_sync_verifies_identity_before_git_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(root, "add", ".odcli/project.toml")
    _git(root, "commit", "-qm", "project config")
    calls: list[str] = []

    class NoPlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            calls.append(getattr(step, "step_id"))
            raise AssertionError("identity rejection must precede Git sync planning")

    def reject_identity(*_args: object, **_kwargs: object) -> object:
        raise PlanValidationError("verified GitLab identity does not match selected member")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_explicit.resolve_credentials", reject_identity
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", NoPlanningExecutor)
    with pytest.raises(PlanValidationError, match="identity"):
        GitResource(_instance(root)).sync_command(repository=root, member_id=MEMBER, base="main")
    assert calls == []


def test_initialized_explicit_sync_rejects_local_transport_before_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(root, "add", ".odcli/project.toml")
    _git(root, "commit", "-qm", "project config")
    credential, identity = _credential()
    _git(root, "config", "http.sslCAInfo", str(tmp_path / "ca.pem"))
    channel_calls: list[tuple[object, ...]] = []

    def fail_channel(*args: object, **_kwargs: object) -> str:
        channel_calls.append(args)
        raise AssertionError("unsafe local transport must be rejected before channel creation")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_explicit.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_channel)
    with pytest.raises(GitRepositoryError, match="credentialed transport"):
        GitResource(_instance(root)).sync_command(repository=root, member_id=MEMBER, base="main")
    assert channel_calls == []


def test_initialized_explicit_sync_revalidates_exact_origin_before_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(root, "add", ".odcli/project.toml")
    _git(root, "commit", "-qm", "project config")
    credential, identity = _credential()
    remote_sha = "a" * 40
    calls: list[str] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            stdout = ""
            if step_id == "git.sync.branch":
                stdout = "feature\n"
            elif step_id == "git.sync.remote":
                stdout = "https://gitlab.example/team/repo\n"
            elif step_id == "git.sync.authoritative-remote-sha":
                stdout = f"{remote_sha}\trefs/heads/feature\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=stdout,
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_explicit.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    command = GitResource(_instance(root)).sync_command(
        repository=root, member_id=MEMBER, base="main", push=True
    )
    calls.clear()
    _git(root, "remote", "set-url", "origin", "https://gitlab.example/team/other")
    with pytest.raises(GitRepositoryChangedError, match="changed"):
        command.run()
    assert calls == []


def test_initialized_explicit_sync_revalidates_transport_before_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(root, "add", ".odcli/project.toml")
    _git(root, "commit", "-qm", "project config")
    credential, identity = _credential()
    calls: list[str] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            factory = getattr(step, "runtime_environment_factory", None)
            if callable(factory):
                _environment, cleanup, _secrets = factory()
                cleanup()
            stdout = ""
            if step_id == "git.sync.branch":
                stdout = "feature\n"
            elif step_id == "git.sync.remote":
                stdout = "https://gitlab.example/team/repo\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=stdout,
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    def fail_channel(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("transport rejection must precede askpass channel creation")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_explicit.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    command = GitResource(_instance(root)).sync_command(
        repository=root, member_id=MEMBER, base="main", push=True
    )
    calls.clear()
    _git(root, "config", "http.sslCAInfo", str(tmp_path / "replaced-ca.pem"))
    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_channel)
    with pytest.raises(GitRepositoryError, match="credentialed transport"):
        command.run()
    assert calls == []


@pytest.mark.parametrize(
    ("returncode", "stdout", "message"),
    (
        (1, "", "authoritative remote branch probe failed"),
        (
            0,
            "not-a-valid-ls-remote-response\n",
            "authoritative remote branch probe returned malformed output",
        ),
    ),
    ids=("failed", "malformed"),
)
def test_generic_sync_rejects_unusable_planning_probe_before_credentialed_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    message: str,
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential(token="planning-secret")
    calls: list[str] = []
    channels: list[Path] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            factory = getattr(step, "runtime_environment_factory", None)
            if callable(factory):
                runtime_environment, cleanup, _secrets = factory()
                channel = dict(runtime_environment).get("ODCLI_GITLAB_ASKPASS_FILE")
                if channel:
                    channels.append(Path(channel))
                cleanup()
            step_stdout = (
                "https://gitlab.example/team/repo\n" if step_id == "git.sync.remote" else stdout
            )
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=returncode if step_id == "git.sync.authoritative-remote-sha" else 0,
                stdout=step_stdout,
                stderr="planning-secret must never escape",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    with pytest.raises(GitSyncError, match=message) as error:
        GitResource(_instance(tmp_path)).generic_sync_command(
            repository=root,
            member_id=MEMBER,
            base="main",
            push=True,
            credential=credential,
            identity=identity,
        )

    assert calls == ["git.sync.remote", "git.sync.authoritative-remote-sha"]
    assert channels
    assert all(not channel.exists() for channel in channels)
    assert "planning-secret" not in str(error.value)
    assert not any(
        step.endswith(("fetch", "integrate", "push-fast-forward", "push-lease")) for step in calls
    )


def test_generic_sync_allows_first_publication_when_branch_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential(token="first-publish-secret")
    calls: list[str] = []
    channels: list[Path] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            factory = getattr(step, "runtime_environment_factory", None)
            if callable(factory):
                runtime_environment, cleanup, _secrets = factory()
                channel = dict(runtime_environment).get("ODCLI_GITLAB_ASKPASS_FILE")
                if channel:
                    channels.append(Path(channel))
                cleanup()
            step_stdout = ""
            returncode = 0
            if step_id == "git.sync.remote":
                step_stdout = "https://gitlab.example/team/repo\n"
            elif step_id == "git.sync.branch":
                step_stdout = "feature\n"
            elif step_id == "git.sync.upstream":
                step_stdout = "origin/feature\n"
            elif step_id == "git.sync.fetched-sha":
                returncode = 1
            elif step_id == "git.sync.push-fast-forward":
                step_stdout = "published\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=returncode,
                stdout=step_stdout,
                stderr="first-publish-secret must never escape",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    result = (
        GitResource(_instance(tmp_path))
        .generic_sync_command(
            repository=root,
            member_id=MEMBER,
            base="main",
            push=True,
            credential=credential,
            identity=identity,
        )
        .run()
    )

    assert result.pushed is True
    assert "git.sync.push-fast-forward" in calls
    assert "git.sync.push-lease" not in calls
    assert channels
    assert all(not channel.exists() for channel in channels)
    assert "first-publish-secret" not in repr(result)


def test_generic_sync_rejects_remote_branch_appearing_before_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential(token="appearance-secret")
    calls: list[str] = []
    channels: list[Path] = []
    authoritative_calls = 0
    appeared_sha = "c" * 40

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            nonlocal authoritative_calls
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            factory = getattr(step, "runtime_environment_factory", None)
            if callable(factory):
                runtime_environment, cleanup, _secrets = factory()
                channel = dict(runtime_environment).get("ODCLI_GITLAB_ASKPASS_FILE")
                if channel:
                    channels.append(Path(channel))
                cleanup()
            step_stdout = ""
            if step_id == "git.sync.remote":
                step_stdout = "https://gitlab.example/team/repo\n"
            elif step_id == "git.sync.authoritative-remote-sha":
                authoritative_calls += 1
                if authoritative_calls == 2:
                    step_stdout = f"{appeared_sha}\trefs/heads/feature\n"
            elif step_id == "git.sync.branch":
                step_stdout = "feature\n"
            elif step_id == "git.sync.upstream":
                step_stdout = "origin/feature\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=step_stdout,
                stderr="appearance-secret must never escape",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    command = GitResource(_instance(tmp_path)).generic_sync_command(
        repository=root,
        member_id=MEMBER,
        base="main",
        push=True,
        credential=credential,
        identity=identity,
    )
    with pytest.raises(GitSyncError, match="remote branch changed after sync planning") as error:
        command.run()

    assert authoritative_calls == 2
    assert "git.sync.fetch" not in calls
    assert "git.sync.integrate" not in calls
    assert "git.sync.push-fast-forward" not in calls
    assert "git.sync.push-lease" not in calls
    assert channels
    assert all(not channel.exists() for channel in channels)
    assert "appearance-secret" not in str(error.value)


def test_generic_sync_rejects_successful_planning_lease_drift_before_fetch_and_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential(token="lease-secret")
    planned_sha = "a" * 40
    drifted_sha = "b" * 40
    authoritative_calls = 0
    calls: list[str] = []
    channels: list[Path] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            nonlocal authoritative_calls
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            factory = getattr(step, "runtime_environment_factory", None)
            if callable(factory):
                runtime_environment, cleanup, _secrets = factory()
                channel = dict(runtime_environment).get("ODCLI_GITLAB_ASKPASS_FILE")
                if channel:
                    channels.append(Path(channel))
                cleanup()
            step_stdout = ""
            if step_id == "git.sync.remote":
                step_stdout = "https://gitlab.example/team/repo\n"
            elif step_id == "git.sync.authoritative-remote-sha":
                authoritative_calls += 1
                step_stdout = (
                    f"{planned_sha if authoritative_calls == 1 else drifted_sha}"
                    "\trefs/heads/feature\n"
                )
            elif step_id == "git.sync.branch":
                step_stdout = "feature\n"
            elif step_id == "git.sync.upstream":
                step_stdout = "origin/feature\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=step_stdout,
                stderr="lease-secret must never escape",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    command = GitResource(_instance(tmp_path)).generic_sync_command(
        repository=root,
        member_id=MEMBER,
        base="main",
        push=True,
        credential=credential,
        identity=identity,
    )
    with pytest.raises(GitSyncError, match="remote branch changed after sync planning") as error:
        command.run()

    assert authoritative_calls == 2
    assert "git.sync.fetch" not in calls
    assert "git.sync.integrate" not in calls
    assert "git.sync.push-fast-forward" not in calls
    assert "git.sync.push-lease" not in calls
    assert channels
    assert all(not channel.exists() for channel in channels)
    assert "lease-secret" not in str(error.value)


@pytest.mark.parametrize(
    "key", ("remote.origin.pushurl", "url.https://gitlab.example/.pushInsteadOf")
)
def test_initialized_explicit_sync_rejects_push_routing_before_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(root, "add", ".odcli/project.toml")
    _git(root, "commit", "-qm", "project config")
    _git(root, "config", key, "https://gitlab.example/team/other")
    credential, identity = _credential()
    calls: list[str] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            calls.append(getattr(step, "step_id"))
            factory = getattr(step, "runtime_environment_factory", None)
            if callable(factory):
                _environment, cleanup, _secrets = factory()
                cleanup()
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout="feature\n" if getattr(step, "step_id") == "git.sync.branch" else "",
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_explicit.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    with pytest.raises(GitRepositoryError, match=r"push|credentialed transport"):
        GitResource(_instance(root)).sync_command(
            repository=root, member_id=MEMBER, base="main", push=True
        )
    assert calls == []


def test_initialized_explicit_sync_revalidates_push_target_before_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(root, "add", ".odcli/project.toml")
    _git(root, "commit", "-qm", "project config")
    credential, identity = _credential()
    calls: list[str] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            factory = getattr(step, "runtime_environment_factory", None)
            if callable(factory):
                _environment, cleanup, _secrets = factory()
                cleanup()
            stdout = "feature\n" if step_id == "git.sync.branch" else ""
            if step_id == "git.sync.remote":
                stdout = "https://gitlab.example/team/repo\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=stdout,
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    def fail_channel(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("push target rejection must precede askpass channel creation")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_explicit.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    command = GitResource(_instance(root)).sync_command(
        repository=root, member_id=MEMBER, base="main", push=True
    )
    calls.clear()
    _git(root, "config", "remote.origin.pushurl", "https://gitlab.example/team/other")
    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_channel)
    with pytest.raises(GitRepositoryError, match="push"):
        command.run()
    assert calls == []


@pytest.mark.parametrize(
    "key", ("remote.origin.pushurl", "url.https://gitlab.example/.pushInsteadOf")
)
def test_generic_sync_rejects_push_routing_before_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    _git(root, "config", key, "https://gitlab.example/team/other")
    credential, identity = _credential()
    calls: list[str] = []

    class NoPlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            calls.append(getattr(step, "step_id"))
            raise AssertionError("push routing rejection must precede Git planning")

    def fail_channel(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("push routing rejection must precede askpass channel creation")

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", NoPlanningExecutor)
    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_channel)
    with pytest.raises((GitRepositoryError, GitSyncError), match=r"push|transport"):
        GitResource(_instance(root)).generic_sync_command(
            repository=root, member_id=MEMBER, base="main", push=True
        )
    assert calls == []


@pytest.mark.parametrize(
    "key", ("remote.origin.pushurl", "url.https://gitlab.example/.pushInsteadOf")
)
def test_generic_sync_revalidates_push_target_before_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential()
    calls: list[str] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            stdout = ""
            if step_id == "git.sync.remote":
                stdout = "https://gitlab.example/team/repo\n"
            elif step_id == "git.sync.authoritative-remote-sha":
                stdout = f"{'a' * 40}\trefs/heads/feature\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=stdout,
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    def fail_channel(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("push target rejection must precede askpass channel creation")

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    command = GitResource(_instance(root)).generic_sync_command(
        repository=root, member_id=MEMBER, base="main", push=True
    )
    calls.clear()
    _git(root, "config", key, "https://gitlab.example/team/other")
    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_channel)
    with pytest.raises((GitRepositoryError, GitSyncError), match=r"push|transport|changed"):
        command.run()
    assert calls == []


def test_generic_pull_sync_rejects_local_transport_before_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    _git(root, "config", "http.sslCAInfo", str(tmp_path / "ca.pem"))
    credential, identity = _credential()
    calls: list[str] = []

    class NoPlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            calls.append(getattr(step, "step_id"))
            raise AssertionError("pull transport rejection must precede Git planning")

    def fail_channel(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("pull transport rejection must precede askpass channel creation")

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", NoPlanningExecutor)
    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_channel)
    with pytest.raises(GitRepositoryError, match="credentialed transport"):
        GitResource(_instance(root)).generic_sync_command(
            repository=root, member_id=MEMBER, base="main", push=False
        )
    assert calls == []


def test_generic_sync_rejects_push_project_drift_before_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential()
    calls: list[str] = []

    class NoPlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            calls.append(getattr(step, "step_id"))
            raise AssertionError("push project rejection must precede Git planning")

    def fail_channel(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("push project rejection must precede askpass channel creation")

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.repository_push_origin",
        lambda _root: "https://gitlab.example/team/other",
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", NoPlanningExecutor)
    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_channel)
    with pytest.raises(GitSyncError, match="push origin"):
        GitResource(_instance(root)).generic_sync_command(
            repository=root, member_id=MEMBER, base="main", push=True
        )
    assert calls == []


@pytest.mark.parametrize(
    "planned_remote",
    ("https://gitlab.example/team/other\n", "not-a-repository-url\n"),
)
def test_generic_sync_rejects_foreign_remote_before_authoritative_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, planned_remote: str
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential()
    calls: list[str] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            calls.append(getattr(step, "step_id"))
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=planned_remote,
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)

    def fail_channel(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("project rejection must precede askpass channel creation")

    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_channel)
    with pytest.raises(GitSyncError, match="verified HTTPS origin"):
        GitResource(_instance(root)).generic_sync_command(
            repository=root, member_id=MEMBER, base="main", push=False
        )
    assert calls == ["git.sync.remote"]


@pytest.mark.parametrize("push", (False, True))
def test_generic_sync_rejects_same_host_runtime_project_before_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, push: bool
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential()
    calls: list[str] = []
    planning = True

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            stdout = ""
            if step_id == "git.sync.remote":
                stdout = (
                    "https://gitlab.example/team/repo\n"
                    if planning
                    else "https://gitlab.example/team/other\n"
                )
            elif step_id == "git.sync.authoritative-remote-sha":
                stdout = f"{'a' * 40}\trefs/heads/feature\n"
            elif step_id == "git.sync.branch":
                stdout = "feature\n"
            elif step_id == "git.sync.upstream":
                stdout = "origin/feature\n"
            elif step_id == "git.sync.fetched-sha":
                stdout = f"{'a' * 40}\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=stdout,
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    command = GitResource(_instance(root)).generic_sync_command(
        repository=root, member_id=MEMBER, base="main", push=push
    )
    planning = False
    calls.clear()
    with pytest.raises(GitSyncError, match="repository origin changed"):
        command.run()
    assert "git.sync.authoritative-remote-sha" not in calls
    assert "git.sync.fetch" not in calls
    assert "git.sync.integrate" not in calls
    assert not any(step_id.startswith("git.sync.push-") for step_id in calls)


def test_generic_pull_sync_revalidates_local_transport_before_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential()
    calls: list[str] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            stdout = ""
            if step_id == "git.sync.remote":
                stdout = "https://gitlab.example/team/repo\n"
            elif step_id == "git.sync.authoritative-remote-sha":
                stdout = f"{'a' * 40}\trefs/heads/feature\n"
            elif step_id == "git.sync.branch":
                stdout = "feature\n"
            elif step_id == "git.sync.upstream":
                stdout = "origin/feature\n"
            elif step_id == "git.sync.fetched-sha":
                stdout = f"{'a' * 40}\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=stdout,
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    def fail_channel(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("pull transport rejection must precede askpass channel creation")

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        lambda *_args, **_kwargs: (credential, identity),
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    command = GitResource(_instance(root)).generic_sync_command(
        repository=root, member_id=MEMBER, base="main", push=False
    )
    calls.clear()
    _git(root, "config", "http.sslCAInfo", str(tmp_path / "replaced-ca.pem"))
    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_channel)
    with pytest.raises(GitRepositoryError, match="credentialed transport"):
        command.run()
    assert calls == []
