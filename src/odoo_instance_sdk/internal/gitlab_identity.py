"""Bounded GitLab identity verification over the existing HTTP boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from odoo_instance_sdk.exceptions import GitLabIdentityError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.models import GitLabCredentialContext, GitLabIdentity


class GitLabUserTransport(Protocol):
    def get_user(self, host: str, token: str) -> Mapping[str, JsonValue]: ...


def _required_text(payload: Mapping[str, JsonValue], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def verify_gitlab_identity(
    credential: GitLabCredentialContext,
    transport: GitLabUserTransport | Callable[[str, str], Mapping[str, JsonValue]],
) -> GitLabIdentity:
    """Verify `/api/v4/user` before any credentialed child or mutation."""
    try:
        if callable(transport):
            payload = transport(credential.host, credential._token)
        else:
            payload = transport.get_user(credential.host, credential._token)
    except GitLabIdentityError:
        raise
    except Exception as exc:
        raise GitLabIdentityError("GitLab identity verification failed") from exc
    user_id = payload.get("id")
    username = payload.get("username")
    name = _required_text(payload, "name")
    email = _required_text(payload, "commit_email", "public_email", "email")
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise GitLabIdentityError("GitLab identity response has no usable user ID")
    if not isinstance(username, str) or username != credential.login:
        raise GitLabIdentityError("GitLab token owner does not match configured login")
    if name is None or email is None:
        raise GitLabIdentityError("GitLab identity has no usable commit name or email")
    return GitLabIdentity(
        user_id=user_id,
        username=username,
        name=name,
        email=email,
        host=credential.host,
    )


verify_gitlab_user = verify_gitlab_identity

__all__ = ["GitLabUserTransport", "verify_gitlab_identity", "verify_gitlab_user"]
