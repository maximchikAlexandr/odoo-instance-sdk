"""Shared ticket branch selection used by checkout and Git rename."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import PlanValidationError, StalePlanError
from odoo_instance_sdk.internal import git_worktree
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import JsonValue


TICKET_EVIDENCE_LIMIT = 32
_TICKET_RE = re.compile(r"[A-Z][A-Z0-9]+-[1-9][0-9]*\Z")


def validate_ticket(ticket: str) -> str:
    if _TICKET_RE.fullmatch(ticket) is None:
        raise PlanValidationError("expected ticket like PROJ-123")
    return ticket


@dataclass(frozen=True, slots=True)
class TicketAllocation:
    ticket: str
    branch: str
    repo_root: Path
    git_common_dir: Path
    base_ref: str
    local_heads: tuple[str, ...]
    catalogue_heads: tuple[str, ...]
    remote_heads: tuple[str, ...]


def ticket_iteration(ticket: str, branch: str) -> int | None:
    if branch == ticket:
        return 0
    match = re.fullmatch(rf"{re.escape(ticket)}_([1-9][0-9]*)", branch)
    return int(match.group(1)) if match is not None else None


def catalogue_branch_names(
    client: OdooClient, repo_root: Path, git_common_dir: Path
) -> tuple[str, ...]:
    catalog = client.get_catalog()
    names: set[str] = set()
    for row in catalog.list_environments(git_common_dir=str(git_common_dir), include_removed=True):
        if (
            Path(str(row["repository_root"])).resolve() == repo_root
            and Path(str(row["git_common_dir"])).resolve() == git_common_dir
        ):
            branch = row["branch"]
            if isinstance(branch, str):
                names.add(branch)
    return tuple(sorted(names))


def resolve_ticket_allocation(
    client: OdooClient,
    project_path: Path,
    ticket: str,
    base_ref_override: str | None,
) -> TicketAllocation:
    validate_ticket(ticket)
    repo_root = git_worktree.rev_parse_toplevel(project_path)
    git_common_dir = git_worktree.rev_parse_git_common_dir(repo_root)
    project = ProjectConfig.load(repo_root)
    base_ref = base_ref_override or project.default_base_ref or "HEAD"
    local_heads = tuple(sorted(set(git_worktree.local_branch_names(repo_root))))
    catalogue_heads = tuple(sorted(set(catalogue_branch_names(client, repo_root, git_common_dir))))
    remote_heads = tuple(sorted(set(git_worktree.remote_branch_names(repo_root, ticket))))
    return select_ticket_allocation(
        ticket,
        repo_root,
        git_common_dir,
        base_ref,
        local_heads,
        catalogue_heads,
        remote_heads,
    )


def select_ticket_allocation(
    ticket: str,
    repo_root: Path,
    git_common_dir: Path,
    base_ref: str,
    local_heads: tuple[str, ...],
    catalogue_heads: tuple[str, ...],
    remote_heads: tuple[str, ...],
) -> TicketAllocation:
    validate_ticket(ticket)
    all_heads = (*local_heads, *catalogue_heads, *remote_heads)
    iterations = [
        iteration
        for branch in all_heads
        if (iteration := ticket_iteration(ticket, branch)) is not None
    ]
    next_iteration = max(iterations, default=-1) + 1
    branch = ticket if next_iteration == 0 else f"{ticket}_{next_iteration}"
    return TicketAllocation(
        ticket=ticket,
        branch=branch,
        repo_root=repo_root,
        git_common_dir=git_common_dir,
        base_ref=base_ref,
        local_heads=local_heads,
        catalogue_heads=catalogue_heads,
        remote_heads=remote_heads,
    )


def bounded_ticket_heads(heads: tuple[str, ...]) -> dict[str, JsonValue]:
    return {
        "heads": list(heads[:TICKET_EVIDENCE_LIMIT]),
        "total": len(heads),
        "truncated": len(heads) > TICKET_EVIDENCE_LIMIT,
    }


def ticket_provenance(allocation: TicketAllocation) -> dict[str, JsonValue]:
    return {
        "ticket_allocation": {
            "ticket": allocation.ticket,
            "resolved_branch": allocation.branch,
            "base_ref": allocation.base_ref,
            "evidence": {
                "local": bounded_ticket_heads(allocation.local_heads),
                "catalogue": bounded_ticket_heads(allocation.catalogue_heads),
                "origin": bounded_ticket_heads(allocation.remote_heads),
            },
        }
    }


def branch_matches_ticket(ticket: str, branch: str) -> bool:
    return ticket_iteration(ticket, branch) is not None


def ensure_ticket_branch_absent(
    client: OdooClient,
    allocation: TicketAllocation,
    local_heads: set[str],
    remote_heads: set[str],
) -> None:
    sources = (
        ("local", local_heads),
        (
            "catalogue",
            set(catalogue_branch_names(client, allocation.repo_root, allocation.git_common_dir)),
        ),
        ("origin", remote_heads),
    )
    for source, branches in sources:
        if allocation.branch in branches:
            raise StalePlanError(
                "Ticket branch allocation became stale",
                expected={"branch": allocation.branch, "absent": True},
                actual={"source": source, "branch": allocation.branch},
            )
