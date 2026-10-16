"""Small authenticated GitLab transport used only for identity verification."""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Literal, Protocol, cast
from urllib.parse import quote

from odoo_instance_sdk.exceptions import GitLabPublicationError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.proc.redaction import redacted_projection
from odoo_instance_sdk.internal.transport.base import (
    BaseHttpClient,
    RequestPayload,
    _RawHttpClient,
)
from odoo_instance_sdk.models import GitLabCredentialContext


class GitLabHttpClient(BaseHttpClient):
    """Bounded, no-retry `/user` client with token kept private in headers."""

    class _ClientFactory(Protocol):
        def __call__(self, *, timeout: float, trust_env: bool) -> _RawHttpClient: ...

    def __init__(self, *, client_factory: _ClientFactory | None = None) -> None:
        super().__init__(timeout=30.0, trust_env=True)
        self._client_factory = client_factory

    def _ensure_client(self) -> _RawHttpClient:
        if self._client is None:
            import httpx

            factory = self._client_factory or cast("GitLabHttpClient._ClientFactory", httpx.Client)
            self._client = factory(timeout=30.0, trust_env=True)
        return self._client

    def get_user(self, host: str, token: str) -> Mapping[str, JsonValue]:
        try:
            client = self._ensure_client()
            response = client.get(
                f"https://{host}/api/v4/user",
                headers={"PRIVATE-TOKEN": token},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            from odoo_instance_sdk.exceptions import GitLabIdentityError

            detail = redacted_projection(str(exc), secrets=(token,), field="error")
            raise GitLabIdentityError(f"GitLab identity verification failed: {detail}") from None
        if not isinstance(payload, Mapping):
            from odoo_instance_sdk.exceptions import GitLabIdentityError

            raise GitLabIdentityError("GitLab identity response is invalid")
        return cast("Mapping[str, JsonValue]", payload)

    def _request_json(
        self,
        credential: GitLabCredentialContext,
        method: Literal["GET", "POST", "PUT"],
        path: str,
        *,
        json: JsonValue | None = None,
        files: Mapping[str, tuple[str, io.BytesIO, str]] | None = None,
        operation: str,
    ) -> JsonValue:
        url = f"https://{credential.host}/api/v4{path}"
        try:
            client = self._ensure_client()
            kwargs: dict[str, RequestPayload] = {"headers": {"PRIVATE-TOKEN": credential._token}}
            if json is not None:
                kwargs["json"] = json
            if files is not None:
                kwargs["files"] = files
            if method == "GET":
                response = client.get(url, **kwargs)
            elif method == "POST":
                response = client.post(url, **kwargs)
            else:
                response = client.put(url, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            detail = redacted_projection(
                str(exc), secrets=credential._redaction_secrets(), field="error"
            )
            raise GitLabPublicationError(f"GitLab {operation} failed: {detail}") from None
        return payload

    def get_project(
        self, credential: GitLabCredentialContext, project: str
    ) -> Mapping[str, JsonValue]:
        payload = self._request_json(
            credential,
            "GET",
            f"/projects/{quote(project, safe='')}",
            operation="project lookup",
        )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("id"), int):
            raise GitLabPublicationError("GitLab project lookup returned an invalid id")
        return cast("Mapping[str, JsonValue]", payload)

    def list_open_merge_requests(
        self,
        credential: GitLabCredentialContext,
        project_id: int,
        source_branch: str,
        target_branch: str,
    ) -> tuple[Mapping[str, JsonValue], ...]:
        payload = self._request_json(
            credential,
            "GET",
            f"/projects/{project_id}/merge_requests?state=opened&source_branch={quote(source_branch)}"
            f"&target_branch={quote(target_branch)}&per_page=100",
            operation="merge-request lookup",
        )
        if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
            raise GitLabPublicationError("GitLab merge-request lookup returned an invalid response")
        return tuple(cast("Mapping[str, JsonValue]", item) for item in payload)

    def upload_project_file(
        self,
        credential: GitLabCredentialContext,
        project_id: int,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> Mapping[str, JsonValue]:
        payload = self._request_json(
            credential,
            "POST",
            f"/projects/{project_id}/uploads",
            files={"file": (filename, io.BytesIO(content), content_type)},
            operation="project image upload",
        )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("markdown"), str):
            raise GitLabPublicationError("GitLab project upload returned an invalid response")
        return cast("Mapping[str, JsonValue]", payload)

    def create_merge_request(
        self,
        credential: GitLabCredentialContext,
        project_id: int,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> Mapping[str, JsonValue]:
        return self._merge_request_mutation(
            credential,
            "POST",
            f"/projects/{project_id}/merge_requests",
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description=description,
            operation="merge-request create",
        )

    def update_merge_request(
        self,
        credential: GitLabCredentialContext,
        project_id: int,
        iid: int,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> Mapping[str, JsonValue]:
        return self._merge_request_mutation(
            credential,
            "PUT",
            f"/projects/{project_id}/merge_requests/{iid}",
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description=description,
            operation="merge-request update",
        )

    def _merge_request_mutation(
        self,
        credential: GitLabCredentialContext,
        method: Literal["POST", "PUT"],
        path: str,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
        operation: str,
    ) -> Mapping[str, JsonValue]:
        payload = self._request_json(
            credential,
            method,
            path,
            json={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
            },
            operation=operation,
        )
        if not isinstance(payload, Mapping):
            raise GitLabPublicationError(f"GitLab {operation} returned an invalid response")
        if not isinstance(payload.get("iid"), int) or not isinstance(payload.get("web_url"), str):
            raise GitLabPublicationError(f"GitLab {operation} returned an invalid merge request")
        return cast("Mapping[str, JsonValue]", payload)


__all__ = ["GitLabHttpClient"]
