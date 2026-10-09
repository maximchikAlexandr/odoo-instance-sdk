from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from multica_py import Issue
from odcli_multica.client import MulticaOdooClient
from odcli_multica.gitlab import (
    GitLabPublicationError,
    publish_merge_request,
    read_description_file,
    repository_project_path,
)
from odcli_multica.models import (
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    MulticaCompatibility,
    VerifiedTaskContext,
)


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


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "utf8", "large"])
def test_description_file_rejects_bounded_negative_cases(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "description.md"
    if kind == "missing":
        pass
    elif kind == "symlink":
        target = tmp_path / "target.md"
        target.write_text("body", encoding="utf-8")
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    elif kind == "utf8":
        path.write_bytes(b"\xff")
    else:
        path.write_bytes(b"x" * (1_048_576 + 1))

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


def _mr_context() -> VerifiedTaskContext:
    return VerifiedTaskContext(
        checkout_path="/task/checkout",
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
        root_creator_id="human-root",
    )


def test_mr_dry_run_plan_is_non_secret_and_does_not_call_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="human-root",
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
    )

    plan_text = repr(command.plan)
    assert "secret-token" not in plan_text
    assert "gitlab.mr.revalidate-context" in plan_text
    assert "gitlab.mr.revalidate-branch" in plan_text
    assert "gitlab.mr.publish" in plan_text


def test_mr_execution_revalidates_stale_context_and_current_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="human-root",
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
