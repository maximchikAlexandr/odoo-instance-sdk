from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import cast

import pytest

from odoo_instance_sdk.exceptions import (
    GitLabCredentialError,
    GitRepositoryError,
    GitSyncError,
    GitWorkflowError,
    PlanValidationError,
)
from odoo_instance_sdk.internal import git_frontier, git_repository, proc
from odoo_instance_sdk.internal.git_sync import AuthoritativeProbeCapture
from odoo_instance_sdk.models import GitLabCredentialContext
from odoo_instance_sdk.resources.git import GitResource
from odoo_instance_sdk.resources.git_generic import _planned_remote_sha, _remote_head
from tests.unit.git_test_support import (
    HOST,
    MEMBER,
    OTHER_HOST,
    OTHER_MEMBER,
    THIRD_HOST,
    _add_local_submodule,
    _credential,
    _git,
    _identity_transport,
    _instance,
    _mapping,
    _real_clone_executor,
    _recursive_sources,
    _repo,
    _two_level_sources,
)


class _MultiSiblingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
        step_id = getattr(step, "step_id")
        self.calls.append(step_id)
        stdout_by_step = {
            "git.clone.submodule-status": "-abc first\n-def nested/second\n",
            "git.clone.submodule-status-final": " abc first\n def nested/second\n",
        }
        return proc.ProcessResult(
            argv=tuple(getattr(step, "argv")),
            returncode=0,
            stdout=stdout_by_step.get(step_id, ""),
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd"),
            environment=(),
        )


def _generic_nested_result_policy(
    step_id: str,
    command_args: Sequence[str],
    checkout_root: Path,
    leaf_source: Path,
    deepest_source: Path | None,
    after_result: bool,
) -> subprocess.CompletedProcess[str] | None:
    if deepest_source is None:
        return None
    nested_policy = (leaf_source / ".gitmodules").read_text()
    if after_result:
        if "gitlab.third.example" not in command_args and step_id == "git.clone.submodule-frontier":
            shutil.rmtree(checkout_root / "child" / "leaf" / "vendor" / "lib", ignore_errors=True)
        return None
    if "evil.example" in nested_policy and step_id in {
        "git.clone.submodule-frontier",
        "git.clone.submodule-update-recursive",
    }:
        return subprocess.CompletedProcess((), 1, "", "unsafe nested frontier")
    if (
        "gitlab.third.example" not in command_args
        and step_id == "git.clone.submodule-update-recursive"
    ):
        return subprocess.CompletedProcess((), 1, "", "submodule frontier unavailable")
    return None


def test_recursive_clone_ignores_other_member_mapping_hosts(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _repo(root)
    _mapping(tmp_path, (HOST,))
    _mapping(tmp_path, (OTHER_HOST,), member_id=OTHER_MEMBER, append=True)
    credential, identity = _credential()

    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo",
        root,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )

    assert [step.step_id for step in command.plan.process_steps] == [
        "git.clone.submodule-status",
        "git.clone.submodule-update",
        "git.clone.submodule-frontier",
        "git.clone.submodule-update-recursive",
        "git.clone.submodule-status-final",
    ]
    assert "gitlab.other.example" not in repr(command.plan)


def test_reused_recursive_clone_updates_all_missing_sibling_submodules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    _mapping(tmp_path, (HOST,))
    credential, identity = _credential()

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.missing_submodules",
        lambda _root: ("first", "nested/second"),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.submodule_recursive_update_arguments",
        lambda _root, *paths, allowed_hosts: (
            "submodule",
            "update",
            "--init",
            "--",
            *paths,
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.validate_frontier", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.validated_frontier_credentials",
        lambda *_args, **_kwargs: (credential,),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.validate_submodule_cache",
        lambda *_args, **_kwargs: None,
    )

    executor = _MultiSiblingExecutor()
    monkeypatch.setattr(proc, "SubprocessExecutor", lambda: executor)
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo",
        root,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )

    update_steps = [
        step
        for step in command.plan.process_steps
        if step.step_id.startswith("git.clone.submodule-update.")
    ]
    assert [step.argv[-1] for step in update_steps] == ["first", "nested/second"]
    result = command.run()
    assert result.initialized_submodules == ("first", "nested/second")
    assert executor.calls == [
        "git.clone.submodule-status",
        "git.clone.submodule-update.0",
        "git.clone.submodule-update.1",
        "git.clone.submodule-status-final",
    ]


