"""Host-scoped Git credential helper used by frozen generic Git steps."""

from __future__ import annotations

import os
import sys

from odoo_instance_sdk.exceptions import GitLabCredentialError, GitLabIdentityError
from odoo_instance_sdk.internal.gitlab_credentials import (
    normalize_gitlab_host,
    resolve_gitlab_credential,
)
from odoo_instance_sdk.internal.gitlab_identity import verify_gitlab_cache_credential
from odoo_instance_sdk.internal.transport.gitlab import GitLabHttpClient

_MAX_REQUEST_BYTES = 4096
_CACHE_READ = "cache-read"


def _request() -> dict[str, str] | None:
    raw = sys.stdin.read(_MAX_REQUEST_BYTES + 1)
    if len(raw.encode("utf-8")) > _MAX_REQUEST_BYTES or not raw.endswith("\n\n"):
        return None
    fields: dict[str, str] = {}
    for line in raw[:-2].split("\n"):
        key, separator, value = line.partition("=")
        if not separator or key not in {"protocol", "host", "path", "username"}:
            return None
        if key in fields or not value or any(character in value for character in "\r\n"):
            return None
        fields[key] = value
    if fields.get("protocol") != "https" or "host" not in fields:
        return None
    try:
        if normalize_gitlab_host(fields["host"]) != fields["host"]:
            return None
    except GitLabCredentialError:
        return None
    return fields


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments != ["get"]:
        return 1
    fields = _request()
    if fields is None:
        return 1
    host = fields["host"]
    purpose = os.environ.get("ODCLI_GITLAB_PURPOSE")
    if purpose != _CACHE_READ:
        return 1
    selected_host = os.environ.get("ODCLI_GITLAB_SELECTED_HOST")
    try:
        selected_host = normalize_gitlab_host(selected_host or "")
        request_host = normalize_gitlab_host(host)
    except GitLabCredentialError:
        return 1
    if request_host != selected_host:
        return 1
    member_id = os.environ.get("ODCLI_GITLAB_MEMBER_ID", "")
    try:
        credential = resolve_gitlab_credential(
            member_id,
            host,
            user_root=os.environ.get("ODCLI_GITLAB_USER_ROOT"),
            mapping_path=os.environ.get("ODCLI_GITLAB_MAPPING_PATH"),
            environment_path=os.environ.get("ODCLI_GITLAB_ENVIRONMENT_PATH"),
        )
        verify_gitlab_cache_credential(credential, GitLabHttpClient())
    except (GitLabCredentialError, GitLabIdentityError, KeyError, OSError, ValueError):
        return 1
    print(f"username={credential.login}")
    print(f"password={credential._token}")
    return 0


if __name__ == "__main__":  # pragma: no cover - invoked by Git
    raise SystemExit(main())
