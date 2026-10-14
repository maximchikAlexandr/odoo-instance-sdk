from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    GitLabCredentialError,
    GitRepositoryError,
    GitSyncError,
    GitWorkflowError,
)
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal import proc
from odoo_instance_sdk.internal.gitlab_credentials import ensure_credential_directory
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
OTHER_HOST = "gitlab.other.example"


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
    (root / "README.md").write_text("base\n", encoding="utf-8")
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


def _mapping(root: Path, hosts: tuple[str, ...]) -> None:
    directory = ensure_credential_directory(root)
    entries = []
    values = []
    for index, host in enumerate(hosts):
        key = f"ODCLI_GITLAB_TOKEN_{index}"
        entries.append(
            "[[mappings]]\n"
            f'user_id = "{MEMBER}"\n'
            f'host = "{host}"\n'
            f'login = "user-{index}"\n'
            f'token_key = "{key}"\n'
        )
        values.append(f"{key}=token-{index}")
    (directory / "gitlab-credentials.toml").write_text("\n".join(entries), encoding="utf-8")
    (directory / "gitlab-credentials.toml").chmod(0o600)
    (directory / ".env").write_text("\n".join(values) + "\n", encoding="utf-8")
    (directory / ".env").chmod(0o600)


def _identity_transport(host: str, _token: str) -> dict[str, JsonValue]:
    return {
        "id": 7,
        "username": "user-0" if host == HOST else "user-1",
        "name": "Selected Member",
        "commit_email": "selected@example.test",
    }


def test_absent_clone_plan_is_dry_and_redacts_credentials(tmp_path: Path) -> None:
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
        '[submodule "evil"]\n\tpath = evil\n\turl = https://gitlab.example/team/evil\n\tupdate = !touch /tmp/should-not-run\n',
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
    update = next(
        step for step in command.plan.process_steps if step.step_id == "git.clone.submodule-update"
    )
    assert any(argument.startswith("credential.helper=") for argument in update.argv)
    assert "token-1" not in repr(command.plan)


def test_absent_recursive_clone_validates_hosts_before_submodule_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential, identity = _credential()
    update_called = False

    class FakeExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            nonlocal update_called
            step_id = getattr(step, "step_id")
            if step_id == "git.clone":
                destination = Path(getattr(step, "argv")[-1])
                _repo(destination)
                (destination / ".gitmodules").write_text(
                    '[submodule "other"]\n\tpath = other\n\turl = https://gitlab.other.example/team/other\n',
                    encoding="utf-8",
                )
            if step_id == "git.clone.submodule-update":
                update_called = True
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout="",
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(proc, "SubprocessExecutor", FakeExecutor)
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo",
        tmp_path / "new-repository",
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
    )

    with pytest.raises(GitWorkflowError, match=r"credential|host-specific"):
        command.run()
    assert update_called is False


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


def test_initialized_odoo_dispatch_keeps_scope_and_tag_policy(tmp_path: Path) -> None:
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

    command = GitResource(_instance(root)).commit_command(
        description="add a model",
        ticket="PROJ-12",
        repository=root,
        member_id=MEMBER,
    )
    commit_step = next(step for step in command.plan.process_steps if step.step_id == "git.commit")
    assert commit_step.argv[-1].startswith("[ADD] sale: PROJ-12 add a model")


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


def test_child_commit_does_not_mutate_parent_gitlink(tmp_path: Path) -> None:
    child = tmp_path / "child"
    _repo(child)
    parent = tmp_path / "parent"
    _repo(parent)
    _git(parent, "-c", "protocol.file.allow=always", "submodule", "add", str(child), "child")
    _git(child, "remote", "set-url", "origin", "https://gitlab.example/team/child")
    _git(parent, "commit", "-qm", "add child")
    parent_head = _git(parent, "rev-parse", "HEAD").strip()
    parent_index = _git(parent, "write-tree").strip()

    (child / "change.txt").write_text("change\n", encoding="utf-8")
    _git(child, "add", "change.txt")
    credential, identity = _credential()
    GitResource(_instance(tmp_path)).generic_commit_command(
        "child change",
        repository=child,
        member_id=MEMBER,
        credential=credential,
        identity=identity,
    ).run()

    assert _git(parent, "rev-parse", "HEAD").strip() == parent_head
    assert _git(parent, "write-tree").strip() == parent_index
