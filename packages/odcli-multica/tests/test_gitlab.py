from __future__ import annotations

import json
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


@pytest.mark.parametrize(("existing", "assignee"), [(False, "alice"), (True, 17)])
def test_publish_preflights_member_and_verifies_assignee(
    existing: bool, assignee: int | str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).endswith("/api/v4/projects/team%2Frepo"):
            return httpx.Response(200, json={"id": 42})
        if request.url.path == "/api/v4/users" or request.url.path == "/api/v4/users/17":
            return httpx.Response(
                200,
                json={"id": 17, "username": "alice", "state": "active"}
                if isinstance(assignee, int)
                else [{"id": 17, "username": "alice", "state": "active"}],
            )
        if request.url.path == "/api/v4/projects/42/members/all/17":
            return httpx.Response(200, json={"id": 17, "access_level": 30})
        if request.url.path == "/api/v4/projects/42/merge_requests":
            if request.method == "GET":
                return httpx.Response(200, json=[{"iid": 7}] if existing else [])
            return httpx.Response(
                200 if existing else 201,
                json={
                    "iid": 7,
                    "web_url": "https://gitlab.example/team/repo/-/merge_requests/7",
                    "assignee": {"id": 17, "username": "alice"},
                },
            )
        if request.url.path == "/api/v4/projects/42/merge_requests/7":
            return httpx.Response(
                200,
                json={
                    "iid": 7,
                    "web_url": "https://gitlab.example/team/repo/-/merge_requests/7",
                    "assignee": {"id": 17, "username": "alice"},
                },
            )
        raise AssertionError(request.url)

    def factory(**kwargs: object) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    result = publish_merge_request(
        credential=_credential(),
        project_path="team/repo",
        source_branch="feature",
        target_branch="main",
        title="title",
        description="body",
        issue_url="https://multica.example/issues/1",
        assignee=assignee,
        assignee_source="default",
        client_factory=factory,
    )

    mutation = requests[-1]
    payload = json.loads(mutation.content)
    user_lookup = next(request for request in requests if "/users" in request.url.path)
    assert mutation.method == ("PUT" if existing else "POST")
    if isinstance(assignee, int):
        assert user_lookup.url.path == "/api/v4/users/17"
        assert "username" not in user_lookup.url.params
    else:
        assert user_lookup.url.path == "/api/v4/users"
        assert user_lookup.url.params["username"] == "alice"
    assert any(request.url.path == "/api/v4/projects/42/members/all/17" for request in requests)
    assert payload["assignee_id"] == 17
    assert "reviewer_ids" not in payload
    assert result.assignee_id == 17
    assert result.assignee_username == "alice"
    assert result.assignee_source == "default"


@pytest.mark.parametrize(
    ("state", "member_status", "ambiguous"),
    [("blocked", 200, False), ("active", 403, False), ("active", 404, False), ("active", 200, True)],
)
def test_publish_rejects_inactive_ambiguous_or_inaccessible_before_mutation(
    state: str, member_status: int, ambiguous: bool
) -> None:
    mutations: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/api/v4/projects/team%2Frepo"):
            return httpx.Response(200, json={"id": 42})
        if request.url.path == "/api/v4/users":
            user = {"id": 17, "username": "alice", "state": state}
            return httpx.Response(200, json=[user, user] if ambiguous else [user])
        if request.url.path == "/api/v4/projects/42/members/all/17":
            return httpx.Response(member_status, json={"id": 17} if member_status == 200 else {})
        mutations.append(request)
        return httpx.Response(500)

    def factory(**kwargs: object) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    with pytest.raises(GitLabPublicationError, match=r"failed|inactive|ambiguous"):
        publish_merge_request(
            credential=_credential(),
            project_path="team/repo",
            source_branch="feature",
            target_branch="main",
            title="title",
            description="body",
            issue_url="https://multica.example/issues/1",
            assignee="alice",
            client_factory=factory,
        )
    assert mutations == []


