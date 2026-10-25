from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import msgspec
import pytest
from multica_py import Issue
from odcli_multica.client import MulticaOdooClient
from odcli_multica.client_policies import current_branch
from odcli_multica.gitlab import (
    GitLabPublicationError,
    publish_merge_request,
    read_description_file,
    repository_project_path,
)
from odcli_multica.models import (
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    MergeRequestPublicationResult,
    MulticaCompatibility,
    VerifiedTaskContext,
)

from odoo_instance_sdk.internal.secret_store import SecretStore


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


def test_description_and_repository_validation(tmp_path: Path) -> None:
    description = tmp_path / "description.md"
    description.write_text("body\n", encoding="utf-8")

    assert read_description_file(description) == "body\n"
    assert repository_project_path("https://GitLab.Example/team/repo.git") == (
        "gitlab.example",
        "team/repo",
    )
    with pytest.raises(GitLabPublicationError, match="HTTPS"):
        repository_project_path("http://gitlab.example/team/repo")


def test_publish_creates_one_exact_merge_request_without_exposing_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).endswith("/api/v4/projects/team%2Frepo"):
            return httpx.Response(200, json={"id": 42})
        if request.url.path == "/api/v4/projects/42/merge_requests":
            if request.method == "GET":
                return httpx.Response(200, json=[])
            return httpx.Response(
                201,
                json={"iid": 7, "web_url": "https://gitlab.example/team/repo/-/merge_requests/7"},
            )
        raise AssertionError(request.url)

    def factory(**kwargs: object) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    result = publish_merge_request(
        credential=_credential(),
        project_path="team/repo",
        source_branch="feature",
        target_branch="main",
        title="Add feature",
        description="body",
        issue_url="https://multica.example/issues/1",
        client_factory=factory,
    )

    assert result.outcome == "created"
    assert result.merge_request_id == 7
    assert len(requests) == 3
    assert all(b"secret-token" not in request.content for request in requests)
    assert requests[-1].headers["PRIVATE-TOKEN"] == "secret-token"


def test_publish_rejects_ambiguous_open_merge_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/api/v4/projects/team%2Frepo"):
            return httpx.Response(200, json={"id": 42})
        return httpx.Response(200, json=[{"iid": 1}, {"iid": 2}])

    def factory(**kwargs: object) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    with pytest.raises(GitLabPublicationError, match="multiple"):
        publish_merge_request(
            credential=_credential(),
            project_path="team/repo",
            source_branch="feature",
            target_branch="main",
            title="Add feature",
            description="body",
            issue_url="https://multica.example/issues/1",
            client_factory=factory,
        )


def test_publish_updates_the_unique_open_merge_request() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if str(request.url).endswith("/api/v4/projects/team%2Frepo"):
            return httpx.Response(200, json={"id": 42})
        if request.method == "GET":
            return httpx.Response(200, json=[{"iid": 7}])
        return httpx.Response(
            200,
            json={"iid": 7, "web_url": "https://gitlab.example/team/repo/-/merge_requests/7"},
        )

    def factory(**kwargs: object) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    result = publish_merge_request(
        credential=_credential(),
        project_path="team/repo",
        source_branch="feature",
        target_branch="main",
        title="Update feature",
        description="body",
        issue_url="https://multica.example/issues/1",
        client_factory=factory,
    )

    assert result.outcome == "updated"
    assert methods[-1] == "PUT"


def test_publish_provider_failure_is_bounded_and_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"secret-token" not in request.content
        return httpx.Response(503, text="secret-token upstream details")

    def factory(**kwargs: object) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    with pytest.raises(GitLabPublicationError, match="failed") as error:
        publish_merge_request(
            credential=_credential(),
            project_path="team/repo",
            source_branch="feature",
            target_branch="main",
            title="Update feature",
            description="body",
            issue_url="https://multica.example/issues/1",
            client_factory=factory,
        )
    assert "secret-token" not in str(error.value)


def _missing_description(path: Path) -> None:
    return None


def _symlink_description(path: Path) -> None:
    target = path.with_name("target.md")
    target.write_text("body", encoding="utf-8")
    path.symlink_to(target)


def _directory_description(path: Path) -> None:
    path.mkdir()


def _invalid_utf8_description(path: Path) -> None:
    path.write_bytes(b"\xff")


def _oversized_description(path: Path) -> None:
    path.write_bytes(b"x" * (1_048_576 + 1))


@pytest.mark.parametrize(
    "arrange",
    [
        pytest.param(_missing_description, id="missing"),
        pytest.param(_symlink_description, id="symlink"),
        pytest.param(_directory_description, id="directory"),
        pytest.param(_invalid_utf8_description, id="utf8"),
        pytest.param(_oversized_description, id="large"),
    ],
)
def test_description_file_rejects_bounded_negative_cases(
    tmp_path: Path, arrange: Callable[[Path], None]
) -> None:
    path = tmp_path / "description.md"
    arrange(path)

    with pytest.raises(GitLabPublicationError):
        read_description_file(path)


