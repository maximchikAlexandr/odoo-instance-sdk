"""Credential-scoped planning helpers for generic Git submodule frontiers."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from odoo_instance_sdk.exceptions import PlanValidationError
from odoo_instance_sdk.internal.git_repository import (
    submodule_urls,
    validate_submodule_configuration,
)
from odoo_instance_sdk.internal.gitlab_credentials import (
    https_host,
    resolve_gitlab_credential,
)
from odoo_instance_sdk.internal.gitlab_identity import verify_gitlab_identity
from odoo_instance_sdk.internal.transport.gitlab import GitLabHttpClient
from odoo_instance_sdk.models import GitLabCredentialContext, GitLabIdentity

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.gitlab_identity import GitLabUserTransport


class CredentialOptions(TypedDict):
    environment: Mapping[str, str]
    secrets: Sequence[str]
    helper: str | None


def credentials(
    member_id: str,
    host: str,
    *,
    credential: GitLabCredentialContext | None,
    identity: GitLabIdentity | None,
    user_root: Path | str | None,
    mapping_path: Path | str | None,
    environment_path: Path | str | None,
    process_environment: Mapping[str, str] | None,
    identity_transport: GitLabUserTransport | Callable[[str, str], Mapping[str, JsonValue]] | None,
) -> tuple[GitLabCredentialContext, GitLabIdentity]:
    selected = credential or resolve_gitlab_credential(
        member_id,
        host,
        user_root=user_root,
        mapping_path=mapping_path,
        environment_path=environment_path,
        process_environment=process_environment,
    )
    verified = identity
    if verified is None:
        verified = verify_gitlab_identity(selected, identity_transport or GitLabHttpClient())
    if verified.host != selected.host or verified.username != selected.login:
        raise PlanValidationError("verified GitLab identity does not match selected member")
    return selected, verified


def host_key(host: str) -> str:
    return sha256(host.encode("utf-8")).hexdigest()[:16].upper()


def credential_helper(credentials: Sequence[GitLabCredentialContext]) -> str:
    cases = []
    for credential in credentials:
        key = host_key(credential.host)
        cases.append(
            f"{shlex.quote(credential.host)}) printf 'username=%s\\npassword=%s\\n' "
            f'"$ODCLI_GITLAB_LOGIN_{key}" "$ODCLI_GITLAB_TOKEN_{key}";;'
        )
    body = " ".join(cases)
    return (
        "!f() { host=''; while IFS='=' read -r key value; do "
        '[ "$key" = host ] && host="$value"; done; '
        f'case "$host" in {body} *) return 1;; esac; }}; f'
    )


def environment(
    credentials: Sequence[GitLabCredentialContext],
    askpass: str | None,
    *,
    exact_host: bool = False,
) -> CredentialOptions:
    if exact_host or len(credentials) > 1:
        values: dict[str, str] = {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        values.update(
            {
                f"ODCLI_GITLAB_LOGIN_{host_key(credential.host)}": credential.login
                for credential in credentials
            }
        )
        values.update(
            {
                f"ODCLI_GITLAB_TOKEN_{host_key(credential.host)}": credential._token
                for credential in credentials
            }
        )
        return {
            "environment": values,
            "secrets": tuple(credential._token for credential in credentials),
            "helper": credential_helper(credentials),
        }
    credential = credentials[0]
    helper = askpass or os.environ.get("ODCLI_GIT_ASKPASS") or shutil.which("odcli-multica-askpass")
    values = dict(
        credential.askpass_environment(helper or "odcli-multica-askpass", channel_path="")
    )
    values["GITLAB_TOKEN"] = credential._token
    return {"environment": values, "secrets": credential._redaction_secrets(), "helper": None}


def _mapping_credential_helper() -> str:
    executable = shlex.quote(sys.executable)
    return f"!exec {executable} -m odoo_instance_sdk.internal.git_credential_helper"


def frontier_environment(
    selected: GitLabCredentialContext,
    *,
    member_id: str,
    user_root: Path | str | None,
    mapping_path: Path | str | None,
    environment_path: Path | str | None,
    include_selected: bool,
) -> CredentialOptions:
    """Capture lazy exact-host lookup without preloading unrelated tokens."""
    values: dict[str, str] = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "ODCLI_GITLAB_MEMBER_ID": member_id,
    }
    for name, value in (
        ("ODCLI_GITLAB_USER_ROOT", user_root),
        ("ODCLI_GITLAB_MAPPING_PATH", mapping_path),
        ("ODCLI_GITLAB_ENVIRONMENT_PATH", environment_path),
    ):
        if value is not None:
            values[name] = str(value)
    secrets: tuple[str, ...] = ()
    if include_selected:
        key = host_key(selected.host)
        values.update(
            {
                "ODCLI_GITLAB_SELECTED_HOST": selected.host,
                f"ODCLI_GITLAB_LOGIN_{key}": selected.login,
                f"ODCLI_GITLAB_TOKEN_{key}": selected._token,
            }
        )
        secrets = (selected._token,)
    return {
        "environment": values,
        "secrets": secrets,
        "helper": _mapping_credential_helper(),
    }


def _frontier_checkouts(
    root: Path, depth: int
) -> tuple[tuple[Path, tuple[tuple[str, str, str], ...]], ...]:
    checkouts: tuple[Path, ...] = (root.resolve(),)
    for _ in range(depth):
        children: list[Path] = []
        for checkout in checkouts:
            for path, _url, _host in submodule_urls(checkout):
                child = (checkout / path).resolve()
                if child.is_relative_to(root.resolve()) and child.is_dir():
                    children.append(child)
        checkouts = tuple(dict.fromkeys(children))
    return tuple((checkout, submodule_urls(checkout)) for checkout in checkouts)


def _credentials_for_hosts(
    member_id: str,
    hosts: Sequence[str],
    *,
    selected: GitLabCredentialContext,
    user_root: Path | str | None,
    mapping_path: Path | str | None,
    environment_path: Path | str | None,
    process_environment: Mapping[str, str] | None,
    identity_transport: GitLabUserTransport | Callable[[str, str], Mapping[str, JsonValue]] | None,
) -> tuple[GitLabCredentialContext, ...]:
    result = []
    for host in sorted(set(hosts)):
        if host == selected.host:
            result.append(selected)
            continue
        credential, _identity = credentials(
            member_id,
            host,
            credential=None,
            identity=None,
            user_root=user_root,
            mapping_path=mapping_path,
            environment_path=environment_path,
            process_environment=process_environment,
            identity_transport=identity_transport,
        )
        result.append(credential)
    return tuple(result)


def validate_frontier(
    member_id: str,
    root: Path,
    depth: int,
    *,
    selected: GitLabCredentialContext,
    user_root: Path | str | None,
    mapping_path: Path | str | None,
    environment_path: Path | str | None,
    process_environment: Mapping[str, str] | None,
    identity_transport: GitLabUserTransport | Callable[[str, str], Mapping[str, JsonValue]] | None,
) -> tuple[str, ...]:
    hosts: set[str] = set()
    for checkout, values in _frontier_checkouts(root, depth):
        checkout_hosts = {
            host for _path, url, _host in values if (host := https_host(url)) is not None
        }
        validate_submodule_configuration(checkout, allowed_hosts=tuple(sorted(checkout_hosts)))
        hosts.update(checkout_hosts)
    if hosts:
        _credentials_for_hosts(
            member_id,
            tuple(hosts),
            selected=selected,
            user_root=user_root,
            mapping_path=mapping_path,
            environment_path=environment_path,
            process_environment=process_environment,
            identity_transport=identity_transport,
        )
    return tuple(sorted(hosts))
