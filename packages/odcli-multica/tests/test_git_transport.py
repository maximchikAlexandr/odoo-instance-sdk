from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import msgspec
import pytest
from multica_py import Issue
from odcli_multica.client import MulticaOdooClient
from odcli_multica.models import (
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    MulticaCompatibility,
    VerifiedTaskContext,
)

from odoo_instance_sdk.exceptions import GitLabCredentialError, GitRepositoryError
from odoo_instance_sdk.internal.gitlab_credentials import resolve_gitlab_credential
from odoo_instance_sdk.internal.secret_store import SecretStore

_MEMBER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _write_legacy_mapping(path: Path) -> None:
    path.write_text(
        f'[[mappings]]\nuser_id = "{_MEMBER_ID}"\n'
        'host = "gitlab.example"\nlogin = "legacy-login"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_ROOT"\n',
        encoding="utf-8",
    )
    path.chmod(0o600)


class _IssueStore:
    def __init__(self, issue: Issue) -> None:
        self.issue = issue

    def get(self, _issue_id: str) -> Issue:
        return self.issue


@pytest.fixture(autouse=True)
def _empty_encrypted_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SecretStore, "lookup_gitlab_member", lambda *_args: None)


def _credential() -> GitLabCredentialContext:
    return GitLabCredentialContext(
        identity=GitLabCredentialIdentity(
            user_id="user",
            host="gitlab.example",
            login="alice",
            token_key="ODCLI_GITLAB_TOKEN_ALICE",
            workspace_id="workspace",
            issue_id="issue",
            root_issue_id="root",
        ),
        _token="secret-token",
    )


def _mr_client(root: Issue) -> MulticaOdooClient:
    client = object.__new__(MulticaOdooClient)
    client.multica = SimpleNamespace(
        issues=_IssueStore(root),
        projects=SimpleNamespace(get=lambda _: pytest.fail("unexpected project lookup")),
        config=SimpleNamespace(server_url="https://multica.example"),
    )
    client._compatibility = MulticaCompatibility(
        package_version="0.1.0",
        package_revision="c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed",
        native_cli_version="0.5.3",
        typed_checkout=True,
        typed_daemon_status=True,
        observed=True,
    )
    return client


def _mr_context(checkout_path: str = "/task/checkout") -> VerifiedTaskContext:
    return VerifiedTaskContext(
        checkout_path=checkout_path,
        task_root="/task",
        repository_url="https://gitlab.example/team/repo.git",
        workspace_id="workspace",
        multica_project_id="project",
        issue_id="root",
        run_id="run",
        runtime_id="runtime",
        daemon_id="daemon",
        observed_at=datetime.now(UTC),
        root_issue_id="root",
        root_creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )


def _git_repo(root: Path, origin: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin], check=True)
    return root


def _git_context(root: Path) -> VerifiedTaskContext:
    return msgspec.structs.replace(_mr_context(), checkout_path=str(root))


class _CapturedGit:
    def __init__(self) -> None:
        self.passthrough_calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.sync_calls: list[dict[str, object]] = []

    def passthrough_command(
        self,
        args: tuple[str, ...],
        *,
        environment: dict[str, str] | None = None,
        secret_values: tuple[str, ...] = (),
        hooks_path: str | None = None,
    ) -> object:
        kwargs = {
            "environment": environment or {},
            "secret_values": secret_values,
        }
        if hooks_path is not None:
            kwargs["hooks_path"] = hooks_path
        self.passthrough_calls.append((args, kwargs))
        return SimpleNamespace(run=lambda: SimpleNamespace(returncode=0, stdout=""))

    def sync_command(
        self,
        *,
        base: str | None = None,
        push: bool = False,
        environment: dict[str, str] | None = None,
        secret_values: tuple[str, ...] = (),
        remote_allowed: Callable[[str], bool] | None = None,
        hooks_path: str | None = None,
    ) -> object:
        kwargs = {
            "base": base,
            "push": push,
            "environment": environment or {},
            "secret_values": secret_values,
            "remote_allowed": remote_allowed,
        }
        if hooks_path is not None:
            kwargs["hooks_path"] = hooks_path
        self.sync_calls.append(kwargs)
        return SimpleNamespace(run=lambda: SimpleNamespace(returncode=0, stdout=""))


