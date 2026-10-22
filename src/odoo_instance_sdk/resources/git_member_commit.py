from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import GitWorkflowError, PlanValidationError
from odoo_instance_sdk.internal.git_repository import resolve_repository
from odoo_instance_sdk.models.git import GitCommitContext

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance


def member_commit_environment(
    instance: OdooInstance,
    context: GitCommitContext | None,
    description: str | None,
    repository: Path | str | None,
    member_id: str | None,
    ticket: str | None,
    generic_commit: Callable[..., object],
) -> tuple[Mapping[str, str] | None, object | None]:
    if member_id is None:
        raise PlanValidationError("explicit repository commit requires member_id")
    if context is not None or description is None:
        raise PlanValidationError(
            "member commit does not accept a prebuilt Odoo commit context"
            if context is not None
            else "commit description is required"
        )
    from odoo_instance_sdk.resources.git_runtime import worktree as _worktree

    selected_root = Path(repository).expanduser() if repository is not None else _worktree(instance)
    snapshot = resolve_repository(selected_root)
    if not snapshot.initialized_odoo:
        if repository is None:
            raise GitWorkflowError(
                "project-aware member commit requires an initialized Odoo worktree"
            )
        return None, generic_commit(
            description, repository=repository, member_id=member_id, ticket=ticket
        )
    if repository is not None and snapshot.canonical_root != str(_worktree(instance)):
        raise GitWorkflowError("initialized Odoo root is not the bound Git worktree")
    from odoo_instance_sdk.internal.git_explicit import explicit_commit_environment

    return explicit_commit_environment(member_id, snapshot.host), None
