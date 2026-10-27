from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import GitRepositoryError, GitWorkflowError, PlanValidationError
from odoo_instance_sdk.internal.git_repository import normalize_repository_url, resolve_repository
from odoo_instance_sdk.models.git import GitCommitContext, GitRepositorySnapshot

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedStep
    from odoo_instance_sdk.models import CommandResult
    from odoo_instance_sdk.resources.instance import OdooInstance


def member_commit_environment(
    instance: OdooInstance,
    context: GitCommitContext | None,
    description: str | None,
    repository: Path | str | None,
    member_id: str | None,
    native_binding: NativeMemberCommitBinding | None = None,
) -> Mapping[str, str] | None:
    if member_id is None:
        raise PlanValidationError("explicit repository commit requires member_id")
    if context is not None or description is None:
        raise PlanValidationError(
            "member commit does not accept a prebuilt Odoo commit context"
            if context is not None
            else "commit description is required"
        )
    if native_binding is not None:
        from odoo_instance_sdk.internal.git_explicit import explicit_commit_environment

        return explicit_commit_environment(
            member_id,
            native_binding.snapshot.host,
            project_root=Path(native_binding.snapshot.canonical_root),
        )
    from odoo_instance_sdk.resources.git_runtime import worktree as _worktree

    selected_root = Path(repository).expanduser() if repository is not None else _worktree(instance)
    snapshot = resolve_repository(selected_root)
    if not snapshot.initialized_odoo:
        if repository is None:
            _verify_registered_native_checkout(instance, snapshot)
            from odoo_instance_sdk.internal.git_explicit import explicit_commit_environment

            return explicit_commit_environment(
                member_id, snapshot.host, project_root=Path(snapshot.canonical_root)
            )
        binding = getattr(instance, "_runtime_binding", None)
        if binding is not None:
            _verify_registered_native_checkout(instance, snapshot)
            from odoo_instance_sdk.internal.git_explicit import explicit_commit_environment

            return explicit_commit_environment(
                member_id, snapshot.host, project_root=Path(snapshot.canonical_root)
            )
        return None
    if repository is not None and snapshot.canonical_root != str(_worktree(instance)):
        raise GitWorkflowError("initialized Odoo root is not the bound Git worktree")
    from odoo_instance_sdk.internal.git_explicit import explicit_commit_environment

    return explicit_commit_environment(
        member_id, snapshot.host, project_root=Path(snapshot.canonical_root)
    )


def _verify_registered_native_checkout(
    instance: OdooInstance, snapshot: GitRepositorySnapshot
) -> None:
    """Require an exact project/environment catalog binding for a manifest-free checkout."""
    from odoo_instance_sdk.exceptions import GitRepositoryError

    binding = getattr(instance, "_runtime_binding", None)
    if binding is None:
        raise GitWorkflowError("manifest-free member commit requires a registered checkout")
    failure = None
    try:
        catalog = instance._client.get_catalog()
        project = catalog.get_project(str(binding.project_id))
        if (
            project is None
            or project["registration_state"] != "registered"
            or Path(str(project["repository_root"])).resolve()
            != Path(binding.repository_root).resolve()
            or Path(str(project["git_common_dir"])).resolve()
            != Path(binding.git_common_dir).resolve()
        ):
            failure = "registered project binding does not match the selected instance"
        else:
            root = Path(snapshot.canonical_root)
            common = Path(snapshot.git_common_dir)
            environment_id = getattr(instance, "_environment_id", None)
        if failure is None and environment_id:
            environment = catalog.get_environment(str(environment_id))
            if (
                environment is None
                or str(environment["project_id"]) != str(binding.project_id)
                or str(environment["state"]) == "removed"
                or Path(str(environment["repository_root"])).resolve()
                != Path(binding.repository_root).resolve()
                or Path(str(environment["git_common_dir"])).resolve()
                != Path(binding.git_common_dir).resolve()
                or Path(str(environment["worktree_path"])).resolve() != root
                or Path(str(environment["checkout_git_common_dir"])).resolve() != common
            ):
                failure = "registered environment checkout binding is stale or foreign"
        elif failure is None:
            if root != Path(binding.repository_root).resolve():
                failure = "manifest-free project commit is outside the registered project"
            elif common != Path(binding.git_common_dir).resolve():
                failure = "native checkout Git common directory is not registered"
    except (GitRepositoryError, KeyError, OSError, TypeError, ValueError) as exc:
        raise GitWorkflowError("registered checkout binding could not be verified") from exc
    if failure is not None:
        raise GitWorkflowError(failure)


