from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest

from odoo_instance_sdk.exceptions import GitLabCredentialError, GitRepositoryError, GitWorkflowError
from odoo_instance_sdk.internal import git_frontier, proc
from odoo_instance_sdk.internal.gitlab_credentials import write_askpass_channel
from odoo_instance_sdk.models import GitLabCredentialContext, GitLabIdentity
from odoo_instance_sdk.resources.git import GitResource
from tests.unit.git_test_support import (
    HOST,
    MEMBER,
    OTHER_HOST,
    _add_local_submodule,
    _credential,
    _git,
    _identity_transport,
    _instance,
    _mapping,
    _real_clone_executor,
    _repo,
    _two_level_sources,
)

_SSH_TRANSPORT_CASES: tuple[tuple[str, str, str, str, bool], ...] = (
    (
        "scp",
        "git@gitlab.other.example:team/child.git",
        "git@gitlab.example:team/leaf.git",
        "https://gitlab.example/team/leaf",
        False,
    ),
    (
        "ssh",
        "ssh://git@gitlab.other.example/team/child.git",
        "ssh://git@gitlab.example/team/leaf.git",
        "https://gitlab.example/team/leaf",
        False,
    ),
    (
        "ssh-port",
        "ssh://git@gitlab.other.example:22/team/child.git",
        "../leaf.git",
        "https://gitlab.other.example/team/leaf",
        True,
    ),
)


def _prepare_ssh_transport_case(
    root: Path,
    child: Path,
    case: tuple[str, str, str, str, bool],
) -> tuple[str, str]:
    _style, child_url, leaf_url, _leaf_origin, move_nested = case
    nested_path = "vendor/lib" if move_nested else "leaf"
    if move_nested:
        (child / "vendor").mkdir()
        _git(child, "mv", "leaf", nested_path)
    _git(child, "config", "-f", ".gitmodules", "submodule.leaf.url", leaf_url)
    _git(child, "add", ".gitmodules", nested_path)
    _git(child, "commit", "-qm", "nested transport")
    child_sha = _git(child, "rev-parse", "HEAD").strip()
    _git(root / "child", "fetch", str(child), "feature")
    _git(root / "child", "checkout", "--detach", child_sha)
    _git(
        root,
        "config",
        "-f",
        ".gitmodules",
        "--rename-section",
        "submodule.child",
        "submodule.platform",
    )
    _git(root, "config", "-f", ".gitmodules", "submodule.platform.url", child_url)
    _git(root, "add", ".gitmodules", "child")
    _git(root, "commit", "-qm", "SSH child transport")
    return nested_path, child_url


@pytest.mark.parametrize(
    "case", _SSH_TRANSPORT_CASES, ids=[case[0] for case in _SSH_TRANSPORT_CASES]
)
def test_recursive_clone_uses_https_for_ssh_submodules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, str, str, bool],
) -> None:
    root, child, leaf = _two_level_sources(tmp_path)
    nested_path, child_url = _prepare_ssh_transport_case(root, child, case)
    executor, calls, environments = _real_clone_executor(root, child, leaf)
    delivered: list[tuple[tuple[str, str, str], ...]] = []
    write_channel = write_askpass_channel

    def record_channel(values: Sequence[GitLabCredentialContext]) -> str:
        delivered.append(tuple((v.identity.user_id, v.host, v._token) for v in values))
        return write_channel(values)

    monkeypatch.setattr(git_frontier, "write_askpass_channel", record_channel)
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)
    _mapping(tmp_path, (HOST, OTHER_HOST))
    credential, identity = _credential()
    checkout = tmp_path / "checkout"
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/root",
        checkout,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    command.run()
    assert (checkout / "child" / nested_path / ".git").exists()
    assert (
        _git(checkout / "child", "config", "remote.origin.url").strip()
        == "https://gitlab.other.example/team/child"
    )
    assert _git(checkout / "child" / nested_path, "config", "remote.origin.url").strip() == case[3]
    assert (
        _git(checkout, "config", "-f", ".gitmodules", "submodule.platform.url").strip() == child_url
    )
    assert "git.clone.submodule-update-recursive" in calls
    assert all("GITLAB_TOKEN" not in env for env in environments)
    assert any((MEMBER, OTHER_HOST, "token-1") in values for values in delivered)
    assert any((MEMBER, HOST, "token") in values for values in delivered)
    assert all(user == MEMBER for values in delivered for user, _host, _token in values)


@pytest.mark.parametrize("ssh_style", ["scp", "ssh", "ssh-port"])
def test_recursive_clone_reuses_existing_checkout_without_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ssh_style: str
) -> None:
    root, child, leaf = _two_level_sources(tmp_path)
    _mapping(tmp_path, (HOST, OTHER_HOST))
    executor, calls, _environments = _real_clone_executor(root, child, leaf)
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)
    credential, identity = _credential()
    checkout = tmp_path / "checkout"
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/root",
        checkout,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    command.run()
    previous_calls = len(calls)
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/root",
        checkout,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    assert command.run().reused
    assert all("update" not in call for call in calls[previous_calls:])


def _cached_ssh_retry_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached_url: str
) -> tuple[Path, list[str], str, GitLabCredentialContext, GitLabIdentity]:
    root, child, leaf = _two_level_sources(tmp_path)
    _git(root, "config", "submodule.child.url", "git@gitlab.other.example:team/child.git")
    _git(root / "child", "remote", "set-url", "origin", cached_url)
    _git(
        root,
        "config",
        "-f",
        ".gitmodules",
        "submodule.child.url",
        "git@gitlab.other.example:team/child.git",
    )
    _git(root, "submodule", "deinit", "-f", "child")
    (child / "new.txt").write_text("new pin not in cached checkout\n")
    _git(child, "add", "new.txt")
    _git(child, "commit", "-qm", "new pin")
    sha = _git(child, "rev-parse", "HEAD").strip()
    _git(root, "update-index", "--cacheinfo", f"160000,{sha},child")
    _git(root, "add", ".gitmodules")
    _git(root, "commit", "-qm", "new SSH pin")
    executor, calls, _environments = _real_clone_executor(root, child, leaf)
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)
    monkeypatch.setenv("GIT_SSH_COMMAND", "false")
    _mapping(tmp_path, (HOST, OTHER_HOST))
    credential, identity = _credential()
    return root, calls, sha, credential, identity


