"""Small, exact-key GitLab merge-request transport for ``odcli-multica``."""

from __future__ import annotations

import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote

import httpx

from odcli_multica.models import GitLabCredentialContext, MergeRequestPublicationResult
from odoo_instance_sdk.execution import JsonValue

_MAX_DESCRIPTION_BYTES = 1_048_576


class GitLabPublicationError(ValueError):
    """Raised for bounded, sanitized GitLab publication failures."""


def read_description_file(path: Path | str) -> str:
    """Read one regular, non-symlink UTF-8 description within the bound."""
    description = Path(path)
    try:
        info = description.stat()
    except OSError as exc:
        raise GitLabPublicationError("merge-request description file is unavailable") from exc
    if description.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise GitLabPublicationError("merge-request description file must be regular")
    if info.st_size > _MAX_DESCRIPTION_BYTES:
        raise GitLabPublicationError("merge-request description file is too large")
    try:
        return description.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GitLabPublicationError("merge-request description file is not valid UTF-8") from exc


def repository_project_path(repository_url: str) -> tuple[str, str]:
    """Return the exact HTTPS host and project path from a GitLab remote."""
    from urllib.parse import urlsplit

    parsed = urlsplit(repository_url)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or not parsed.path
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GitLabPublicationError("GitLab repository URL must be HTTPS")
    project = parsed.path.strip("/")
    if project.endswith(".git"):
        project = project[:-4]
    if not project or "//" in project or any(part in {".", ".."} for part in project.split("/")):
        raise GitLabPublicationError("GitLab project path is invalid")
    host = parsed.hostname.casefold()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return host, project


def _json(response: httpx.Response, *, operation: str) -> JsonValue:
    try:
        response.raise_for_status()
        return cast("JsonValue", response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise GitLabPublicationError(f"GitLab {operation} failed") from exc


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
    with client_factory(base_url=base, headers=headers, timeout=30.0) as client:
        project_payload = _json(
            client.get(f"/projects/{quote(project_path, safe='')}"), operation="project lookup"
        )
        if not isinstance(project_payload, Mapping):
            raise GitLabPublicationError("GitLab project lookup returned an invalid response")
        project_id = project_payload.get("id")
        if not isinstance(project_id, int) or isinstance(project_id, bool):
            raise GitLabPublicationError("GitLab project lookup returned an invalid id")

        assignee_id: int | None = None
        if assignee is not None:
            users = _json(
                client.get("/users", params={"username": assignee}), operation="assignee lookup"
            )
            if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], Mapping):
                raise GitLabPublicationError("GitLab assignee is missing or ambiguous")
            raw_assignee_id = users[0].get("id")
            if not isinstance(raw_assignee_id, int) or isinstance(raw_assignee_id, bool):
                raise GitLabPublicationError("GitLab assignee is invalid")
            assignee_id = raw_assignee_id

        raw_matches = _json(
            client.get(
                f"/projects/{project_id}/merge_requests",
                params={
                    "state": "opened",
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                    "per_page": 100,
                },
            ),
            operation="merge-request lookup",
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
            response = client.put(f"/projects/{project_id}/merge_requests/{iid}", json=payload)
            outcome: Literal["created", "updated"] = "updated"
        else:
            response = client.post(f"/projects/{project_id}/merge_requests", json=payload)
            outcome = "created"
        result = _json(response, operation=f"merge-request {outcome}")
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