class _IssueStore:
    def __init__(self, issue: Issue) -> None:
        self.issue = issue

    def get(self, _issue_id: str) -> Issue:
        return self.issue


class _BranchResource:
    def __init__(self, branch: str) -> None:
        self.branch = branch

    def passthrough(self, args: tuple[str, ...]) -> object:
        assert args == ("symbolic-ref", "--quiet", "--short", "HEAD")
        return SimpleNamespace(run=lambda: SimpleNamespace(returncode=0, stdout=f"{self.branch}\n"))


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


def test_encrypted_only_credential_reaches_private_merge_request_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.secret_store import GitLabMemberCredential

    member_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    token = "encrypted-mr-test-token"
    monkeypatch.setattr(
        SecretStore,
        "lookup_gitlab_member",
        lambda *_args: GitLabMemberCredential(
            member_id=member_id,
            host="gitlab.example",
            login="root-login",
            token=token,
            token_key="ODCLI_GITLAB_TOKEN_ROOT",
        ),
    )
    root = Issue(
        "root", "Root", "todo", project_id="project", creator_id=member_id, creator_type="member"
    )
    project = tmp_path / "empty-project"
    project.mkdir()
    description = tmp_path / "description.md"
    description.write_text("body", encoding="utf-8")
    captured: list[GitLabCredentialContext] = []
    monkeypatch.setattr(
        "odcli_multica.gitlab.publish_merge_request",
        lambda **kwargs: (
            captured.append(kwargs["credential"])
            or MergeRequestPublicationResult(
                merge_request_id=7,
                web_url="https://gitlab.example/team/repo/-/merge_requests/7",
                source_branch="feature",
                target_branch="main",
                outcome="created",
            )
        ),
    )
    command = _mr_client(root).publish_merge_request_command(
        _mr_context(),
        project_root=project,
        source_branch="feature",
        target_branch="main",
        title="Private credential path",
        description_file=description,
    )

    result = command.run()

    assert result.merge_request_id == 7
    assert captured[0]._token == token
    assert token not in repr(command.plan)
    assert token not in repr(result)


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


@pytest.mark.parametrize(
    ("source", "included_config"),
    [
        ("include.path", "[http]\n\tproxy = https://proxy.example\n"),
        ("includeIf.gitdir", "[http]\n\tsslVerify = false\n"),
        ("config.worktree", "[http]\n\tproxy = https://proxy.example\n"),
    ],
)
def test_credentialed_git_rejects_included_transport_config_before_channel_or_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    included_config: str,
) -> None:
    checkout = _git_repo(tmp_path / "checkout", "https://gitlab.example/team/repo.git")
    included = tmp_path / "included.gitconfig"
    included.write_text(included_config, encoding="utf-8")
    config = checkout / ".git" / "config"
    if source == "include.path":
        with config.open("a", encoding="utf-8") as stream:
            stream.write(f"\n[include]\n\tpath = {included}\n")
    elif source == "includeIf.gitdir":
        with config.open("a", encoding="utf-8") as stream:
            stream.write(f'\n[includeIf "gitdir:{checkout}/"]\n\tpath = {included}\n')
    else:
        subprocess.run(
            ["git", "-C", str(checkout), "config", "extensions.worktreeConfig", "true"],
            check=True,
        )
        (checkout / ".git" / "config.worktree").write_text(included_config, encoding="utf-8")

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

    error: Exception | None = None
    try:
        client.git_command(
            _git_context(checkout),
            ("fetch", "origin"),
            git_resource=git,
            project_root=tmp_path,
        ).run()
    except Exception as exc:
        error = exc

    assert channel_calls == []
    assert git.passthrough_calls == []
    assert error is not None
    assert any(word in str(error).casefold() for word in ("unsafe", "transport", "configuration"))


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
    ).run()

    assert credential_dir.stat().st_mode & 0o777 == 0o755
    assert git.passthrough_calls[0][1]["secret_values"] == ("legacy-token",)
    assert git.passthrough_calls[0][1]["environment"]["GITLAB_LOGIN"] == "legacy-login"


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


def test_mr_dry_run_plan_is_non_secret_and_does_not_call_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        creator_type="member",
    )
    description = tmp_path / "description.md"
    description.write_text("body", encoding="utf-8")
    client = _mr_client(root)
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    monkeypatch.setattr(
        "odcli_multica.gitlab.publish_merge_request",
        lambda **_: pytest.fail("dry-run invoked provider"),
    )

    command = client.publish_merge_request_command(
        _mr_context(),
        project_root=tmp_path,
        git_resource=_BranchResource("feature"),
        source_branch="feature",
        target_branch="main",
        title="Add feature",
        description_file=description,
        issue_url="https://multica.example/issues/root",
    )

    plan_text = repr(command.plan)
    assert "secret-token" not in plan_text
    assert "gitlab.mr.revalidate-context" in plan_text
    assert "gitlab.mr.revalidate-branch" in plan_text
    assert "gitlab.mr.publish" in plan_text
    assert "https://multica.example/issues/root" in plan_text