def test_reused_recursive_clone_falls_back_after_missing_paths_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    _mapping(tmp_path, (HOST,))
    credential, identity = _credential()

    def fail_missing(_root: Path) -> tuple[str, ...]:
        raise GitRepositoryError("status unavailable")

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.missing_submodules",
        fail_missing,
    )

    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo",
        root,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )

    assert any(step.step_id == "git.clone.submodule-update" for step in command.plan.process_steps)
    assert not any(
        step.step_id.startswith("git.clone.submodule-update.")
        for step in command.plan.process_steps
    )


def test_real_runtime_probe_parses_before_redaction_and_cleans_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "abcdef0123456789abcdef0123456789abcdef01"
    credential, _identity = _credential(token=token)
    options = git_frontier.environment((credential,), None)
    channels: list[Path] = []
    original_writer = cast(
        "Callable[[Sequence[GitLabCredentialContext]], str]",
        getattr(git_frontier, "write_askpass_channel"),
    )

    def recording_writer(values: Sequence[GitLabCredentialContext]) -> str:
        channel = Path(original_writer(values))
        channels.append(channel)
        return str(channel)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_frontier.write_askpass_channel", recording_writer
    )
    authoritative_capture = AuthoritativeProbeCapture()
    payload = f"{token}\trefs/heads/feature\n"
    step = proc.PreparedStep(
        step_id="git.sync.authoritative-remote-sha",
        argv=(sys.executable, "-c", f"import sys;sys.stdout.write({payload!r})"),
        environment=tuple(sorted(options["environment"].items())),
        environment_policy="explicit",
        runtime_environment_factory=options["runtime_environment"],
        secret_values=tuple(options["secrets"]),
        private_stdout_consumer=authoritative_capture.capture,
    )
    result = proc.SubprocessExecutor().execute(step)

    assert result.returncode == 0
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    assert "<redacted>" in stdout
    assert token not in stdout
    assert _planned_remote_sha(result, authoritative_capture) == token
    assert _remote_head(payload) == token
    assert _remote_head("not-a-ref\n") is None
    assert channels and all(not channel.exists() for channel in channels)


def test_runtime_non_probe_does_not_retain_unredacted_stdout() -> None:
    token = "runtime-non-probe-token"
    credential, _identity = _credential(token=token)
    options = git_frontier.environment((credential,), None)
    events: list[proc.StepEvent] = []
    payload = f"{token}\n"
    step = proc.PreparedStep(
        step_id="git.fetch",
        argv=(
            sys.executable,
            "-c",
            f"import sys;sys.stdout.write({payload!r});sys.stderr.write({payload!r});sys.exit(1)",
        ),
        environment=tuple(sorted(options["environment"].items())),
        environment_policy="explicit",
        runtime_environment_factory=options["runtime_environment"],
        secret_values=tuple(options["secrets"]),
    )
    result = proc.SubprocessExecutor().execute(step, observer=events.append, observe_output=True)
    serialized = asdict(result)

    assert result.returncode == 1
    assert token not in repr(result)
    assert token not in repr(serialized)
    assert token not in repr([asdict(event) for event in events])


