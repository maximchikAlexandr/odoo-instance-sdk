"""Small, exact-key GitLab merge-request transport for ``odcli-multica``."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Literal, cast
from urllib.parse import quote

import httpx

from odcli_multica.models import GitLabCredentialContext, MergeRequestPublicationResult
from odoo_instance_sdk.exceptions import GitLabPublicationError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.gitlab_publication import (
    read_description_file,
    repository_project_path,
)
from odoo_instance_sdk.internal.proc.redaction import redacted_projection


def _json(
    response: httpx.Response,
    *,
    operation: str,
    secrets: Sequence[str] = (),
) -> JsonValue:
    try:
        response.raise_for_status()
        return cast("JsonValue", response.json())
    except (httpx.HTTPError, ValueError) as exc:
        detail = redacted_projection(str(exc), secrets=secrets, field="error")
        raise GitLabPublicationError(f"GitLab {operation} failed: {detail}") from None


def _request(
    request: Callable[[], httpx.Response],
    *,
    operation: str,
    secrets: Sequence[str],
) -> httpx.Response:
    try:
        return request()
    except Exception as exc:
        detail = redacted_projection(str(exc), secrets=secrets, field="error")
        raise GitLabPublicationError(f"GitLab {operation} failed: {detail}") from None


def _request_json(
    request: Callable[[], httpx.Response],
    *,
    operation: str,
    secrets: Sequence[str],
) -> JsonValue:
    return _json(
        _request(request, operation=operation, secrets=secrets),
        operation=operation,
        secrets=secrets,
    )


def _required_text(payload: Mapping[str, JsonValue], key: str, *, operation: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GitLabPublicationError(f"GitLab {operation} returned an invalid {key}")
    return value


def publish_merge_request(  # noqa: C901
    *,
    credential: GitLabCredentialContext,
    project_path: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
    issue_url: str,
    assignee: str | None = None,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> MergeRequestPublicationResult:
    """Create or update exactly one open MR for one project/branch tuple."""
    values = (project_path, source_branch, target_branch, title)
    if any(not value.strip() for value in values):
        raise GitLabPublicationError("GitLab merge-request fields must not be empty")
    if "\n" in title or "\r" in title:
        raise GitLabPublicationError("merge-request title must be one line")
    if not issue_url.strip():
        raise GitLabPublicationError("merge-request issue link is unavailable")
    final_description = description
    if issue_url not in final_description:
        final_description = f"{description.rstrip()}\n\nRelated to {issue_url}"
    base = f"https://{credential.host}/api/v4"
    headers = {"PRIVATE-TOKEN": dict(credential.child_environment())["GITLAB_TOKEN"]}
    secrets = credential._redaction_secrets()
    with client_factory(base_url=base, headers=headers, timeout=30.0) as client:
        project_payload = _request_json(
            lambda: client.get(f"/projects/{quote(project_path, safe='')}"),
            operation="project lookup",
            secrets=secrets,
        )
        if not isinstance(project_payload, Mapping):
            raise GitLabPublicationError("GitLab project lookup returned an invalid response")
        project_id = project_payload.get("id")
        if not isinstance(project_id, int) or isinstance(project_id, bool):
            raise GitLabPublicationError("GitLab project lookup returned an invalid id")

        assignee_id: int | None = None
        if assignee is not None:
            users = _request_json(
                lambda: client.get("/users", params={"username": assignee}),
                operation="assignee lookup",
                secrets=secrets,
            )
            if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], Mapping):
                raise GitLabPublicationError("GitLab assignee is missing or ambiguous")
            raw_assignee_id = users[0].get("id")
            if not isinstance(raw_assignee_id, int) or isinstance(raw_assignee_id, bool):
                raise GitLabPublicationError("GitLab assignee is invalid")
            assignee_id = raw_assignee_id

        raw_matches = _request_json(
            lambda: client.get(
                f"/projects/{project_id}/merge_requests",
                params={
                    "state": "opened",
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                    "per_page": 100,
                },
            ),
            operation="merge-request lookup",
            secrets=secrets,
        )
        if not isinstance(raw_matches, list) or not all(
            isinstance(item, Mapping) for item in raw_matches
        ):
            raise GitLabPublicationError("GitLab merge-request lookup returned an invalid response")
        matches: list[Mapping[str, JsonValue]] = [
            cast("Mapping[str, JsonValue]", item) for item in raw_matches
        ]
        if len(matches) > 1:
            identifiers = ", ".join(
                str(item.get("iid")) for item in matches if isinstance(item.get("iid"), int)
            )
            raise GitLabPublicationError(f"multiple matching open merge requests: {identifiers}")
        payload: dict[str, JsonValue] = {
            "title": title,
            "description": final_description,
            "source_branch": source_branch,
            "target_branch": target_branch,
        }
        if assignee_id is not None:
            payload["assignee_id"] = assignee_id
        if matches:
            match = matches[0]
            iid = match.get("iid")
            if not isinstance(iid, int) or isinstance(iid, bool):
                raise GitLabPublicationError("GitLab merge-request lookup returned an invalid iid")
            response = _request(
                lambda: client.put(f"/projects/{project_id}/merge_requests/{iid}", json=payload),
                operation="merge-request update",
                secrets=secrets,
            )
            outcome: Literal["created", "updated"] = "updated"
        else:
            response = _request(
                lambda: client.post(f"/projects/{project_id}/merge_requests", json=payload),
                operation="merge-request create",
                secrets=secrets,
            )
            outcome = "created"
        result = _json(
            response,
            operation=f"merge-request {outcome}",
            secrets=secrets,
        )
        if not isinstance(result, Mapping):
            raise GitLabPublicationError("GitLab merge-request returned an invalid response")
        raw_id = result.get("iid")
        if not isinstance(raw_id, int) or isinstance(raw_id, bool):
            raise GitLabPublicationError("GitLab merge-request returned an invalid iid")
        web_url = _required_text(result, "web_url", operation="merge-request")
        return MergeRequestPublicationResult(
            merge_request_id=raw_id,
            web_url=web_url,
            source_branch=source_branch,
            target_branch=target_branch,
            outcome=outcome,
        )


__all__ = [
    "GitLabPublicationError",
    "publish_merge_request",
    "read_description_file",
    "repository_project_path",
]
