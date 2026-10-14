"""Small authenticated GitLab transport used only for identity verification."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.transport.base import BaseHttpClient


class GitLabHttpClient(BaseHttpClient):
    """Bounded, no-retry `/user` client with token kept private in headers."""

    def __init__(self) -> None:
        super().__init__(timeout=30.0, trust_env=True)

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

            raise GitLabIdentityError("GitLab identity verification failed") from exc
        if not isinstance(payload, Mapping):
            from odoo_instance_sdk.exceptions import GitLabIdentityError

            raise GitLabIdentityError("GitLab identity response is invalid")
        return cast("Mapping[str, JsonValue]", payload)


__all__ = ["GitLabHttpClient"]