def test_absent_clone_plan_is_dry_and_redacts_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential, identity = _credential()
    destination = tmp_path / "new-repository"

    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo.git",
        destination,
        member_id=MEMBER,
        credential=credential,
        identity=identity,
    )

    assert not destination.exists()
    clone = next(step for step in command.plan.process_steps if step.step_id == "git.clone")
    assert clone.argv[-2] == "https://<redacted>/team/repo"
    assert "token" not in repr(command.plan)

    def fail_if_planned(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("askpass channel must be created only during child execution")

    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_if_planned)
    recursive = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo.git",
        tmp_path / "recursive-repository",
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
    )

    assert not (tmp_path / "recursive-repository").exists()
    assert "ODCLI_GITLAB_ASKPASS_FILE" not in repr(recursive.plan)
    assert [step.step_id for step in recursive.plan.process_steps] == [
        "git.clone",
        "git.clone.submodule-status",
        "git.clone.submodule-update",
        "git.clone.submodule-frontier",
        "git.clone.submodule-update-recursive",
        "git.clone.submodule-status-final",
    ]


def test_existing_clone_reuses_branch_without_reclone_or_effect(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential()
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo",
        root,
        member_id=MEMBER,
        credential=credential,
        identity=identity,
    )

    assert not any(step.step_id == "git.clone" for step in command.plan.process_steps)
    result = command.run()
    assert result.reused is True
    assert _git(root, "branch", "--show-current").strip() == "feature"


def test_recursive_clone_rejects_hostile_gitmodules_before_update(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".gitmodules").write_text(
        '[submodule "vendor-lib"]\n\tpath = vendor/lib\n\turl = https://gitlab.example/team/evil\n\tupdate = !touch /tmp/should-not-run\n',
        encoding="utf-8",
    )
    credential, identity = _credential()

    with pytest.raises(GitRepositoryError, match="unsafe"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/repo",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
        )

    (root / ".gitmodules").write_text(
        '[submodule "self"]\n\tpath = .\n\turl = https://gitlab.example/team/self\n',
        encoding="utf-8",
    )
    with pytest.raises(GitRepositoryError, match="escapes"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/repo",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
        )

    (root / ".gitmodules").write_text(
        '[submodule "cycle"]\n\tpath = cycle\n\turl = https://gitlab.example/team/cycle\n',
        encoding="utf-8",
    )
    (root / "cycle").symlink_to(root, target_is_directory=True)
    with pytest.raises(PlanValidationError, match="repeats"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/repo",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
        )


def test_recursive_clone_requires_mapping_for_each_cross_host_submodule(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".gitmodules").write_text(
        '[submodule "other"]\n\tpath = other\n\turl = https://gitlab.other.example/team/other\n',
        encoding="utf-8",
    )
    credential, identity = _credential()

    with pytest.raises(GitLabCredentialError, match="unavailable"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/repo",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
        )

    _mapping(tmp_path, (HOST, OTHER_HOST))
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo",
        root,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    assert command.plan.observations
    assert any(step.step_id == "git.clone.submodule-update" for step in command.plan.process_steps)
    assert any(
        step.step_id == "git.clone.submodule-frontier" for step in command.plan.process_steps
    )
    assert any(
        step.step_id == "git.clone.submodule-update-recursive"
        for step in command.plan.process_steps
    )
    assert not any(
        step.step_id.startswith("git.clone.submodule-update.dynamic")
        for step in command.plan.process_steps
    )
    assert "token-1" not in repr(command.plan)


def test_absent_recursive_clone_uses_real_git_for_two_level_cross_host_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_source, child_source, leaf_source = _two_level_sources(tmp_path)
    credential, identity = _credential()
    _mapping(tmp_path, (HOST, OTHER_HOST, THIRD_HOST))
    executor, calls, environments = _real_clone_executor(root_source, child_source, leaf_source)
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)

    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/root",
        tmp_path / "checkout",
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    result = command.run()

    checkout = tmp_path / "checkout"
    assert result.initialized_submodules == ("child",)
    assert (checkout / "child" / "leaf" / ".git").exists()
    assert _git(checkout, "submodule", "status", "--recursive").count("-") == 0
    assert calls == [
        "git.clone",
        "git.clone.submodule-status",
        "git.clone.submodule-update",
        "git.clone.submodule-frontier",
        "git.clone.submodule-update-recursive",
        "git.clone.submodule-status-final",
    ]
    planned = {
        step.step_id: step
        for step in command.plan.process_steps
        if step.step_id.startswith("git.clone.submodule-update")
    }
    assert set(planned) == {
        "git.clone.submodule-update",
        "git.clone.submodule-update-recursive",
    }
    for environment in environments:
        assert "token-1" not in environment.values()
        assert "token-2" not in environment.values()
    update_environments = environments[1::2]
    assert all("token" not in environment.values() for environment in update_environments)
    assert all("GIT_ASKPASS" in environment for environment in update_environments)
    assert all("token-0" not in environment.values() for environment in update_environments[1:])