def test_publish_rejects_mismatched_response_assignee_without_retry() -> None:
    mutations: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/api/v4/projects/team%2Frepo"):
            return httpx.Response(200, json={"id": 42})
        if request.url.path == "/api/v4/users":
            return httpx.Response(200, json=[{"id": 17, "username": "alice", "state": "active"}])
        if request.url.path == "/api/v4/projects/42/members/all/17":
            return httpx.Response(200, json={"id": 17, "access_level": 30})
        if request.url.path == "/api/v4/projects/42/merge_requests":
            if request.method == "GET":
                return httpx.Response(200, json=[])
            mutations.append(request)
            return httpx.Response(
                201,
                json={"iid": 7, "web_url": "https://gitlab.example/mr/7", "assignee": None},
            )
        raise AssertionError(request.url)

    def factory(**kwargs: object) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    with pytest.raises(GitLabPublicationError, match="inconsistent assignee"):
        publish_merge_request(
            credential=_credential(),
            project_path="team/repo",
            source_branch="feature",
            target_branch="main",
            title="title",
            description="body",
            issue_url="https://multica.example/issues/1",
            assignee="alice",
            client_factory=factory,
        )
    assert len(mutations) == 1


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


class _SettingsCatalog:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}
        self.keys: list[str] = []

    def get_setting(self, key: str) -> object | None:
        self.keys.append(key)
        value = self.values.get(key)
        return None if value is None else SimpleNamespace(value=value)


def _mr_client(root: Issue) -> MulticaOdooClient:
    catalog = _SettingsCatalog()
    client = object.__new__(MulticaOdooClient)
    client.core = SimpleNamespace(get_catalog=lambda: catalog)
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
    assert "gitlab.mr.default_assignee:https://gitlab.example/team/repo" in plan_text
    assert "catalog_default_or_absent" in plan_text
    assert "deferred-until-execution" in plan_text


@pytest.mark.parametrize(
    ("configured", "override", "selected", "source"),
    [
        ("exact-user", None, "exact-user", "default"),
        (17, None, 17, "default"),
        ("catalog-user", "explicit-user", "explicit-user", "override"),
        ("catalog-user", "   ", "catalog-user", "default"),
        (None, None, None, "none"),
    ],
)
def test_mr_uses_only_exact_scoped_setting_and_explicit_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: object,
    override: str | None,
    selected: object,
    source: str,
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
    catalog = client.core.get_catalog()
    catalog.values["gitlab.mr.default_assignee:https://gitlab.example/team/repo"] = configured
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    observed: list[dict[str, object]] = []

    def publish(**kwargs: object) -> MergeRequestPublicationResult:
        observed.append(kwargs)
        return MergeRequestPublicationResult(
            merge_request_id=1,
            web_url="https://gitlab.example/team/repo/-/merge_requests/1",
            source_branch="feature",
            target_branch="main",
            outcome="created",
        )

    monkeypatch.setattr("odcli_multica.gitlab.publish_merge_request", publish)
    client.publish_merge_request_command(
        _mr_context(),
        project_root=tmp_path,
        source_branch="feature",
        target_branch="main",
        title="title",
        description_file=description,
        assignee=override,
    ).run()

    assert catalog.keys == (
        []
        if override is not None and override.strip()
        else ["gitlab.mr.default_assignee:https://gitlab.example/team/repo"]
    )
    assert observed[0]["assignee"] == selected
    assert observed[0]["assignee_source"] == source


def test_mr_setting_isolated_by_normalized_host_and_selected_project(
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
    catalog = client.core.get_catalog()
    catalog.values.update(
        {
            "gitlab.mr.default_assignee:https://gitlab.example/other/repo": "wrong-project",
            "gitlab.mr.default_assignee:https://other.example/team/repo": "wrong-host",
        }
    )
    client.credential_context = lambda *args, **kwargs: _credential()  # type: ignore[method-assign]
    observed: list[dict[str, object]] = []
    monkeypatch.setattr(
        "odcli_multica.gitlab.publish_merge_request",
        lambda **kwargs: (
            observed.append(kwargs)
            or MergeRequestPublicationResult(
                merge_request_id=1,
                web_url="https://gitlab.example/other/repo/-/merge_requests/1",
                source_branch="feature",
                target_branch="main",
                outcome="created",
            )
        ),
    )
    context = msgspec.structs.replace(
        _mr_context(),
        repository_url="https://GITLAB.EXAMPLE/team/repo.git",
    )
    client.publish_merge_request_command(
        context,
        project_root=tmp_path,
        source_branch="feature",
        target_branch="main",
        title="title",
        description_file=description,
    ).run()

    assert catalog.keys == ["gitlab.mr.default_assignee:https://gitlab.example/team/repo"]
    assert observed[0]["assignee"] is None


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
