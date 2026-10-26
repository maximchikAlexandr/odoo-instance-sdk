"""Dispatch explicit repository Git commands through the right repository policy."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from odoo_instance_sdk.exceptions import GitRepositoryError, GitWorkflowError, PlanValidationError
from odoo_instance_sdk.internal.git_frontier import (
    CredentialOptions,
    credentials as resolve_credentials,
    environment,
)
from odoo_instance_sdk.internal.git_repository import (
    normalize_repository_url,
    repository_push_origin,
    resolve_repository,
    validate_credentialed_transport_configuration,
)
from odoo_instance_sdk.models import GitRepositorySnapshot, GitSyncResult

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command


class _GitCommandFactory(Protocol):
    def __call__(
        self, *, repository: Path | str, member_id: str, base: str, push: bool
    ) -> Command[GitSyncResult]: ...


class _OdooCommandFactory(Protocol):
    def __call__(
        self,
        *,
        base: str,
        push: bool,
        environment: Mapping[str, str],
        secret_values: Sequence[str],
        runtime_environment: Callable[
            [], tuple[tuple[tuple[str, str], ...], Callable[[], None], tuple[str, ...]]
        ],
        remote_allowed: Callable[[str], bool],
        hooks_path: str,
        repository_snapshot: GitRepositorySnapshot,
    ) -> Command[GitSyncResult]: ...


def explicit_commit_environment(member_id: str, host: str) -> dict[str, str]:
    _selected, verified = resolve_credentials(
        member_id,
        host,
        credential=None,
        identity=None,
        user_root=None,
        mapping_path=None,
        environment_path=None,
        project_root=Path.cwd(),
        process_environment=None,
        identity_transport=None,
    )
    return {
        "GIT_AUTHOR_NAME": verified.author_name,
        "GIT_AUTHOR_EMAIL": verified.author_email,
        "GIT_COMMITTER_NAME": verified.committer_name,
        "GIT_COMMITTER_EMAIL": verified.committer_email,
    }


def explicit_sync_command(
    repository: Path | str,
    member_id: str,
    base: str,
    push: bool,
    *,
    bound_root: Path,
    odoo_command: _OdooCommandFactory,
    generic_command: _GitCommandFactory,
) -> Command[GitSyncResult]:
    if not base:
        raise PlanValidationError("generic sync requires repository, member_id, and base")
    snapshot = resolve_repository(repository)
    if not snapshot.initialized_odoo:
        return generic_command(repository=repository, member_id=member_id, base=base, push=push)
    if snapshot.canonical_root != str(bound_root):
        raise GitWorkflowError("initialized Odoo root is not the bound Git worktree")
    selected, _verified = resolve_credentials(
        member_id,
        snapshot.host,
        credential=None,
        identity=None,
        user_root=None,
        mapping_path=None,
        environment_path=None,
        project_root=Path(snapshot.canonical_root),
        process_environment=None,
        identity_transport=None,
    )
    if push and repository_push_origin(snapshot.canonical_root) != snapshot.origin:
        raise GitRepositoryError("Git push origin does not match the captured repository origin")
    validate_credentialed_transport_configuration(Path(snapshot.canonical_root))
    options: CredentialOptions = environment((selected,), None)

    def remote_allowed(value: str) -> bool:
        try:
            return normalize_repository_url(value)[0] == snapshot.origin
        except GitRepositoryError:
            return False

    return odoo_command(
        base=base,
        push=push,
        environment=options["environment"],
        secret_values=options["secrets"],
        runtime_environment=options["runtime_environment"],
        remote_allowed=remote_allowed,
        hooks_path=os.devnull,
        repository_snapshot=snapshot,
    )
