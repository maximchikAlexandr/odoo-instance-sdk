from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import GitWorkflowError, StalePlanError
from odoo_instance_sdk.internal.locks import exclusive_lock, provisioning_lock_path
from odoo_instance_sdk.internal.ticket_branch_allocation import (
    TicketAllocation,
    branch_matches_ticket,
    catalogue_branch_names,
    ensure_ticket_branch_absent,
    select_ticket_allocation,
    validate_ticket,
)
from odoo_instance_sdk.models import GitBranchRenameResult
from odoo_instance_sdk.resources.git import (
    _capture_probe,
    _plan,
    _planning_observation,
    _step,
    _text,
)
from odoo_instance_sdk.resources.git_runtime import worktree

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import ProcessResult, RunContext
    from odoo_instance_sdk.resources.git import GitResource


def _remote_names(value: str, ticket: str) -> tuple[str, ...]:
    names: set[str] = set()
    for line in value.splitlines():
        _, separator, ref = line.strip().partition("\t")
        if separator and ref.startswith("refs/heads/"):
            names.add(ref.removeprefix("refs/heads/"))
    return tuple(sorted(name for name in names if name == ticket or name.startswith(f"{ticket}_")))


def _catalogue_heads(client: OdooClient, repo_root: Path, git_common_dir: Path) -> tuple[str, ...]:
    try:
        return catalogue_branch_names(client, repo_root, git_common_dir)
    except Exception as error:
        raise GitWorkflowError("Git branch catalogue evidence is unavailable") from error


def _ensure_ticket_branch_absent(
    client: OdooClient,
    allocation: TicketAllocation,
    local_heads: set[str],
    remote_heads: set[str],
) -> None:
    try:
        ensure_ticket_branch_absent(client, allocation, local_heads, remote_heads)
    except StalePlanError:
        raise
    except Exception as error:
        raise GitWorkflowError("Git branch catalogue evidence is unavailable") from error


def rename_branch_command(resource: GitResource, ticket: str) -> Command[GitBranchRenameResult]:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import SubprocessExecutor

    validate_ticket(ticket)
    root = worktree(resource._instance)
    client = resource._instance._client
    branch_probe = _step(root, "git.rename.plan.branch", ("branch", "--show-current"))
    head_probe = _step(root, "git.rename.plan.head", ("rev-parse", "--verify", "HEAD^{commit}"))
    branch_result = _capture_probe(branch_probe, allow_failure=True)
    current_branch = _text(branch_result.stdout).strip()
    if branch_result.returncode != 0 or not current_branch:
        raise GitWorkflowError("Git branch rename requires an attached current branch")
    head_result = _capture_probe(head_probe)
    current_head = _text(head_result.stdout).strip()
    if branch_matches_ticket(ticket, current_branch):
        plan = _plan(
            (),
            observations=(
                _planning_observation("git-rename", (branch_probe, head_probe)),
                {
                    "kind": "rename",
                    "branch": current_branch,
                    "head": current_head,
                    "renamed": False,
                },
            ),
        )

        def noop(_: RunContext[GitBranchRenameResult]) -> GitBranchRenameResult:
            return GitBranchRenameResult(
                previous_branch=current_branch,
                selected_branch=current_branch,
                renamed=False,
            )

        return Command.create(plan, noop, (), executor=SubprocessExecutor())

    common_probe = _step(
        root,
        "git.rename.plan.common-dir",
        ("rev-parse", "--path-format=absolute", "--git-common-dir"),
    )
    local_probe = _step(
        root,
        "git.rename.plan.local-heads",
        ("for-each-ref", "--format=%(refname:short)", "refs/heads"),
    )
    origin_probe = _step(
        root,
        "git.rename.plan.origin-heads",
        ("ls-remote", "--heads", "origin", ticket, f"{ticket}_*"),
    )
    common_result = _capture_probe(common_probe)
    repo_root = root.resolve()
    git_common_dir = Path(_text(common_result.stdout).strip()).resolve()
    local_result = _capture_probe(local_probe)
    origin_result = _capture_probe(origin_probe)
    local_heads = tuple(sorted(set(_text(local_result.stdout).splitlines())))
    remote_heads = _remote_names(_text(origin_result.stdout), ticket)
    catalogue_heads = _catalogue_heads(client, repo_root, git_common_dir)
    allocation = select_ticket_allocation(
        ticket,
        repo_root,
        git_common_dir,
        "HEAD",
        local_heads,
        catalogue_heads,
        remote_heads,
    )

    branch_check = _step(root, "git.rename.revalidate.branch", ("branch", "--show-current"))
    head_check = _step(
        root, "git.rename.revalidate.head", ("rev-parse", "--verify", "HEAD^{commit}")
    )
    local_check = _step(
        root,
        "git.rename.revalidate.local-heads",
        ("for-each-ref", "--format=%(refname:short)", "refs/heads"),
    )
    origin_check = _step(
        root,
        "git.rename.revalidate.origin-heads",
        ("ls-remote", "--heads", "origin", ticket, f"{ticket}_*"),
    )
    rename_step = _step(
        root,
        "git.rename.branch",
        ("branch", "-m", allocation.branch),
        mutating=True,
    )
    all_steps = (branch_check, head_check, local_check, origin_check, rename_step)
    planning = (branch_probe, head_probe, common_probe, local_probe, origin_probe)
    plan = _plan(
        all_steps,
        observations=(
            _planning_observation("git-rename", planning),
            {
                "kind": "rename",
                "previous_branch": current_branch,
                "selected_branch": allocation.branch,
            },
        ),
    )

    def callback(context: RunContext[GitBranchRenameResult]) -> GitBranchRenameResult:
        with exclusive_lock(provisioning_lock_path()):
            branch_value = cast("ProcessResult", context.process(branch_check.step_id))
            head_value = cast("ProcessResult", context.process(head_check.step_id))
            actual_branch = _text(branch_value.stdout).strip()
            actual_head = _text(head_value.stdout).strip()
            if branch_value.returncode != 0 or head_value.returncode != 0:
                raise GitWorkflowError("Git rename precondition could not be revalidated")
            if actual_branch != current_branch or actual_head != current_head:
                raise StalePlanError(
                    "Git branch rename plan is stale",
                    expected={"branch": current_branch, "head": current_head},
                    actual={"branch": actual_branch, "head": actual_head},
                )
            local_value = cast("ProcessResult", context.process(local_check.step_id))
            origin_value = cast("ProcessResult", context.process(origin_check.step_id))
            if local_value.returncode != 0 or origin_value.returncode != 0:
                raise GitWorkflowError("Git branch collision evidence is unavailable")
            current_local = {
                line.strip() for line in _text(local_value.stdout).splitlines() if line.strip()
            }
            current_origin = set(_remote_names(_text(origin_value.stdout), ticket))
            _ensure_ticket_branch_absent(client, allocation, current_local, current_origin)
            rename_result = cast("ProcessResult", context.process(rename_step.step_id))
            if rename_result.returncode != 0:
                raise GitWorkflowError("Git branch rename failed")
            return GitBranchRenameResult(
                previous_branch=current_branch,
                selected_branch=allocation.branch,
                renamed=True,
            )

    return Command.create(plan, callback, all_steps, executor=SubprocessExecutor())