def _credential_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    environment = project / ".odcli" / ".env"
    environment.parent.mkdir(parents=True)
    environment.parent.chmod(0o700)
    environment.write_text("ODCLI_GITLAB_TOKEN_ROOT=secret-token\n", encoding="utf-8")
    environment.chmod(0o600)
    mapping = project / ".odcli" / "gitlab-credentials.toml"
    mapping.write_text(
        '[[mappings]]\nuser_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"\nhost = "gitlab.example"\n'
        'login = "root-login"\ntoken_key = "ODCLI_GITLAB_TOKEN_ROOT"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)
    return project, mapping


def test_git_passthrough_and_sync_scope_https_credentials_to_child(tmp_path: Path) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        creator_type="member",
    )
    project, mapping = _credential_project(tmp_path)
    client = _mr_client(root)

    local_git = _CapturedGit()
    client.git_command(
        _mr_context(),
        ("status", "--short"),
        git_resource=local_git,
        project_root=project,
        mapping_path=mapping,
    ).run()
    assert local_git.passthrough_calls[0][1] == {"environment": {}, "secret_values": ()}

    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    remote_git = _CapturedGit()
    client.git_command(
        _git_context(checkout),
        ("fetch", "origin"),
        git_resource=remote_git,
        project_root=project,
        mapping_path=mapping,
    ).run()
    environment = remote_git.passthrough_calls[0][1]["environment"]
    assert environment["GITLAB_LOGIN"] == "root-login"
    assert environment["GIT_ASKPASS"].endswith("odcli-multica-askpass")
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert "GITLAB_TOKEN" not in environment
    assert environment["ODCLI_GITLAB_HOST"] == "gitlab.example"
    assert not Path(environment["ODCLI_GITLAB_ASKPASS_FILE"]).exists()
    assert remote_git.passthrough_calls[0][1]["secret_values"] == ("secret-token",)

    sync_git = _CapturedGit()
    client.sync_command(
        _git_context(checkout),
        git_resource=sync_git,
        project_root=project,
        mapping_path=mapping,
    ).run()
    sync_environment = sync_git.sync_calls[0]["environment"]
    assert sync_environment["GITLAB_LOGIN"] == environment["GITLAB_LOGIN"]
    assert sync_environment["GIT_ASKPASS"] == environment["GIT_ASKPASS"]
    assert sync_environment["ODCLI_GITLAB_HOST"] == environment["ODCLI_GITLAB_HOST"]
    assert not Path(sync_environment["ODCLI_GITLAB_ASKPASS_FILE"]).exists()
    assert sync_git.sync_calls[0]["secret_values"] == ("secret-token",)


def test_encrypted_only_credential_reaches_private_git_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.secret_store import GitLabMemberCredential

    member_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    token = "encrypted-test-token"
    encrypted = GitLabMemberCredential(
        member_id=member_id,
        host="gitlab.example",
        login="root-login",
        token=token,
        token_key="ODCLI_GITLAB_TOKEN_ROOT",
    )
    monkeypatch.setattr(SecretStore, "lookup_gitlab_member", lambda *_args: encrypted)
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id=member_id,
        creator_type="member",
    )
    project = tmp_path / "empty-project"
    project.mkdir()
    checkout = _git_repo(tmp_path / "checkout-encrypted", "https://gitlab.example/team/repo.git")
    git = _CapturedGit()

    command = _mr_client(root).git_command(
        _git_context(checkout),
        ("fetch", "origin"),
        git_resource=git,
        project_root=project,
    )
    command.run()

    environment = git.passthrough_calls[0][1]["environment"]
    assert environment["GITLAB_LOGIN"] == "root-login"
    assert "GITLAB_TOKEN" not in environment
    assert git.passthrough_calls[0][1]["secret_values"] == (token,)
    assert token not in repr(command.plan)


def test_git_https_ambiguous_host_fails_before_child_process(tmp_path: Path) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        creator_type="member",
    )
    project, mapping = _credential_project(tmp_path)
    git = _CapturedGit()

    with pytest.raises(Exception, match="ambiguous"):
        _mr_client(root).git_command(
            _mr_context(),
            ("fetch", "https://other.example/team/repo.git"),
            git_resource=git,
            project_root=project,
            mapping_path=mapping,
        ).run()
    assert git.passthrough_calls == []


