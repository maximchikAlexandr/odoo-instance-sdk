from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from odcli_multica.gitlab import (
    GitLabPublicationError,
    publish_merge_request,
    read_description_file,
    repository_project_path,
)
from odcli_multica.models import (
    GitLabCredentialContext,
    GitLabCredentialIdentity,
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
