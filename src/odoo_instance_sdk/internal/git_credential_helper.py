"""Host-scoped Git credential helper used by frozen generic Git steps."""

from __future__ import annotations

import os
import sys
from hashlib import sha256

from odoo_instance_sdk.exceptions import GitLabCredentialError
from odoo_instance_sdk.internal.gitlab_credentials import resolve_gitlab_credential


def _request_host() -> str | None:
    for line in sys.stdin:
        key, separator, value = line.rstrip("\n").partition("=")
        if separator and key == "host":
            return value
    return None


def main() -> int:
    host = _request_host()
    if not host:
        return 1
    selected_host = os.environ.get("ODCLI_GITLAB_SELECTED_HOST")
    if host == selected_host:
        key = sha256(host.encode("utf-8")).hexdigest()[:16].upper()
        login = os.environ.get(f"ODCLI_GITLAB_LOGIN_{key}")
        token = os.environ.get(f"ODCLI_GITLAB_TOKEN_{key}")
        if login and token:
            print(f"username={login}")
            print(f"password={token}")
            return 0
    try:
        credential = resolve_gitlab_credential(
            os.environ["ODCLI_GITLAB_MEMBER_ID"],
            host,
            user_root=os.environ.get("ODCLI_GITLAB_USER_ROOT"),
            mapping_path=os.environ.get("ODCLI_GITLAB_MAPPING_PATH"),
            environment_path=os.environ.get("ODCLI_GITLAB_ENVIRONMENT_PATH"),
        )
    except (GitLabCredentialError, KeyError, OSError, ValueError):
        return 1
    print(f"username={credential.login}")
    print(f"password={credential._token}")
    return 0


if __name__ == "__main__":  # pragma: no cover - invoked by Git
    raise SystemExit(main())