def test_ssh_raw_passthrough_does_not_request_https_credentials(tmp_path: Path) -> None:
    context = msgspec.structs.replace(
        _mr_context(), repository_url="git@gitlab.example:team/repo.git"
    )
    git = _CapturedGit()

    _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    ).git_command(
        context,
        ("fetch", "origin"),
        git_resource=git,
        project_root=tmp_path,
    ).run()

    assert git.passthrough_calls[0][1] == {"environment": {}, "secret_values": ()}


def test_credentialed_git_rejects_changed_origin_before_credentials(tmp_path: Path) -> None:
    checkout = _git_repo(tmp_path / "checkout", "https://attacker.example/team/repo.git")
    client = _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    )
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    git = _CapturedGit()

    with pytest.raises(Exception, match="origin"):
        client.git_command(
            _git_context(checkout),
            ("fetch", "origin"),
            git_resource=git,
            project_root=tmp_path,
        ).run()
    assert git.passthrough_calls == []


def test_credentialed_git_rechecks_origin_at_execution(tmp_path: Path) -> None:
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    project, mapping = _credential_project(tmp_path)
    client = _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    )
    git = _CapturedGit()
    command = client.git_command(
        _git_context(checkout),
        ("fetch", "origin"),
        git_resource=git,
        project_root=project,
        mapping_path=mapping,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "remote",
            "set-url",
            "origin",
            "https://attacker.example/team/repo.git",
        ],
        check=True,
    )

    with pytest.raises(Exception, match="origin"):
        command.run()
    assert git.passthrough_calls == []


def test_credentialed_git_uses_hermetic_environment_and_disables_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    project, mapping = _credential_project(tmp_path)
    hook = checkout / ".git" / "hooks" / "pre-push"
    hook.write_text('#!/bin/sh\nprintf canary >"$ODCLI_GITLAB_ASKPASS_FILE"\n', encoding="utf-8")
    hook.chmod(0o700)
    monkeypatch.setenv("GIT_EXEC_PATH", str(tmp_path / "attacker-git"))
    git = _CapturedGit()

    _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    ).git_command(
        _git_context(checkout),
        ("fetch", "origin"),
        git_resource=git,
        project_root=project,
        mapping_path=mapping,
    ).run()

    call = git.passthrough_calls[0][1]
    assert "GIT_EXEC_PATH" not in call["environment"]
    hooks_path = call["hooks_path"]
    assert isinstance(hooks_path, str)
    assert not Path(hooks_path).exists()
    assert call["environment"]["ODCLI_GITLAB_ASKPASS_FILE"]


def test_credentialed_git_rejects_custom_submodule_update_before_child(
    tmp_path: Path,
) -> None:
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "config",
            "submodule.foreign.update",
            "!printf canary",
        ],
        check=True,
    )
    client = _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    )
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    git = _CapturedGit()

    with pytest.raises(Exception, match="custom submodule update"):
        client.git_command(
            _git_context(checkout),
            ("submodule", "update", "--init"),
            git_resource=git,
            project_root=tmp_path,
        ).run()
    assert git.passthrough_calls == []