def test_absent_recursive_clone_rejects_nested_hostile_config_before_real_nested_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_source, child_source, leaf_source, deepest_source, ultimate_source = _recursive_sources(
        tmp_path, hostile=True
    )
    credential, identity = _credential()
    _mapping(tmp_path, (HOST, OTHER_HOST, THIRD_HOST))
    executor, calls, _environments = _real_clone_executor(
        root_source,
        child_source,
        leaf_source,
        deepest_source,
        ultimate_source,
        result_policy=_generic_nested_result_policy,
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)

    with pytest.raises(GitWorkflowError, match="unsafe"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/root",
            tmp_path / "checkout",
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
            identity_transport=_identity_transport,
        ).run()

    checkout = tmp_path / "checkout"
    assert (checkout / "child" / ".git").exists()
    assert not (checkout / "child" / "leaf" / "vendor" / "lib" / ".git").exists()
    assert "git.clone.submodule-update" in calls
    assert "git.clone.submodule-frontier" in calls
    assert "git.clone.submodule-update-recursive" not in calls


def test_absent_recursive_clone_rejects_unmapped_deepest_frontier_before_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_source, child_source, leaf_source, deepest_source, ultimate_source = _recursive_sources(
        tmp_path
    )
    credential, identity = _credential()
    _mapping(tmp_path, (HOST, OTHER_HOST))
    executor, calls, _environments = _real_clone_executor(
        root_source,
        child_source,
        leaf_source,
        deepest_source,
        ultimate_source,
        result_policy=_generic_nested_result_policy,
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)

    with pytest.raises(GitWorkflowError, match="unavailable"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/root",
            tmp_path / "checkout",
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
            identity_transport=_identity_transport,
        ).run()

    checkout = tmp_path / "checkout"
    assert (checkout / "child" / "leaf" / ".git").exists()
    assert not (checkout / "child" / "leaf" / "vendor" / "lib" / ".git").exists()
    assert "git.clone.submodule-frontier" in calls
    assert "git.clone.submodule-update-recursive" not in calls


def test_recursive_clone_accepts_git_relative_sibling_override_and_rejects_project_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ours = tmp_path / "ours-source"
    _repo(ours, origin="https://gitlab.example/team/ours-addon")
    odoo = tmp_path / "odoo-source"
    _repo(odoo, origin="https://gitlab.example/team/odoo-addon")
    root = tmp_path / "b-source"
    _repo(root, origin="https://gitlab.example/team/b")
    _add_local_submodule(root, ours, "ours", "../ours-addon.git")
    _add_local_submodule(root, odoo, "odoo", "../odoo-addon.git")
    _git(root, "config", "submodule.ours.url", "https://gitlab.example/team/ours-addon.git")
    _git(root, "config", "submodule.odoo.url", "https://gitlab.example/team/odoo-addon.git")

    matching = git_repository.validate_submodule_configuration(root, allowed_hosts=(HOST,))
    assert matching == (
        ("ours", "https://gitlab.example/team/ours-addon", HOST),
        ("odoo", "https://gitlab.example/team/odoo-addon", HOST),
    )

    credential, identity = _credential()
    _mapping(tmp_path, (HOST,))
    channel_calls: list[object] = []
    monkeypatch.setattr(
        git_frontier,
        "write_askpass_channel",
        lambda *args, **kwargs: channel_calls.append((args, kwargs)),
    )
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/b",
        root,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    assert command.plan.process_steps

    _git(root, "config", "submodule.odoo.url", "https://gitlab.example/team/other.git")
    with pytest.raises(GitRepositoryError, match="unsafe"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/b",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
            identity_transport=_identity_transport,
        )
    assert channel_calls == []


