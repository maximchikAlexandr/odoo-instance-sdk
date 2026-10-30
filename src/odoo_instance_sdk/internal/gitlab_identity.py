"""Bounded GitLab identity verification over the existing HTTP boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from odoo_instance_sdk.exceptions import GitLabIdentityError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.models import GitLabCredentialContext, GitLabIdentity


class GitLabUserTransport(Protocol):
    def get_user(self, host: str, token: str) -> Mapping[str, JsonValue]: ...


class GitLabCacheCredentialTransport(GitLabUserTransport, Protocol):
    def get_personal_access_token(self, host: str, token: str) -> Mapping[str, JsonValue]: ...


def _required_text(payload: Mapping[str, JsonValue], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _verify_gitlab_owner(
    credential: GitLabCredentialContext,
    transport: GitLabUserTransport | Callable[[str, str], Mapping[str, JsonValue]],
) -> tuple[int, str, Mapping[str, JsonValue]]:
    """Prove the runtime provider user and configured login agree."""
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
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise GitLabIdentityError("GitLab identity response has no usable user ID")
    if not isinstance(username, str) or username != credential.login:
        raise GitLabIdentityError("GitLab token owner does not match configured login")
    return user_id, username, payload


def verify_gitlab_identity(
    credential: GitLabCredentialContext,
    transport: GitLabUserTransport | Callable[[str, str], Mapping[str, JsonValue]],
) -> GitLabIdentity:
    """Verify `/api/v4/user` before any credentialed child or mutation."""
    user_id, username, payload = _verify_gitlab_owner(credential, transport)
    name = _required_text(payload, "name")
    email = _required_text(payload, "commit_email", "public_email", "email")
    if name is None or email is None:
        raise GitLabIdentityError("GitLab identity has no usable commit name or email")
    return GitLabIdentity(
        user_id=user_id,
        username=username,
        name=name,
        email=email,
        host=credential.host,
    )


def verify_gitlab_cache_credential(
    credential: GitLabCredentialContext,
    transport: GitLabCacheCredentialTransport,
) -> int:
    """Prove the configured login owns an active, read-only cache token."""
    identity_user_id, _username, _payload = _verify_gitlab_owner(credential, transport)
    try:
        token = transport.get_personal_access_token(credential.host, credential._token)
    except GitLabIdentityError:
        raise
    except Exception as exc:
        raise GitLabIdentityError("GitLab cache token information is unavailable") from exc
    token_owner_id = token.get("user_id")
    active = token.get("active")
    scopes = token.get("scopes")
    if (
        not isinstance(token_owner_id, int)
        or isinstance(token_owner_id, bool)
        or token_owner_id != identity_user_id
        or active is not True
        or not isinstance(scopes, list)
        or scopes != ["read_repository"]
    ):
        raise GitLabIdentityError("GitLab cache token is not an active read-only credential")
    return identity_user_id


verify_gitlab_user = verify_gitlab_identity

__all__ = [
    "GitLabCacheCredentialTransport",
    "GitLabUserTransport",
    "verify_gitlab_cache_credential",
    "verify_gitlab_identity",
    "verify_gitlab_user",
]