def test_never_run_credentialed_command_does_not_create_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, mapping = _credential_project(tmp_path)
    git = _CapturedGit()
    import odcli_multica.git_execution as execution_module

    monkeypatch.setattr(
        execution_module,
        "write_askpass_channel",
        lambda credential: pytest.fail("credential channel was created before run"),
    )
    command = _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    ).git_command(
        _git_context(_git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")),
        ("fetch", "origin"),
        git_resource=git,
        project_root=project,
        mapping_path=mapping,
    )

    assert command.plan.steps
    assert git.passthrough_calls == []


def test_credential_channel_is_removed_when_child_construction_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, mapping = _credential_project(tmp_path)
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    channel = tmp_path / "askpass.json"
    import odcli_multica.git_execution as execution_module

    def write_channel(credential: GitLabCredentialContext) -> str:
        channel.write_text("canary", encoding="utf-8")
        return str(channel)

    monkeypatch.setattr(execution_module, "write_askpass_channel", write_channel)

    class FailingGit(_CapturedGit):
        def passthrough_command(
            self,
            args: tuple[str, ...],
            *,
            environment: dict[str, str] | None = None,
            secret_values: tuple[str, ...] = (),
            hooks_path: str | None = None,
        ) -> object:
            raise RuntimeError("child construction failed")

    with pytest.raises(RuntimeError, match="child construction"):
        _mr_client(
            Issue(
                "root",
                "Root",
                "todo",
                project_id="project",
                creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                creator_type="member",
            )
        ).git_command(
            _git_context(checkout),
            ("fetch", "origin"),
            git_resource=FailingGit(),
            project_root=project,
            mapping_path=mapping,
        ).run()

    assert not channel.exists()


def test_credentialed_git_rejects_repository_credential_helper_before_credentials(
    tmp_path: Path,
) -> None:
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    subprocess.run(
        ["git", "-C", str(checkout), "config", "credential.helper", "!printf canary"],
        check=True,
    )
    client = _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    )
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    git = _CapturedGit()

    with pytest.raises(Exception, match="credential helper"):
        client.git_command(
            _git_context(checkout),
            ("fetch", "origin"),
            git_resource=git,
            project_root=tmp_path,
        ).run()
    assert git.passthrough_calls == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("http.proxy", "https://proxy.example"),
        ("remote.origin.proxy", "https://proxy.example"),
        ("remote.origin.pushurl", "https://attacker.example/team/repo.git"),
        ("url.https://attacker.example/.insteadOf", "https://gitlab.example/"),
        ("protocol.file.allow", "always"),
        ("core.sshCommand", "printf canary"),
    ],
)
def test_credentialed_git_rejects_repo_transport_controls_before_channel_or_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    subprocess.run(["git", "-C", str(checkout), "config", key, value], check=True)
    client = _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    )
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    git = _CapturedGit()
    channel_calls: list[str] = []
    monkeypatch.setattr(
        "odcli_multica.git_execution.write_askpass_channel",
        lambda _credential: channel_calls.append("created") or "unused",
    )

    with pytest.raises(Exception, match=r"unsafe|transport|changed|ambiguous"):
        client.git_command(
            _git_context(checkout),
            ("fetch", "origin"),
            git_resource=git,
            project_root=tmp_path,
        ).run()

    assert channel_calls == []
    assert git.passthrough_calls == []


def _include_path(checkout: Path, included: Path) -> None:
    with (checkout / ".git" / "config").open("a", encoding="utf-8") as stream:
        stream.write(f"\n[include]\n\tpath = {included}\n")


def _include_if_gitdir(checkout: Path, included: Path) -> None:
    with (checkout / ".git" / "config").open("a", encoding="utf-8") as stream:
        stream.write(f'\n[includeIf "gitdir:{checkout}/"]\n\tpath = {included}\n')


def _worktree_config(checkout: Path, included: Path) -> None:
    subprocess.run(
        ["git", "-C", str(checkout), "config", "extensions.worktreeConfig", "true"],
        check=True,
    )
    (checkout / ".git" / "config.worktree").write_text(
        included.read_text(encoding="utf-8"), encoding="utf-8"
    )


def _http_proxy_include_path(tmp_path: Path) -> Path:
    return _included_config_case(
        tmp_path, "include.path", "[http]\n\tproxy = https://proxy.example\n"
    )


def _ssl_verify_include_if(tmp_path: Path) -> Path:
    return _included_config_case(tmp_path, "includeIf.gitdir", "[http]\n\tsslVerify = false\n")


def _http_proxy_worktree_config(tmp_path: Path) -> Path:
    return _included_config_case(
        tmp_path, "config.worktree", "[http]\n\tproxy = https://proxy.example\n"
    )


def _direct_vcs(tmp_path: Path) -> Path:
    return _vcs_config_case(tmp_path, "direct")


def _include_path_vcs(tmp_path: Path) -> Path:
    return _vcs_config_case(tmp_path, "include.path")


def _include_if_vcs(tmp_path: Path) -> Path:
    return _vcs_config_case(tmp_path, "includeIf.gitdir")


