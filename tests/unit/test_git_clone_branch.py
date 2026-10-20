from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.exceptions import GitWorkflowError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal import proc
from odoo_instance_sdk.models import GitCloneResult, GitLabCredentialContext, GitLabIdentity
from odoo_instance_sdk.resources.git import GitResource
from tests.unit.git_test_support import (
    HOST,
    MEMBER,
    OTHER_HOST,
    _credential,
    _git,
    _identity_transport,
    _instance,
    _mapping,
    _real_clone_executor,
    _repo,
    _two_level_sources,
)


def _branch_clone_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str | None
) -> tuple[Command[GitCloneResult], Path, list[str], str, str, str | None]:
    source, child, leaf = _two_level_sources(tmp_path)
    old_child_sha = _git(child, "rev-parse", "HEAD").strip()
    _git(source, "switch", "-c", "dev")
    (child / "new.txt").write_text("new child version\n")
    _git(child, "add", "new.txt")
    _git(child, "commit", "-qm", "new child version")
    new_child_sha = _git(child, "rev-parse", "HEAD").strip()
    _git(source / "child", "fetch", str(child), "feature")
    _git(source / "child", "checkout", "--detach", new_child_sha)
    _git(source, "add", "child")
    _git(source, "commit", "-qm", "dev child pin")
    _git(source, "branch", "topic/nested")
    _git(source, "switch", "feature")
    executor, calls, _environments = _real_clone_executor(source, child, leaf)
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)
    _mapping(tmp_path, (HOST, OTHER_HOST))
    credential, identity = _credential()
    checkout = tmp_path / "checkout"
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/root",
        checkout,
        member_id=MEMBER,
        branch=branch,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    assert not checkout.exists()
    return command, checkout, calls, old_child_sha, new_child_sha, branch


def test_clone_missing_branch_fails_before_recursive_submodules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command, checkout, calls, _old_child_sha, _new_child_sha, _branch = _branch_clone_command(
        tmp_path, monkeypatch, "missing"
    )
    with pytest.raises(GitWorkflowError, match="git clone failed"):
        command.run()
    assert calls == ["git.clone"]
    assert not checkout.exists()


@pytest.mark.parametrize("branch", ["dev", "topic/nested", None])
def test_clone_selects_branch_before_recursive_submodules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str | None
) -> None:
    command, checkout, _calls, old_child_sha, new_child_sha, branch = _branch_clone_command(
        tmp_path, monkeypatch, branch
    )
    command.run()
    assert _git(checkout, "branch", "--show-current").strip() == (branch or "feature")
    assert _git(checkout / "child", "rev-parse", "HEAD").strip() == (
        new_child_sha if branch else old_child_sha
    )
    assert (checkout / "child" / "leaf" / ".git").exists()
    assert _git(checkout, "rev-parse", "--is-shallow-repository").strip() == "false"
    assert _git(checkout / "child", "rev-parse", "--is-shallow-repository").strip() == "false"
    assert "refs/remotes/origin/feature" in _git(checkout, "for-each-ref", "--format=%(refname)")


def _existing_clone_resource(
    tmp_path: Path,
) -> tuple[Path, GitLabCredentialContext, GitLabIdentity, GitResource]:
    root = tmp_path / "checkout"
    _repo(root)
    (root / "local.txt").write_text("keep this change\n")
    credential, identity = _credential()
    return root, credential, identity, GitResource(_instance(tmp_path))


def test_existing_clone_branch_mismatch_is_rejected_without_mutation(tmp_path: Path) -> None:
    root, credential, identity, resource = _existing_clone_resource(tmp_path)
    with pytest.raises(GitWorkflowError, match="branch"):
        resource.clone(
            "https://gitlab.example/team/repo",
            root,
            member_id=MEMBER,
            branch="dev",
            credential=credential,
            identity=identity,
        )
    assert _git(root, "branch", "--show-current").strip() == "feature"
    assert (root / "local.txt").read_text() == "keep this change\n"


@pytest.mark.parametrize("branch", ["feature", None])
def test_existing_clone_reuse_preserves_branch_and_local_file(
    tmp_path: Path, branch: str | None
) -> None:
    root, credential, identity, resource = _existing_clone_resource(tmp_path)
    assert resource.clone(
        "https://gitlab.example/team/repo",
        root,
        member_id=MEMBER,
        branch=branch,
        credential=credential,
        identity=identity,
    ).reused
    assert _git(root, "branch", "--show-current").strip() == "feature"
    assert (root / "local.txt").read_text() == "keep this change\n"


@pytest.mark.parametrize("branch", ["", "--upload-pack=evil", "dev\nother"])
def test_invalid_clone_branch_fails_before_mutation(tmp_path: Path, branch: str) -> None:
    credential, identity = _credential()
    checkout = tmp_path / "checkout"
    with pytest.raises(GitWorkflowError, match="branch"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/repo",
            checkout,
            member_id=MEMBER,
            branch=branch,
            credential=credential,
            identity=identity,
        )
    assert not checkout.exists()


def test_cli_clone_passes_branch_to_public_sdk(tmp_path: Path) -> None:
    resource = Mock()
    resource.clone_command.return_value = SimpleNamespace(
        plan=ExecutionPlan(steps=()).with_fingerprint()
    )
    with patch("odoo_instance_sdk.commands.git._generic_resource", return_value=resource):
        result = CliRunner().invoke(
            cli,
            [
                "git",
                "clone",
                "https://gitlab.example/team/repo",
                str(tmp_path / "checkout"),
                "--member-id",
                MEMBER,
                "--branch",
                "dev",
                "--recurse-submodules",
                "--dry-run",
                "--format",
                "json",
            ],
        )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["dry_run"] is True
    resource.clone_command.assert_called_once_with(
        "https://gitlab.example/team/repo",
        tmp_path / "checkout",
        member_id=MEMBER,
        branch="dev",
        recurse_submodules=True,
    )