@pytest.mark.parametrize(
    "url",
    [
        "ssh://git@gitlab.example:2222/team/child.git",
        "ssh://other@gitlab.example/team/child.git",
        "git@unmapped.example:team/child.git",
        "git@gitlab.example:/srv/child.git",
        "ssh://git@gitlab.example//srv/child.git",
        "git@gitlab.example:~/child.git",
        "ssh://git@gitlab.example/~git/child.git",
        "ssh://git@gitlab.example/%2Fsrv/child.git",
        "ssh://git@gitlab.example/%7Egit/child.git",
    ],
)
def test_recursive_clone_rejects_unsupported_or_unmapped_ssh_before_update(
    tmp_path: Path, url: str
) -> None:
    root = tmp_path / "root"
    child = tmp_path / "child"
    _repo(root)
    _repo(child)
    _add_local_submodule(root, child, "child", url)
    _git(root, "submodule", "deinit", "-f", "child")
    _mapping(tmp_path, (HOST,))
    credential, identity = _credential()
    with pytest.raises((GitRepositoryError, GitLabCredentialError)):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/repo",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
            identity_transport=_identity_transport,
        )
    assert not (root / "child" / ".git").exists()


def test_retry_preserves_populated_ssh_parent_when_initializing_relative_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, child, leaf = _two_level_sources(tmp_path)
    checkout = root / "child"
    _git(root, "config", "submodule.child.url", "https://gitlab.other.example/team/child")
    _git(checkout, "remote", "set-url", "origin", "git@gitlab.other.example:team/child.git")
    _git(checkout, "config", "user.email", "tests@example.test")
    _git(checkout, "config", "user.name", "tests")
    _git(checkout, "config", "-f", ".gitmodules", "submodule.leaf.url", "../leaf.git")
    _git(checkout, "add", ".gitmodules")
    _git(checkout, "commit", "-qm", "local relative child")
    original_sha = _git(checkout, "rev-parse", "HEAD").strip()
    executor, _calls, _environments = _real_clone_executor(root, child, leaf)
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)
    _mapping(tmp_path, (HOST, OTHER_HOST))
    credential, identity = _credential()
    GitResource(_instance(tmp_path)).clone(
        "https://gitlab.example/team/root",
        root,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    assert _git(checkout, "rev-parse", "HEAD").strip() == original_sha
    assert _git(checkout, "config", "remote.origin.url").strip().startswith("git@")
    assert (checkout / "leaf" / ".git").exists()
    assert (
        _git(checkout / "leaf", "config", "remote.origin.url").strip()
        == "https://gitlab.other.example/team/leaf"
    )


@pytest.mark.parametrize(
    ("cached_url", "error"),
    [
        ("git@unmapped.example:team/child.git", "cached submodule origin"),
        ("git@gitlab.other.example:team/other.git", "cached submodule origin"),
        ("ssh://git@gitlab.other.example:2222/team/child.git", "SSH URL"),
    ],
)
def test_retry_fetches_cached_ssh_submodule_with_https(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached_url: str, error: str | None
) -> None:
    root, calls, _sha, credential, identity = _cached_ssh_retry_setup(
        tmp_path, monkeypatch, cached_url
    )
    with pytest.raises(GitWorkflowError, match=error):
        GitResource(_instance(tmp_path)).clone(
            "https://gitlab.example/team/root",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
            identity_transport=_identity_transport,
        )
    assert not any("submodule-update" in call for call in calls)


@pytest.mark.parametrize(
    "cached_url",
    [
        "git@gitlab.other.example:team/child.git",
        "git@GitLab.Other.Example:team/child.git",
        "ssh://git@GitLab.Other.Example:22/team/child.git",
    ],
)
def test_retry_fetches_valid_cached_ssh_submodule_with_https(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached_url: str
) -> None:
    root, _calls, sha, credential, identity = _cached_ssh_retry_setup(
        tmp_path, monkeypatch, cached_url
    )
    GitResource(_instance(tmp_path)).clone(
        "https://gitlab.example/team/root",
        root,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    assert (root / "child" / "new.txt").exists()
    assert _git(root / "child", "rev-parse", "HEAD").strip() == sha


def test_changed_submodule_host_is_rejected_before_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, child, leaf = _two_level_sources(tmp_path)
    _git(root, "submodule", "deinit", "-f", "child")
    _mapping(tmp_path, (HOST, OTHER_HOST))
    credential, identity = _credential()
    executor, calls, _environments = _real_clone_executor(root, child, leaf)
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)

    def change_after_validation(
        *args: object, **kwargs: object
    ) -> tuple[GitLabCredentialContext, ...]:
        result = cast(
            "tuple[GitLabCredentialContext, ...]",
            getattr(git_frontier, "validated_frontier_credentials")(*args, **kwargs),
        )
        _git(
            root,
            "config",
            "-f",
            ".gitmodules",
            "submodule.child.url",
            "https://anonymous.example/team/child",
        )
        return result

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.validated_frontier_credentials",
        change_after_validation,
    )
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/root",
        root,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    with pytest.raises(GitWorkflowError, match="mapping"):
        command.run()
    assert not any("submodule-update" in call for call in calls)
