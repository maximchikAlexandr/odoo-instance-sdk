"""Shared monitor inventory context helpers."""

from __future__ import annotations

from odoo_instance_sdk.commands.context import CliContext, resolve_project_path
from odoo_instance_sdk.exceptions import ProjectContextError
from odoo_instance_sdk.internal.git_worktree import (
    rev_parse_git_common_dir,
    rev_parse_toplevel,
)
from odoo_instance_sdk.internal.repo_key import repo_key


def resolve_monitor_project_id(ctx: CliContext, all_projects: bool) -> str | None:
    """Resolve the monitor project filter from CLI context."""
    if all_projects:
        return None
    try:
        project_path = resolve_project_path(ctx)
    except ProjectContextError:
        return None
    repo_root = rev_parse_toplevel(project_path)
    git_common = rev_parse_git_common_dir(repo_root)
    return f"project_{repo_key(repo_root, git_common)}"