def test_mr_rejects_unverified_issue_url_even_when_https(tmp_path: Path) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        creator_type="member",
    )
    description = tmp_path / "description.md"
    description.write_text("body", encoding="utf-8")

    with pytest.raises(Exception, match="verified issue"):
        _mr_client(root).publish_merge_request_command(
            _mr_context(),
            project_root=tmp_path,
            git_resource=_BranchResource("feature"),
            source_branch="feature",
            target_branch="main",
            title="Add feature",
            description_file=description,
            issue_url="https://other.example/issues/root",
        )


def test_mr_accepts_explicit_public_issue_url_for_loopback_daemon(tmp_path: Path) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        creator_type="member",
    )
    description = tmp_path / "description.md"
    description.write_text("body", encoding="utf-8")
    client = _mr_client(root)
    client.multica.config.server_url = "http://localhost:8080"

    command = client.publish_merge_request_command(
        _mr_context(),
        project_root=tmp_path,
        git_resource=_BranchResource("feature"),
        source_branch="feature",
        target_branch="main",
        title="Add feature",
        description_file=description,
        issue_url="https://multica.example/my-lab/issues/root",
    )

    assert "https://multica.example/my-lab/issues/root" in repr(command.plan)


def test_mr_rejects_hostless_explicit_url_for_loopback_daemon(tmp_path: Path) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        creator_type="member",
    )
    description = tmp_path / "description.md"
    description.write_text("body", encoding="utf-8")
    client = _mr_client(root)
    client.multica.config.server_url = "http://127.0.0.1:8080"

    with pytest.raises(Exception, match="issue URL"):
        client.publish_merge_request_command(
            _mr_context(),
            project_root=tmp_path,
            git_resource=_BranchResource("feature"),
            source_branch="feature",
            target_branch="main",
            title="Add feature",
            description_file=description,
            issue_url="https://:443/issues/root",
        )


def test_mr_rejects_hostless_configured_server_url_during_construction(
    tmp_path: Path,
) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        creator_type="member",
    )
    description = tmp_path / "description.md"
    description.write_text("body", encoding="utf-8")
    client = _mr_client(root)
    client.multica.config.server_url = "https://:443"

    with pytest.raises(Exception, match="issue URL"):
        client.publish_merge_request_command(
            _mr_context(),
            project_root=tmp_path,
            git_resource=_BranchResource("feature"),
            source_branch="feature",
            target_branch="main",
            title="Add feature",
            description_file=description,
        )


@pytest.mark.parametrize(
    "issue_url",
    [
        None,
        "http://multica.example/issues/root",
        "https://localhost/issues/root",
        "https://user@multica.example/issues/root",
        "https://multica.example/issues/other",
        "https://multica.example/issues/root?redirect=other",
        "https://multica.example/other/root",
    ],
)
def test_mr_rejects_invalid_explicit_url_for_loopback_daemon(
    tmp_path: Path, issue_url: str | None
) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        creator_type="member",
    )
    description = tmp_path / "description.md"
    description.write_text("body", encoding="utf-8")
    client = _mr_client(root)
    client.multica.config.server_url = "http://127.0.0.1:8080"

    with pytest.raises(Exception, match="issue URL"):
        client.publish_merge_request_command(
            _mr_context(),
            project_root=tmp_path,
            git_resource=_BranchResource("feature"),
            source_branch="feature",
            target_branch="main",
            title="Add feature",
            description_file=description,
            issue_url=issue_url,
        )


def test_mr_execution_revalidates_stale_context_and_current_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        creator_type="member",
    )
    replacement = Issue(
        "root",
        "Replacement",
        "todo",
        project_id="project",
        creator_id="other-human",
        creator_type="member",
    )
    description = tmp_path / "description.md"
    description.write_text("body", encoding="utf-8")
    client = _mr_client(root)
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    observed_branch = "feature"
    monkeypatch.setattr("odcli_multica.client._current_branch", lambda _: observed_branch)
    provider_calls: list[bool] = []
    monkeypatch.setattr(
        "odcli_multica.gitlab.publish_merge_request",
        lambda **_: provider_calls.append(True),
    )
    command = client.publish_merge_request_command(
        _mr_context(),
        project_root=tmp_path,
        git_resource=_BranchResource("feature"),
        source_branch="feature",
        target_branch="main",
        title="Add feature",
        description_file=description,
    )
    client.multica.issues.issue = replacement

    with pytest.raises(Exception, match="task root creator"):
        command.run()
    assert provider_calls == []

    client.multica.issues.issue = root
    observed_branch = "other"
    bad_branch = client.publish_merge_request_command(
        _mr_context(),
        project_root=tmp_path,
        git_resource=_BranchResource("other"),
        source_branch="feature",
        target_branch="main",
        title="Add feature",
        description_file=description,
    )
    with pytest.raises(Exception, match="branch"):
        bad_branch.run()
    assert provider_calls == []


def test_current_branch_reads_git_output_without_passthrough_stdout(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "init", "-q", "-b", "feature", str(checkout)],
        check=True,
        capture_output=True,
    )

    assert current_branch(checkout) == "feature"