def capture_native_member_binding(
    instance: OdooInstance, repository: Path | str | None
) -> NativeMemberCommitBinding | None:
    """Capture a validated registered native checkout, when the manifest is absent."""
    from odoo_instance_sdk.resources.git_runtime import worktree as _worktree

    root = Path(repository).expanduser() if repository is not None else _worktree(instance)
    snapshot = resolve_repository(root)
    if snapshot.initialized_odoo or getattr(instance, "_runtime_binding", None) is None:
        return None
    _verify_registered_native_checkout(instance, snapshot)
    return NativeMemberCommitBinding(snapshot, native_member_binding_steps(snapshot))


def capture_member_binding(
    instance: OdooInstance, repository: Path | str | None, member_id: str | None
) -> NativeMemberCommitBinding | None:
    from odoo_instance_sdk.resources.git_runtime import worktree as _worktree

    if member_id is None or getattr(instance, "_runtime_binding", None) is None:
        return None
    root = Path(repository).expanduser() if repository is not None else _worktree(instance)
    if (root / ".odcli" / "project.toml").is_file():
        return None
    return capture_native_member_binding(instance, repository)


@dataclass(frozen=True, slots=True)
class NativeMemberCommitBinding:
    snapshot: GitRepositorySnapshot
    steps: tuple[PreparedStep, ...]

    def revalidate(self, instance: OdooInstance, process: Callable[[str], CommandResult]) -> None:
        evidence = tuple(process(step.step_id) for step in self.steps)
        revalidate_native_member_binding(instance, self.snapshot, evidence)


def native_member_binding_steps(snapshot: GitRepositorySnapshot) -> tuple[PreparedStep, ...]:
    from odoo_instance_sdk.internal.proc import PreparedStep

    root = snapshot.canonical_root
    return tuple(
        PreparedStep(
            step_id=f"git.commit.binding-{name}",
            argv=("git", "-C", root, *args),
            cwd=root,
            timeout=15.0,
            read_only=True,
        )
        for name, args in (
            ("root", ("rev-parse", "--show-toplevel")),
            ("common-dir", ("rev-parse", "--git-common-dir")),
            ("origin", ("remote", "get-url", "--all", "origin")),
        )
    )


def revalidate_native_member_binding(
    instance: OdooInstance,
    expected: GitRepositorySnapshot,
    evidence: tuple[CommandResult, ...],
) -> None:
    """Compare planned catalog and read-only Git evidence before a native commit."""
    if len(evidence) != 3:
        raise GitWorkflowError("native checkout evidence is unavailable")
    root_result, common_result, origin_result = evidence
    if any(item.returncode != 0 for item in evidence):
        raise GitWorkflowError("native checkout evidence is unavailable")
    root = Path(root_result.stdout.strip()).resolve()
    common_raw = Path(common_result.stdout.strip())
    common = (root / common_raw if not common_raw.is_absolute() else common_raw).resolve()
    origins = tuple(line.strip() for line in origin_result.stdout.splitlines() if line.strip())
    if len(origins) != 1:
        raise GitWorkflowError("native checkout origin is absent or ambiguous")
    try:
        origin, host, project = normalize_repository_url(origins[0])
    except GitRepositoryError as exc:
        raise GitWorkflowError("native checkout origin is invalid") from exc
    if (
        str(root) != expected.canonical_root
        or str(common) != expected.git_common_dir
        or origin != expected.origin
        or host != expected.host
        or project != expected.project
    ):
        raise GitWorkflowError("native checkout Git identity changed after commit planning")
    _verify_registered_native_checkout(instance, expected)