def _worktree_vcs(tmp_path: Path) -> Path:
    return _vcs_config_case(tmp_path, "config.worktree")


def _included_config_case(tmp_path: Path, source: str, included_config: str) -> Path:
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    included = tmp_path / "included.gitconfig"
    included.write_text(included_config, encoding="utf-8")
    if source == "include.path":
        _include_path(checkout, included)
    elif source == "includeIf.gitdir":
        _include_if_gitdir(checkout, included)
    elif source == "config.worktree":
        _worktree_config(checkout, included)
    return checkout


def _vcs_config_case(tmp_path: Path, source: str) -> Path:
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    included = tmp_path / "included.gitconfig"
    included.write_text('[remote "origin"]\n\tvcs = dangerous\n', encoding="utf-8")
    if source == "direct":
        subprocess.run(
            ["git", "-C", str(checkout), "config", "remote.origin.vcs", "dangerous"],
            check=True,
        )
    elif source == "include.path":
        _include_path(checkout, included)
    elif source == "includeIf.gitdir":
        _include_if_gitdir(checkout, included)
    elif source == "config.worktree":
        _worktree_config(checkout, included)
    return checkout


@pytest.mark.parametrize(
    "case_builder",
    [_http_proxy_include_path, _ssl_verify_include_if, _http_proxy_worktree_config],
    ids=["include-path-proxy", "include-if-ssl-verify", "worktree-proxy"],
)
def test_credentialed_git_rejects_included_transport_config_before_channel_or_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_builder: Callable[[Path], Path],
) -> None:
    checkout = case_builder(tmp_path)

    client = _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    )
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    git = _CapturedGit()
    channel_calls: list[str] = []
    monkeypatch.setattr(
        "odcli_multica.git_execution.write_askpass_channel",
        lambda _credential: channel_calls.append("created") or "unused",
    )

    with pytest.raises(GitRepositoryError, match="credentialed transport is unsafe"):
        client.git_command(
            _git_context(checkout),
            ("fetch", "origin"),
            git_resource=git,
            project_root=tmp_path,
        ).run()

    assert channel_calls == []
    assert git.passthrough_calls == []


@pytest.mark.parametrize(
    "case_builder",
    [_direct_vcs, _include_path_vcs, _include_if_vcs, _worktree_vcs],
    ids=["direct", "include-path", "include-if", "worktree"],
)
def test_credentialed_git_rejects_remote_vcs_before_channel_or_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_builder: Callable[[Path], Path],
) -> None:
    checkout = case_builder(tmp_path)
    client = _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    )
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    git = _CapturedGit()
    channel_calls: list[str] = []
    monkeypatch.setattr(
        "odcli_multica.git_execution.write_askpass_channel",
        lambda _credential: channel_calls.append("created") or "unused",
    )

    with pytest.raises(GitRepositoryError, match="credentialed transport is unsafe"):
        client.git_command(
            _git_context(checkout), ("fetch", "origin"), git_resource=git, project_root=tmp_path
        ).run()

    assert channel_calls == []
    assert git.passthrough_calls == []


def test_project_legacy_credential_fallback_accepts_existing_0755_directory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    credential_dir = project / ".odcli"
    credential_dir.mkdir(parents=True)
    credential_dir.chmod(0o755)
    mapping = credential_dir / "gitlab-credentials.toml"
    mapping.write_text(
        '[[mappings]]\nuser_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"\n'
        'host = "gitlab.example"\nlogin = "legacy-login"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_ROOT"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)
    environment = credential_dir / ".env"
    environment.write_text("ODCLI_GITLAB_TOKEN_ROOT=legacy-token\n", encoding="utf-8")
    environment.chmod(0o600)
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    git = _CapturedGit()

    _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    ).git_command(
        _git_context(checkout),
        ("fetch", "origin"),
        git_resource=git,
        project_root=project,
        mapping_path=mapping,
    ).run()

    assert credential_dir.stat().st_mode & 0o777 == 0o755
    assert git.passthrough_calls[0][1]["secret_values"] == ("legacy-token",)
    assert git.passthrough_calls[0][1]["environment"]["GITLAB_LOGIN"] == "legacy-login"