def test_submodule_name_cannot_inject_a_command_config_value(tmp_path: Path) -> None:
    root = tmp_path / "root"
    child = tmp_path / "child"
    _repo(root)
    _repo(child)
    _add_local_submodule(
        root,
        child,
        "child",
        "https://gitlab.example/team/child",
        logical_name="other.url=https://unmapped.example",
    )
    _git(root, "config", "--unset-all", "submodule.child.url")
    with pytest.raises(GitRepositoryError, match="logical name"):
        git_repository.submodule_update_arguments(root, "child", allowed_hosts=(HOST,))


@pytest.mark.parametrize("target", ["directory", "config"])
def test_cached_submodule_symlinks_are_rejected(tmp_path: Path, target: str) -> None:
    root, _child, _leaf = _two_level_sources(tmp_path)
    _git(root, "submodule", "deinit", "-f", "child")
    cached = root / _git(root, "rev-parse", "--git-path", "modules/child").strip()
    selected = cached if target == "directory" else cached / "config"
    moved = tmp_path / "external-cache-target"
    selected.rename(moved)
    selected.symlink_to(moved, target_is_directory=target == "directory")
    with pytest.raises(GitRepositoryError, match="cached submodule directory is unsafe"):
        git_repository.submodule_update_arguments(root, "child", allowed_hosts=(HOST, OTHER_HOST))


def test_cached_submodule_origin_must_be_unambiguous(tmp_path: Path) -> None:
    root, _child, _leaf = _two_level_sources(tmp_path)
    _git(root, "submodule", "deinit", "-f", "child")
    cached = root / _git(root, "rev-parse", "--git-path", "modules/child").strip()
    _git(
        root,
        "--git-dir",
        str(cached),
        "config",
        "--local",
        "--add",
        "remote.origin.url",
        "https://gitlab.other.example/team/child",
    )
    with pytest.raises(GitRepositoryError, match="ambiguous"):
        git_repository.submodule_update_arguments(root, "child", allowed_hosts=(HOST, OTHER_HOST))


def test_generic_commit_uses_staged_scope_and_verified_identity(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / "staged.txt").write_text("staged\n", encoding="utf-8")
    (root / "unstaged.txt").write_text("unstaged\n", encoding="utf-8")
    _git(root, "add", "staged.txt")
    credential, identity = _credential()

    command = GitResource(_instance(tmp_path)).generic_commit_command(
        "make the change",
        repository=root,
        member_id=MEMBER,
        ticket="PROJ-12",
        credential=credential,
        identity=identity,
    )
    commit_step = next(
        step for step in command.plan.process_steps if step.step_id == "git.generic-commit"
    )
    assert commit_step.argv[-2:] == ("-m", "PROJ-12: make the change")
    assert "GIT_AUTHOR_NAME" in dict(commit_step.environment_overrides)
    result = command.run()

    assert result.subject == "PROJ-12: make the change"
    assert (
        _git(root, "show", "--format=%s", "--name-only", "--no-renames", "-1").find("staged.txt")
        >= 0
    )
    assert "unstaged.txt" not in _git(root, "show", "--format=", "--name-only", "-1")


