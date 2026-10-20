"""Fail-closed filesystem cleanup for the project-removal coordinator."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.git_worktree import worktree_is_dirty
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    RunContext,
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.project_removal import canonical_containment
from odoo_instance_sdk.internal.project_removal_coordinator import ProjectRemovalAdapter
from odoo_instance_sdk.internal.repo_key import parse_git_common_dir
from odoo_instance_sdk.models.project_removal import ProjectResourceDecision


@dataclass(frozen=True, slots=True)
class FilesystemPurgeTarget:
    """A path whose ownership and containment were proven at capture time."""

    identity: str
    project_id: str
    path: Path
    allowed_root: Path
    git_common_dir: Path | None = None


def project_owned_paths(
    root: Path, *, managed_filestore: Path | None = None
) -> tuple[tuple[str, Path], ...]:
    """Return only canonical project-local artifacts with a known owner."""
    odcli = root / ".odcli"
    candidates = [
        ("config", odcli / "project.toml"),
        ("config", odcli / "odoo.conf"),
        ("secret", odcli / ".env"),
        ("temporary", odcli / "tmp"),
        ("log", odcli / "logs"),
        ("python_environment", root / ".venv"),
    ]
    if managed_filestore is not None:
        candidates.append(("filestore", managed_filestore))
    return tuple(
        (kind, path) for kind, path in candidates if kind and (path.exists() or path.is_symlink())
    )


def _assert_safe_directory(path: Path, *, source: bool) -> None:
    """Refuse a recursive delete when ownership or link traversal is ambiguous."""
    unknown_odcli = {"project.toml", "odoo.conf", ".env", "tmp", "logs", "filestore"}
    try:
        for directory, directories, files in os.walk(path, followlinks=False):
            directory_path = Path(directory)
            if any((directory_path / name).is_symlink() for name in (*directories, *files)):
                raise ConfigError("nested symlink in project filesystem target")
            if source and directory_path == path / ".odcli":
                unknown = set(directories).union(files) - unknown_odcli
                if unknown:
                    raise ConfigError("unknown project filesystem content was retained")
    except OSError as exc:
        raise ConfigError("project filesystem inventory failed") from exc


class FilesystemPurgeAdapter:
    """Delete only exact contained files or the registered primary checkout."""

    def __init__(self, targets: tuple[FilesystemPurgeTarget, ...]) -> None:
        self._targets = {target.identity: target for target in targets}

    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None:
        return self if decision.identity in self._targets else None

    def capture(  # noqa: C901
        self, decision: ProjectResourceDecision, *, project_id: str
    ) -> Command[ProjectResourceDecision] | None:
        target = self._targets.get(decision.identity)
        if target is None or target.project_id != project_id:
            return None
        action = PreparedAction(
            step_id=f"project-removal:filesystem:{decision.identity}",
            action="delete-filesystem-resource",
            description="Delete one proven contained project filesystem resource",
            details={"identity": decision.identity},
            mutating=True,
        )
        status_step = (
            PreparedStep(
                step_id=f"project-removal:filesystem:git-status:{decision.identity}",
                argv=(
                    "git",
                    "-C",
                    str(target.path),
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                ),
                read_only=True,
            )
            if decision.resource_type == "source" and (target.path / ".git" / "HEAD").is_file()
            else None
        )

        def run(context: RunContext[ProjectResourceDecision]) -> ProjectResourceDecision:
            context.action(action.step_id)
            path = target.path
            if path.is_symlink():
                raise ConfigError("project filesystem target is a symlink")
            if not canonical_containment(path, target.allowed_root):
                raise ConfigError("project filesystem target escaped its allowed root")
            if decision.resource_type == "source" and (
                target.git_common_dir is None or parse_git_common_dir(path) != target.git_common_dir
            ):
                raise ConfigError("registered primary source identity changed")
            if decision.resource_type == "source" and path.is_dir():
                _assert_safe_directory(path, source=True)
            if decision.resource_type == "source" and (
                status_step is None or worktree_is_dirty(path, step_id=status_step.step_id)
            ):
                raise ConfigError("registered primary source is not a clean Git worktree")
            if path.exists():
                if path.is_dir():
                    _assert_safe_directory(path, source=decision.resource_type == "source")
                    shutil.rmtree(path)
                else:
                    path.unlink()
            if path.exists() or path.is_symlink():
                raise ConfigError("project filesystem deletion postcondition failed")
            context.complete_action(action.step_id)
            return decision

        public_status_steps = tuple(
            step.public_projection() for step in ((status_step,) if status_step is not None else ())
        )
        return Command.create(
            ExecutionPlan(steps=(*public_status_steps, action.public_projection())),
            run,
            tuple(step for step in (status_step, action) if step is not None),
            executor=SubprocessExecutor() if status_step is not None else None,
        )


__all__ = ["FilesystemPurgeAdapter", "FilesystemPurgeTarget", "project_owned_paths"]