def _dotenv_token_case(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.delenv("ODCLI_GITLAB_TOKEN_ROOT", raising=False)
    return "dotenv-token"


def _process_override_case(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("ODCLI_GITLAB_TOKEN_ROOT", "process-token")
    return "process-token"


@pytest.mark.parametrize(
    "credential_case",
    [_dotenv_token_case, _process_override_case],
    ids=["project-dotenv", "process-overrides-dotenv"],
)
def test_external_legacy_mapping_in_0755_parent_keeps_project_and_process_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    credential_case: Callable[[pytest.MonkeyPatch], str],
) -> None:
    project = tmp_path / "project"
    project_config = project / ".odcli"
    project_config.mkdir(parents=True)
    project_config.chmod(0o700)
    (project_config / ".env").write_text("ODCLI_GITLAB_TOKEN_ROOT=dotenv-token\n", encoding="utf-8")
    (project_config / ".env").chmod(0o600)
    mapping_parent = tmp_path / "external-mapping"
    mapping_parent.mkdir()
    mapping_parent.chmod(0o755)
    mapping = mapping_parent / "gitlab-credentials.toml"
    mapping.write_text(
        '[[mappings]]\nuser_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"\n'
        'host = "gitlab.example"\nlogin = "legacy-login"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_ROOT"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)
    expected_token = credential_case(monkeypatch)
    git = _CapturedGit()
    client = _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    )

    client.git_command(
        _git_context(_git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")),
        ("fetch", "origin"),
        git_resource=git,
        project_root=project,
        mapping_path=mapping,
    ).run()

    assert mapping_parent.stat().st_mode & 0o777 == 0o755
    assert git.passthrough_calls[0][1]["environment"]["GITLAB_LOGIN"] == "legacy-login"
    assert git.passthrough_calls[0][1]["secret_values"] == (expected_token,)


def test_core_legacy_mapping_parent_remains_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SecretStore, "lookup_gitlab_member", lambda *_args: None)
    mapping_parent = tmp_path / "mapping"
    mapping_parent.mkdir()
    mapping_parent.chmod(0o755)
    mapping = mapping_parent / "gitlab-credentials.toml"
    _write_legacy_mapping(mapping)

    with pytest.raises(GitLabCredentialError, match="owner-only permissions"):
        resolve_gitlab_credential(
            _MEMBER_ID,
            "gitlab.example",
            user_root=tmp_path / "user",
            mapping_path=mapping,
        )


def test_core_legacy_environment_parent_remains_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SecretStore, "lookup_gitlab_member", lambda *_args: None)
    mapping_parent = tmp_path / "mapping"
    mapping_parent.mkdir(mode=0o700)
    mapping_parent.chmod(0o700)
    mapping = mapping_parent / "gitlab-credentials.toml"
    _write_legacy_mapping(mapping)
    environment_parent = tmp_path / "environment"
    environment_parent.mkdir()
    environment_parent.chmod(0o755)
    environment = environment_parent / ".env"
    environment.write_text("ODCLI_GITLAB_TOKEN_ROOT=legacy-token\n", encoding="utf-8")
    environment.chmod(0o600)

    with pytest.raises(GitLabCredentialError, match="owner-only permissions"):
        resolve_gitlab_credential(
            _MEMBER_ID,
            "gitlab.example",
            user_root=tmp_path / "user",
            mapping_path=mapping,
            environment_path=environment,
        )


def test_credentialed_git_rejects_foreign_submodule_authority_before_credentials(
    tmp_path: Path,
) -> None:
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    (checkout / ".gitmodules").write_text(
        '[submodule "foreign"]\n\tpath = foreign\n\turl = https://attacker.example/foreign.git\n',
        encoding="utf-8",
    )
    client = _mr_client(
        Issue(
            "root",
            "Root",
            "todo",
            project_id="project",
            creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            creator_type="member",
        )
    )
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    git = _CapturedGit()

    with pytest.raises(Exception, match="submodule"):
        client.git_command(
            _git_context(checkout),
            ("submodule", "update", "--init"),
            git_resource=git,
            project_root=tmp_path,
        ).run()
    assert git.passthrough_calls == []
