"""Fail-closed filesystem cleanup for the project-removal coordinator."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import PreparedAction, RunContext
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


def project_owned_paths(root: Path) -> tuple[tuple[str, Path], ...]:
    """Return only canonical project-local artifacts with a known owner."""
    odcli = root / ".odcli"
    candidates = (
        ("config", odcli / "project.toml"),
        ("config", odcli / "odoo.conf"),
        ("secret", odcli / ".env"),
        ("temporary", odcli / "tmp"),
        ("log", odcli / "logs"),
        ("python_environment", root / ".venv"),
    )
    return tuple((kind, path) for kind, path in candidates if path.exists() or path.is_symlink())


class FilesystemPurgeAdapter:
    """Delete only exact contained files or the registered primary checkout."""

    def __init__(self, targets: tuple[FilesystemPurgeTarget, ...]) -> None:
        self._targets = {target.identity: target for target in targets}

    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None:
        return self if decision.identity in self._targets else None

    def capture(
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
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            if path.exists() or path.is_symlink():
                raise ConfigError("project filesystem deletion postcondition failed")
            context.complete_action(action.step_id)
            return decision

        return Command.create(
            ExecutionPlan(steps=(action.public_projection(),)),
            run,
            (action,),
        )


__all__ = ["FilesystemPurgeAdapter", "FilesystemPurgeTarget", "project_owned_paths"]
