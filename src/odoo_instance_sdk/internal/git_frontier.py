"""Credential-scoped planning helpers for generic Git submodule frontiers."""

from __future__ import annotations

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
    cleanup_askpass_channel,
    https_host,
    packaged_askpass,
    resolve_gitlab_credential,
    write_askpass_channel,
)
from odoo_instance_sdk.internal.gitlab_identity import verify_gitlab_identity
from odoo_instance_sdk.internal.transport.gitlab import GitLabHttpClient
from odoo_instance_sdk.models import GitLabCredentialContext, GitLabIdentity

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.gitlab_identity import GitLabUserTransport


type RuntimeEnvironmentFactory = Callable[
    [], tuple[tuple[tuple[str, str], ...], Callable[[], None], tuple[str, ...]]
]


class CredentialOptions(TypedDict):
    environment: Mapping[str, str]
    secrets: Sequence[str]
    helper: str | None
    runtime_environment: RuntimeEnvironmentFactory


def credentials(
    member_id: str,
    host: str,
    *,
    credential: GitLabCredentialContext | None,
    identity: GitLabIdentity | None,
    user_root: Path | str | None,
    mapping_path: Path | str | None,
    environment_path: Path | str | None,
    project_root: Path | str | None,
    process_environment: Mapping[str, str] | None,
    identity_transport: GitLabUserTransport | Callable[[str, str], Mapping[str, JsonValue]] | None,
) -> tuple[GitLabCredentialContext, GitLabIdentity]:
    selected = credential or resolve_gitlab_credential(
        member_id,
        host,
        user_root=user_root,
        mapping_path=mapping_path,
        environment_path=environment_path,
        project_root=project_root,
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
    del credentials
    return packaged_askpass()


def _runtime_environment(
    credentials: Sequence[GitLabCredentialContext],
) -> RuntimeEnvironmentFactory:
    """Create an owner-only askpass channel only for an executing child."""

    frozen = tuple(credentials)

    def materialize() -> tuple[tuple[tuple[str, str], ...], Callable[[], None], tuple[str, ...]]:
        channel = write_askpass_channel(frozen)
        try:
            values = dict(frozen[0].askpass_environment(packaged_askpass(), channel_path=channel))
            values.pop("GITLAB_TOKEN", None)
        except BaseException:
            cleanup_askpass_channel(channel)
            raise
        return (
            tuple(sorted(values.items())),
            lambda: cleanup_askpass_channel(channel),
            tuple(item._token for item in frozen),
        )

    return materialize


def _base_environment(credential: GitLabCredentialContext) -> dict[str, str]:
    values = dict(credential.askpass_environment(packaged_askpass(), channel_path=""))
    values.pop("ODCLI_GITLAB_ASKPASS_FILE", None)
    values.pop("GITLAB_TOKEN", None)
    return values


def environment(
    credentials: Sequence[GitLabCredentialContext],
    askpass: str | None,
    *,
    exact_host: bool = False,
) -> CredentialOptions:
    del exact_host, askpass
    if not credentials:
        raise PlanValidationError("Git askpass requires at least one credential")
    values = _base_environment(credentials[0])
    return {
        "environment": values,
        "secrets": tuple(item._token for item in credentials),
        "helper": None,
        "runtime_environment": _runtime_environment(credentials),
    }


def frontier_environment(
    selected: GitLabCredentialContext,
    *,
    member_id: str,
    user_root: Path | str | None,
    mapping_path: Path | str | None,
    environment_path: Path | str | None,
    include_selected: bool,
    frontier_credentials: Sequence[GitLabCredentialContext] | None = None,
    credentials_provider: Callable[[], Sequence[GitLabCredentialContext]] | None = None,
) -> CredentialOptions:
    """Capture only validated frontier credentials through packaged askpass."""
    del member_id, user_root, mapping_path, environment_path, include_selected
    values_for_child = tuple(frontier_credentials or (selected,))
    if credentials_provider is not None and frontier_credentials is not None:
        raise PlanValidationError("frontier credentials must use one source")
    if not values_for_child:
        raise PlanValidationError("Git askpass requires at least one frontier credential")

    if credentials_provider is None:
        runtime = _runtime_environment(values_for_child)
    else:

        def runtime() -> tuple[tuple[tuple[str, str], ...], Callable[[], None], tuple[str, ...]]:
            return _runtime_environment(tuple(credentials_provider()))()

    values = _base_environment(selected)
    return {
        "environment": values,
        "secrets": tuple(item._token for item in values_for_child),
        "helper": None,
        "runtime_environment": runtime,
    }


def _frontier_checkouts(
    root: Path, depth: int | None = None
) -> tuple[tuple[Path, tuple[tuple[str, str, str], ...]], ...]:
    root = root.resolve()
    checkouts: tuple[Path, ...] = (root,)
    seen: set[Path] = {root}
    frontier = checkouts
    current_depth = 0
    while frontier and (depth is None or current_depth < depth):
        children: list[Path] = []
        for checkout in frontier:
            for path, _url, _host in submodule_urls(checkout):
                child = (checkout / path).resolve()
                if child.is_relative_to(root) and child.is_dir():
                    if child in seen:
                        raise PlanValidationError(
                            "recursive submodule checkout frontier repeats a resolved checkout"
                        )
                    seen.add(child)
                    children.append(child)
        frontier = tuple(dict.fromkeys(children))
        checkouts += frontier
        current_depth += 1
    return tuple((checkout, submodule_urls(checkout)) for checkout in checkouts)


def _credentials_for_hosts(
    member_id: str,
    hosts: Sequence[str],
    *,
    selected: GitLabCredentialContext,
    user_root: Path | str | None,
    mapping_path: Path | str | None,
    environment_path: Path | str | None,
    project_root: Path | str,
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
            project_root=project_root,
            process_environment=process_environment,
            identity_transport=identity_transport,
        )
        result.append(credential)
    return tuple(result)


def validated_frontier_credentials(
    member_id: str,
    root: Path,
    depth: int | None = None,
    *,
    selected: GitLabCredentialContext,
    user_root: Path | str | None,
    mapping_path: Path | str | None,
    environment_path: Path | str | None,
    process_environment: Mapping[str, str] | None,
    identity_transport: GitLabUserTransport | Callable[[str, str], Mapping[str, JsonValue]] | None,
    project_root: Path | str | None = None,
) -> tuple[GitLabCredentialContext, ...]:
    """Validate one complete frontier and return its exact host-bound credentials."""
    hosts: set[str] = set()
    for checkout, values in _frontier_checkouts(root, depth):
        checkout_hosts = {
            host for _path, url, _host in values if (host := https_host(url)) is not None
        }
        validate_submodule_configuration(checkout, allowed_hosts=tuple(sorted(checkout_hosts)))
        hosts.update(checkout_hosts)
    if not hosts:
        return ()
    return _credentials_for_hosts(
        member_id,
        tuple(hosts),
        selected=selected,
        user_root=user_root,
        mapping_path=mapping_path,
        environment_path=environment_path,
        project_root=root if project_root is None else project_root,
        process_environment=process_environment,
        identity_transport=identity_transport,
    )


def validate_frontier(
    member_id: str,
    root: Path,
    depth: int | None = None,
    *,
    selected: GitLabCredentialContext,
    user_root: Path | str | None,
    mapping_path: Path | str | None,
    environment_path: Path | str | None,
    process_environment: Mapping[str, str] | None,
    identity_transport: GitLabUserTransport | Callable[[str, str], Mapping[str, JsonValue]] | None,
    project_root: Path | str | None = None,
) -> tuple[str, ...]:
    credentials = validated_frontier_credentials(
        member_id,
        root,
        depth,
        selected=selected,
        user_root=user_root,
        mapping_path=mapping_path,
        environment_path=environment_path,
        project_root=project_root,
        process_environment=process_environment,
        identity_transport=identity_transport,
    )
    return tuple(item.host for item in credentials)
