from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.internal import proc
from odoo_instance_sdk.internal.gitlab_credentials import write_askpass_channel
from odoo_instance_sdk.models import GitLabCredentialContext
from odoo_instance_sdk.resources.git import GitResource
from tests.unit.git_test_support import (
    HOST,
    MEMBER,
    OTHER_HOST,
    THIRD_HOST,
    _add_local_submodule,
    _credential,
    _credential_resolver,
    _credential_resolver_for_hosts,
    _identity_transport,
    _instance,
    _mapping,
    _real_clone_executor,
    _recursive_sources,
    _repo,
)

if TYPE_CHECKING:
    import pytest


def test_recursive_clone_does_not_resolve_unused_same_member_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    _mapping(tmp_path, (HOST, THIRD_HOST))
    credential, identity = _credential()
    requested_hosts: list[str] = []
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        _credential_resolver(credential, identity, requested_hosts),
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

    assert requested_hosts == [HOST]
    assert command.plan.process_steps[0].step_id == "git.clone.submodule-status"


def test_recursive_clone_runs_selected_host_frontier_without_stale_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_source = tmp_path / "root-source"
    child_source = tmp_path / "child-source"
    _repo(root_source, origin="https://gitlab.example/team/root")
    _repo(child_source, origin="https://gitlab.example/team/child")
    _add_local_submodule(root_source, child_source, "child", "https://gitlab.example/team/child")
    leaf_source = tmp_path / "leaf-source"
    _repo(leaf_source, origin="https://gitlab.example/team/leaf")
    _mapping(tmp_path, (HOST, THIRD_HOST))
    credential, identity = _credential()
    requested_hosts: list[str] = []
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        _credential_resolver(credential, identity, requested_hosts),
    )
    executor, calls, _environments = _real_clone_executor(root_source, child_source, leaf_source)
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)

    result = (
        GitResource(_instance(tmp_path))
        .clone_command(
            "https://gitlab.example/team/root",
            tmp_path / "checkout",
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
            identity_transport=_identity_transport,
        )
        .run()
    )

    assert result.initialized_submodules == ("child",)
    assert (tmp_path / "checkout" / "child" / ".git").exists()
    assert calls == [
        "git.clone",
        "git.clone.submodule-status",
        "git.clone.submodule-update",
        "git.clone.submodule-frontier",
        "git.clone.submodule-update-recursive",
        "git.clone.submodule-status-final",
    ]
    assert requested_hosts == [HOST]


def test_recursive_clone_delivers_new_deep_frontier_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_source, child_source, leaf_source, deepest_source, ultimate_source = _recursive_sources(
        tmp_path
    )
    credential, identity = _credential()
    other_credential, other_identity = _credential(OTHER_HOST, login="user-1", token="token-1")
    third_credential, third_identity = _credential(THIRD_HOST, login="user-2", token="token-2")
    _mapping(tmp_path, (HOST, OTHER_HOST, THIRD_HOST))
    requested_hosts: list[str] = []
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        _credential_resolver_for_hosts(
            {
                HOST: (credential, identity),
                OTHER_HOST: (other_credential, other_identity),
                THIRD_HOST: (third_credential, third_identity),
            },
            requested_hosts,
        ),
    )
    executor, calls, _environments = _real_clone_executor(
        root_source, child_source, leaf_source, deepest_source, ultimate_source
    )
    delivered: list[tuple[tuple[str, str], ...]] = []
    write_channel = write_askpass_channel

    def record_channel(values: Sequence[GitLabCredentialContext]) -> str:
        delivered.append(tuple((value.host, value._token) for value in values))
        return write_channel(values)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_frontier.write_askpass_channel", record_channel
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)

    result = (
        GitResource(_instance(tmp_path))
        .clone_command(
            "https://gitlab.example/team/root",
            tmp_path / "checkout",
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
            identity_transport=_identity_transport,
        )
        .run()
    )

    assert result.initialized_submodules == ("child",)
    assert calls == [
        "git.clone",
        "git.clone.submodule-status",
        "git.clone.submodule-update",
        "git.clone.submodule-frontier",
        "git.clone.submodule-update-recursive",
        "git.clone.submodule-status-final",
    ]
    assert delivered == [
        ((HOST, "token"),),
        ((OTHER_HOST, "token-1"),),
        ((HOST, "token"), (OTHER_HOST, "token-1")),
        ((HOST, "token"), (OTHER_HOST, "token-1"), (THIRD_HOST, "token-2")),
        ((HOST, "token"),),
    ]
    assert requested_hosts == [HOST]


def test_recursive_clone_plan_tolerates_stale_unused_same_member_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    _mapping(tmp_path, (HOST, THIRD_HOST))
    credential, identity = _credential()
    requested_hosts: list[str] = []

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git_generic.resolve_credentials",
        _credential_resolver(credential, identity, requested_hosts),
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

    assert requested_hosts == [HOST]
    assert command.plan.process_steps[0].step_id == "git.clone.submodule-status"