def test_initialized_odoo_dispatch_keeps_scope_and_selected_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(root, "add", ".odcli/project.toml")
    _git(root, "commit", "-qm", "project config")
    module = root / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "models.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "addons/sale")
    _git(root, "config", "user.name", "Ambient User")
    _git(root, "config", "user.email", "ambient@example.test")
    _credential_context, selected_identity = _credential()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_explicit.resolve_credentials",
        lambda *_args, **_kwargs: (_credential_context, selected_identity),
    )

    command = GitResource(_instance(root)).commit_command(
        description="add a model",
        ticket="PROJ-12",
        repository=root,
        member_id=MEMBER,
    )
    commit_step = next(step for step in command.plan.process_steps if step.step_id == "git.commit")
    assert commit_step.argv[-1].startswith("[ADD] sale: PROJ-12 add a model")
    private_commit_step = cast(
        "proc.PreparedStep",
        next(step for step in command._prepared().steps if step.step_id == "git.commit"),
    )
    commit_environment = dict(private_commit_step.environment)
    assert commit_environment["GIT_AUTHOR_NAME"] == selected_identity.name
    assert commit_environment["GIT_AUTHOR_EMAIL"] == selected_identity.email
    assert commit_environment["GIT_COMMITTER_NAME"] == selected_identity.name
    assert commit_environment["GIT_COMMITTER_EMAIL"] == selected_identity.email
    command.run()
    assert (
        _git(root, "show", "-s", "--format=%an <%ae> %cn <%ce>").strip()
        == "Alice Example <alice@example.test> Alice Example <alice@example.test>"
    )


def test_generic_sync_plan_captures_branch_lease_and_rejects_stale_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential()
    remote_sha = "a" * 40
    fail_integrate = False
    calls: list[str] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            nonlocal fail_integrate
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            stdout = ""
            returncode = 0
            if step_id == "git.sync.remote":
                stdout = "https://gitlab.example/team/repo\n"
            elif step_id == "git.sync.authoritative-remote-sha":
                stdout = f"{remote_sha}\trefs/heads/feature\n"
            elif step_id == "git.sync.branch":
                stdout = "feature\n"
            elif step_id == "git.sync.upstream":
                stdout = "origin/feature\n"
            elif step_id == "git.sync.fetched-sha":
                stdout = f"{remote_sha}\n"
            elif step_id == "git.sync.integrate" and fail_integrate:
                returncode = 1
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=returncode,
                stdout=stdout,
                stderr="",
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
    lease = next(
        step for step in command.plan.process_steps if step.step_id == "git.sync.push-lease"
    )
    assert any(
        argument.startswith("--force-with-lease=refs/heads/feature:") for argument in lease.argv
    )
    assert any(step.step_id == "git.sync.integrate" for step in command.plan.process_steps)

    fail_integrate = True
    with pytest.raises(GitSyncError, match="rebase conflict"):
        command.run()
    assert "git.sync.push-lease" not in calls

    _git(root, "remote", "set-url", "origin", "https://gitlab.example/team/changed")
    fail_integrate = False
    with pytest.raises(GitSyncError, match="origin or branch changed"):
        command.run()


def test_generic_sync_does_not_mutate_parent_gitlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = tmp_path / "child"
    _repo(child)
    parent = tmp_path / "parent"
    _repo(parent)
    _git(parent, "-c", "protocol.file.allow=always", "submodule", "add", str(child), "child")
    _git(child, "remote", "set-url", "origin", "https://gitlab.example/team/child")
    _git(parent, "commit", "-qm", "add child")
    parent_head = _git(parent, "rev-parse", "HEAD").strip()
    parent_index = _git(parent, "write-tree").strip()

    credential, identity = _credential()
    remote_sha = "a" * 40

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            stdout = ""
            if step_id == "git.sync.remote":
                stdout = "https://gitlab.example/team/child\n"
            elif step_id == "git.sync.authoritative-remote-sha":
                stdout = f"{remote_sha}\trefs/heads/feature\n"
            elif step_id == "git.sync.branch":
                stdout = "feature\n"
            elif step_id == "git.sync.upstream":
                stdout = "origin/feature\n"
            elif step_id == "git.sync.fetched-sha":
                stdout = f"{remote_sha}\n"
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
    GitResource(_instance(tmp_path)).generic_sync_command(
        repository=child,
        member_id=MEMBER,
        base="main",
        push=False,
        credential=credential,
        identity=identity,
    ).run()

    assert _git(parent, "rev-parse", "HEAD").strip() == parent_head
    assert _git(parent, "write-tree").strip() == parent_index
