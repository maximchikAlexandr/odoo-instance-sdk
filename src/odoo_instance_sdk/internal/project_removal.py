"""Read-only ownership projection for project removal.

The coordinator consumes this module, but this module never performs a
mutation.  It turns the existing local inventory into one deterministic,
secret-free decision graph and keeps the evidence needed to detect drift
inside the internal value.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from odoo_instance_sdk.internal.repo_key import parse_git_common_dir
from odoo_instance_sdk.internal.resource_inventory import (
    ActiveUse,
    MeasurementCompleteness,
    OwnershipConfidence,
    ResourceInventory,
    ResourceInventoryItem,
    ResourceType,
    stable_resource_identity,
)
from odoo_instance_sdk.internal.sanitize import sanitize_event_message
from odoo_instance_sdk.models.project_removal import (
    ProjectRemovalResult,
    ProjectResourceAction,
    ProjectResourceDecision,
    ProjectSelector,
)

RemovalReason = str
RegistrationValue = str | Path | bool | None


@dataclass(frozen=True, slots=True)
class ProjectRegistration:
    """The minimum durable project identity needed for exact selection."""

    project_id: str
    repository_root: Path
    git_common_dir: Path | None = None
    registration_state: Literal["registered", "forgotten"] = "registered"
    source_ownership: OwnershipConfidence = OwnershipConfidence.PROVEN
    linked_checkout: bool = False
    caller_owned_checkout: bool = False
    other_tool_managed_checkout: bool = False

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        if self.registration_state not in {"registered", "forgotten"}:
            raise ValueError("invalid project registration state")


@dataclass(frozen=True, slots=True)
class RemovalRuntimeSource:
    """A runtime observation that is not represented by ``ResourceInventory``."""

    project_id: str
    identity: str
    target: str = "runtime"
    active_use: ActiveUse = ActiveUse.UNKNOWN
    ownership: OwnershipConfidence = OwnershipConfidence.UNKNOWN
    stale: bool = False


@dataclass(frozen=True, slots=True)
class RemovalPublicationSource:
    """A publication observation that is not represented by the inventory DTO."""

    project_id: str
    identity: str
    target: str = "publication"
    active_use: ActiveUse = ActiveUse.UNKNOWN
    ownership: OwnershipConfidence = OwnershipConfidence.UNKNOWN


@dataclass(frozen=True, slots=True)
class RemovalPathSource:
    """A path with explicit ownership evidence and its allowed root."""

    project_id: str
    identity: str
    path: Path
    target: str
    kind: str = "file"
    allowed_root: Path | None = None
    ownership: OwnershipConfidence = OwnershipConfidence.UNKNOWN
    active_use: ActiveUse = ActiveUse.INACTIVE
    symlink_escape: bool = False
    unknown: bool = False
    relationships: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RemovalComposeSource:
    """A Compose identity and its exclusive-use evidence."""

    project_id: str
    identity: str
    target: str = "compose cluster"
    ownership: OwnershipConfidence = OwnershipConfidence.UNKNOWN
    active_use: ActiveUse = ActiveUse.UNKNOWN
    dependencies: tuple[str, ...] = ()
    labels_match: bool = False


@dataclass(frozen=True, slots=True)
class InternalRemovalDecision:
    """Decision plus evidence retained for execution-time revalidation."""

    identity: str
    resource_type: str
    action: ProjectResourceAction
    reason: RemovalReason
    target: str
    relationships: tuple[str, ...] = ()
    ownership: OwnershipConfidence = OwnershipConfidence.UNKNOWN
    active_use: ActiveUse = ActiveUse.UNKNOWN
    dependencies: tuple[str, ...] = ()
    evidence: tuple[tuple[str, str], ...] = ()

    def public(self) -> ProjectResourceDecision:
        return ProjectResourceDecision(
            identity=self.identity,
            resource_type=self.resource_type,
            action=self.action,
            reason=self.reason,
            target=self.target,
        )

    def canonical(
        self,
    ) -> tuple[str | tuple[str, ...] | tuple[tuple[str, str], ...], ...]:
        return (
            self.identity,
            self.resource_type,
            self.action,
            self.reason,
            self.target,
            self.relationships,
            self.ownership.value,
            self.active_use.value,
            self.dependencies,
            self.evidence,
        )


@dataclass(frozen=True, slots=True)
class ProjectRemovalProjection:
    """One immutable project-scoped projection and its drift fingerprint."""

    project_id: str
    registration_state: Literal["registered", "forgotten"]
    repository_root: Path
    expected_confirmation: str
    decisions: tuple[InternalRemovalDecision, ...]
    fingerprint: str
    complete: bool = True

    @property
    def public_decisions(self) -> tuple[ProjectResourceDecision, ...]:
        return tuple(decision.public() for decision in self.decisions)

    @property
    def retained(self) -> tuple[ProjectResourceDecision, ...]:
        return tuple(
            decision.public() for decision in self.decisions if decision.action == "retain"
        )

    def bounded_decisions(self, limit: int = 100) -> tuple[ProjectResourceDecision, ...]:
        """Return a deterministic public prefix without changing the fingerprint."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("decision limit must be an integer between 1 and 1000")
        return self.public_decisions[:limit]

    def result(
        self,
        *,
        mode: Literal["forget", "purge"] = "purge",
        registration_state: Literal["registered", "forgotten"] | None = None,
        limit: int = 100,
    ) -> ProjectRemovalResult:
        decisions = self.bounded_decisions(limit)
        retained = tuple(decision for decision in decisions if decision.action == "retain")
        remaining = tuple(decision for decision in decisions if decision.action != "retain")
        return ProjectRemovalResult(
            project_id=self.project_id,
            mode=mode,
            registration_state=registration_state or self.registration_state,
            retained=retained,
            remaining=remaining,
        )


def _canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _safe_target(value: str, *, path: Path | None = None) -> str:
    """Keep public targets relative and redact accidental diagnostic secrets."""
    if path is not None and os.path.isabs(value):
        value = f"<local>/{path.name}" if path.name else "<local>"
    return sanitize_event_message(value) or "<local>"


def canonical_containment(path: Path, root: Path) -> bool:
    """Check containment after resolving every existing path component.

    A symlink is never treated as owned merely because its textual path is
    below ``root``.  This is deliberately strict for missing paths too: a
    future target must still resolve below the same canonical root.
    """
    root_canonical = _canonical_path(root)
    path_canonical = _canonical_path(path)
    try:
        path_canonical.relative_to(root_canonical)
    except ValueError:
        return False
    current = path if path.is_absolute() else path.absolute()
    try:
        while current != current.parent:
            if os.path.lexists(current) and current.is_symlink():
                return False
            current = current.parent
    except OSError:
        return False
    return True


def expected_confirmation_token(repository_root: Path, project_id: str) -> str:
    """Build the exact purge token without exposing the absolute root path."""
    name = _canonical_path(repository_root).name
    if not name or not project_id.strip():
        raise ValueError("repository_root and project_id are required")
    return f"{name}:{project_id}"


def _registration(
    value: ProjectRegistration | Mapping[str, RegistrationValue],
) -> ProjectRegistration:
    if isinstance(value, ProjectRegistration):
        return value
    root = value.get("repository_root")
    project_id = value.get("project_id")
    if not isinstance(root, str | Path) or not isinstance(project_id, str):
        raise TypeError("project registration requires repository_root and project_id")
    common = value.get("git_common_dir")
    state = value.get("registration_state", "registered")
    if state not in {"registered", "forgotten"}:
        raise ValueError("invalid project registration state")
    ownership = value.get("source_ownership", OwnershipConfidence.PROVEN)
    if not isinstance(ownership, OwnershipConfidence):
        try:
            ownership = OwnershipConfidence(str(ownership))
        except ValueError as exc:
            raise ValueError("invalid source ownership") from exc
    return ProjectRegistration(
        project_id=project_id,
        repository_root=Path(root),
        git_common_dir=Path(common) if isinstance(common, str | Path) else None,
        registration_state=state,  # type: ignore[arg-type]
        source_ownership=ownership,
        linked_checkout=bool(value.get("linked_checkout", False)),
        caller_owned_checkout=bool(value.get("caller_owned_checkout", False)),
        other_tool_managed_checkout=bool(value.get("other_tool_managed_checkout", False)),
    )


def resolve_project_selector(
    selector: ProjectSelector,
    registrations: Iterable[ProjectRegistration | Mapping[str, RegistrationValue]],
) -> ProjectRegistration:
    """Resolve only exact full ids or exact canonical roots.

    There is intentionally no prefix matching.  A missing source root can be
    recovered only by its durable full id.
    """
    values = tuple(_registration(value) for value in registrations)
    if selector.project_id is not None:
        matches = tuple(value for value in values if value.project_id == selector.project_id)
        if len(matches) != 1:
            raise ValueError("unknown or non-unique full project_id")
        return matches[0]
    assert selector.path is not None
    requested = Path(selector.path)
    if requested.is_symlink():
        raise ValueError("project path must not be a symlink")
    if not requested.exists():
        raise ValueError("unknown canonical project path")
    canonical = _canonical_path(requested)
    matches = tuple(
        value for value in values if _canonical_path(value.repository_root) == canonical
    )
    if len(matches) != 1:
        raise ValueError("unknown canonical project path")
    return matches[0]


def _owner_projects(inventory: ResourceInventory) -> dict[str, frozenset[str]]:  # noqa: C901
    """Map resources to direct project owners without crossing shared nodes.

    Project and environment edges are ownership evidence.  A project edge is
    also projected onto its direct resource targets so the normal inventory
    shape (project -> database, environment -> backup) remains useful.  The
    projection deliberately does not walk arbitrary resource edges: doing so
    would turn a shared venv or database into a bridge to another project's
    worktree.  Shared nodes are classified, but never used as traversal
    sources.
    """
    direct_projects: dict[str, set[str]] = {}
    for item in inventory.resources:
        owners = {
            relationship.removeprefix("project:")
            for relationship in item.relationships
            if relationship.startswith("project:")
        }
        if item.stable_identity.startswith("project:"):
            owners.add(item.stable_identity.removeprefix("project:"))
        direct_projects[item.stable_identity] = owners

    owners_by_identity: dict[str, set[str]] = {
        identity: set(owners) for identity, owners in direct_projects.items()
    }
    for item in inventory.resources:
        owners = owners_by_identity[item.stable_identity]
        for relationship in item.relationships:
            if relationship.startswith("environment:"):
                owners.update(direct_projects.get(relationship, ()))

    resources = {item.stable_identity: item for item in inventory.resources}
    backups_by_database: dict[str, set[str]] = {}
    for item in inventory.resources:
        if item.type is ResourceType.BACKUP:
            for relationship in item.relationships:
                if (
                    resources.get(relationship) is not None
                    and resources[relationship].type is ResourceType.DATABASE
                ):
                    backups_by_database.setdefault(relationship, set()).add(item.stable_identity)

    def dependency_targets(item: ResourceInventoryItem) -> tuple[str, ...]:
        if item.type in {ResourceType.PROJECT, ResourceType.ENVIRONMENT}:
            return item.relationships
        if item.type is ResourceType.DATABASE:
            return tuple(
                relationship
                for relationship in item.relationships
                if resources.get(relationship) is not None
                and resources[relationship].type is ResourceType.BACKUP
            )
        return ()

    # Propagate ownership to dependency targets until no new owner appears.
    # Shared nodes may propagate only along the explicitly safe dependency
    # edges above; they never become arbitrary graph traversal bridges.
    changed = True
    while changed:
        changed = False
        for item in inventory.resources:
            owners = owners_by_identity[item.stable_identity]
            targets = set(dependency_targets(item))
            if item.type is ResourceType.DATABASE:
                targets.update(backups_by_database.get(item.stable_identity, ()))
            for relationship in targets:
                if relationship.startswith("project:"):
                    continue
                target_owners = owners_by_identity.setdefault(relationship, set())
                before = len(target_owners)
                target_owners.update(owners)
                changed |= len(target_owners) != before

    return {identity: frozenset(owners) for identity, owners in owners_by_identity.items()}


def _scope_ids(
    inventory: ResourceInventory,
    project_id: str,
    owner_projects: Mapping[str, frozenset[str]] | None = None,
) -> set[str]:
    """Return only resources directly owned by, or shared with, a project."""
    owners = owner_projects if owner_projects is not None else _owner_projects(inventory)
    return {identity for identity, project_ids in owners.items() if project_id in project_ids}


def _inventory_decision(  # noqa: C901
    item: ResourceInventoryItem,
    *,
    scoped: set[str],
    project_id: str,
    owner_projects: frozenset[str] = frozenset(),
) -> InternalRemovalDecision | None:
    relationships = tuple(sorted(set(item.relationships)))
    related = item.stable_identity in scoped
    if not related:
        return None
    reason: str
    action: ProjectResourceAction
    cross_project = any(
        relationship.startswith("project:") and relationship != f"project:{project_id}"
        for relationship in relationships
    )
    if (
        len(owner_projects) > 1
        or cross_project
        or item.ownership_confidence is OwnershipConfidence.SHARED
    ):
        action, reason = "retain", "shared_resource"
    elif item.ownership_confidence is OwnershipConfidence.EXTERNAL:
        action, reason = "retain", "external_resource"
    elif item.ownership_confidence is OwnershipConfidence.UNKNOWN:
        action, reason = "retain", "ownership_unproven"
    elif item.active_use is ActiveUse.ACTIVE:
        action, reason = "retain", "active_use"
    elif (
        item.type in {ResourceType.DATABASE, ResourceType.VOLUME}
        and item.completeness is not MeasurementCompleteness.COMPLETE
    ):
        action, reason = "retain", "observation_incomplete"
    elif (
        item.type in {ResourceType.DATABASE, ResourceType.VOLUME}
        and item.active_use is ActiveUse.UNKNOWN
    ):
        action, reason = "retain", "active_use_unproven"
    elif item.type is ResourceType.PROJECT:
        action, reason = "retain", "source_ownership_requires_git_provenance"
    elif item.type in {ResourceType.ENVIRONMENT, ResourceType.WORKTREE}:
        action, reason = "delete", "sdk_owned_environment_resource"
    elif item.type is ResourceType.BACKUP and len(relationships) > 1:
        action, reason = "retain", "referenced_by_shared_resource"
    elif item.type is ResourceType.VOLUME and not item.reclaimable:
        action, reason = "retain", "exclusive_use_not_proven"
    elif item.type is ResourceType.OTHER:
        action, reason = "retain", "unknown_file"
    else:
        action, reason = "delete", "exclusive_sdk_ownership"
    return InternalRemovalDecision(
        identity=item.stable_identity,
        resource_type=item.type.value,
        action=action,
        reason=reason,
        target=_safe_target(item.sanitized_path or item.name),
        relationships=relationships,
        ownership=item.ownership_confidence,
        active_use=item.active_use,
        dependencies=relationships,
        evidence=(
            ("project_id", project_id),
            ("complete", item.completeness.value),
            ("reclaimable", str(item.reclaimable).lower()),
        ),
    )


def _project_source_decision(
    project: ProjectRegistration,
    existing: InternalRemovalDecision | None,
) -> InternalRemovalDecision:
    """Allow source deletion only when the registered Git identity still matches."""
    root = _canonical_path(project.repository_root)
    common = parse_git_common_dir(root) if root.is_dir() else None
    clean = False
    if (
        root.is_dir()
        and common is not None
        and common == (_canonical_path(project.git_common_dir) if project.git_common_dir else None)
    ):
        if (root / ".git" / "HEAD").is_file():
            from odoo_instance_sdk.internal.git_worktree import worktree_is_dirty
            from odoo_instance_sdk.internal.proc import active_context

            clean = True if active_context() is not None else not worktree_is_dirty(root)
        else:
            # The guarded source adapter performs the execution-time check;
            # an incomplete test/durable identity must not be treated as a
            # capture-time dirty result.
            clean = True
    proven = (
        root.is_dir()
        and not project.repository_root.is_symlink()
        and project.source_ownership is OwnershipConfidence.PROVEN
        and not project.linked_checkout
        and not project.caller_owned_checkout
        and not project.other_tool_managed_checkout
        and project.git_common_dir is not None
        and common == _canonical_path(project.git_common_dir)
        and clean
    )
    action: ProjectResourceAction
    if proven:
        action, reason = "delete", "registered_primary_source"
    elif not root.exists():
        action, reason = "retain", "source_already_absent"
    else:
        action, reason = "retain", "source_git_provenance_unmatched"
    return InternalRemovalDecision(
        identity=f"project:{project.project_id}",
        resource_type="source",
        action=action,
        reason=reason,
        target=_safe_target(existing.target if existing is not None else "<local>"),
        relationships=existing.relationships if existing is not None else (),
        ownership=OwnershipConfidence.PROVEN if proven else OwnershipConfidence.UNKNOWN,
        active_use=ActiveUse.INACTIVE,
        evidence=(
            ("project_id", project.project_id),
            ("registered_root", str(root)),
            (
                "registered_common_dir",
                str(_canonical_path(project.git_common_dir)) if project.git_common_dir else "",
            ),
            ("observed_common_dir", str(common) if common else ""),
        ),
    )


def _path_decision(source: RemovalPathSource) -> InternalRemovalDecision:
    action: ProjectResourceAction
    if source.symlink_escape:
        action, reason = "retain", "symlink_or_path_escape"
    elif source.allowed_root is None:
        action, reason = "retain", "containment_unproven"
    elif not canonical_containment(source.path, source.allowed_root):
        action, reason = "retain", "symlink_or_path_escape"
    elif source.unknown:
        action, reason = "retain", "unknown_file"
    elif source.ownership is not OwnershipConfidence.PROVEN:
        action, reason = "retain", f"{source.ownership.value}_ownership"
    elif source.active_use is ActiveUse.ACTIVE:
        action, reason = "retain", "active_use"
    else:
        action, reason = "delete", "contained_exclusive_sdk_resource"
    return InternalRemovalDecision(
        identity=source.identity,
        resource_type=source.kind,
        action=action,
        reason=reason,
        target=_safe_target(source.target, path=source.path),
        relationships=tuple(sorted(set(source.relationships))),
        ownership=source.ownership,
        active_use=source.active_use,
        evidence=(
            ("canonical_path", str(_canonical_path(source.path))),
            ("project_id", source.project_id),
        ),
    )


def _runtime_decision(source: RemovalRuntimeSource) -> InternalRemovalDecision:
    action: ProjectResourceAction
    if source.ownership is not OwnershipConfidence.PROVEN:
        action, reason = "retain", "runtime_ownership_unproven"
    elif source.active_use is ActiveUse.ACTIVE or source.stale:
        action, reason = "stop", "owned_runtime_active_or_stale"
    else:
        action, reason = "retain", "runtime_already_stopped"
    return InternalRemovalDecision(
        identity=source.identity,
        resource_type="runtime",
        action=action,
        reason=reason,
        target=_safe_target(source.target),
        ownership=source.ownership,
        active_use=source.active_use,
        evidence=(("project_id", source.project_id), ("stale", str(source.stale).lower())),
    )


def _publication_decision(source: RemovalPublicationSource) -> InternalRemovalDecision:
    action: ProjectResourceAction
    if source.ownership is OwnershipConfidence.PROVEN and source.active_use is ActiveUse.ACTIVE:
        action, reason = "unpublish", "owned_publication_active"
    elif source.ownership is OwnershipConfidence.PROVEN:
        action, reason = "retain", "publication_already_unpublished"
    else:
        action, reason = "retain", "publication_ownership_unproven"
    return InternalRemovalDecision(
        identity=source.identity,
        resource_type="publication",
        action=action,
        reason=reason,
        target=_safe_target(source.target),
        ownership=source.ownership,
        active_use=source.active_use,
        evidence=(("project_id", source.project_id),),
    )


def _compose_decision(source: RemovalComposeSource) -> InternalRemovalDecision:
    can_delete = (
        source.ownership is OwnershipConfidence.PROVEN
        and source.active_use is ActiveUse.INACTIVE
        and source.labels_match
        and not source.dependencies
    )
    action: ProjectResourceAction
    action, reason = (
        ("delete", "exclusive_sdk_compose_resource")
        if can_delete
        else ("retain", "compose_identity_or_dependency_unproven")
    )
    return InternalRemovalDecision(
        identity=source.identity,
        resource_type="compose",
        action=action,
        reason=reason,
        target=_safe_target(source.target),
        ownership=source.ownership,
        active_use=source.active_use,
        dependencies=tuple(sorted(set(source.dependencies))),
        evidence=(("project_id", source.project_id), ("labels_match", str(source.labels_match))),
    )


def removal_fingerprint(
    project_id: str,
    registration_state: str,
    repository_root: Path,
    decisions: Sequence[InternalRemovalDecision],
) -> str:
    """Hash all identity, ownership, path and dependency facts, not secrets."""
    payload = {
        "project_id": project_id,
        "registration_state": registration_state,
        "repository_root": str(_canonical_path(repository_root)),
        "decisions": [decision.canonical() for decision in decisions],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_project_removal_projection(  # noqa: C901
    *,
    registration: ProjectRegistration | Mapping[str, RegistrationValue],
    inventory: ResourceInventory,
    runtimes: Iterable[RemovalRuntimeSource] = (),
    publications: Iterable[RemovalPublicationSource] = (),
    paths: Iterable[RemovalPathSource] = (),
    compose: Iterable[RemovalComposeSource] = (),
    complete: bool = True,
) -> ProjectRemovalProjection:
    """Build a deterministic project-scoped removal projection.

    Inputs are observations captured by callers.  The function only classifies
    them; it never repairs catalogue rows, adopts paths, or follows a symlink.
    """
    project = _registration(registration)
    owner_projects = _owner_projects(inventory)
    scoped = _scope_ids(inventory, project.project_id, owner_projects)
    decisions: dict[str, InternalRemovalDecision] = {}

    for item in inventory.resources:
        decision = _inventory_decision(
            item,
            scoped=scoped,
            project_id=project.project_id,
            owner_projects=owner_projects.get(item.stable_identity, frozenset()),
        )
        if decision is not None:
            decisions[decision.identity] = decision
    decisions[f"project:{project.project_id}"] = _project_source_decision(
        project, decisions.get(f"project:{project.project_id}")
    )
    for runtime in runtimes:
        if runtime.project_id == project.project_id:
            decisions[runtime.identity] = _runtime_decision(runtime)
    for publication in publications:
        if publication.project_id == project.project_id:
            decisions[publication.identity] = _publication_decision(publication)
    for path_source in paths:
        if path_source.project_id == project.project_id:
            decisions[path_source.identity] = _path_decision(path_source)
    for compose_source in compose:
        if compose_source.project_id == project.project_id:
            decisions[compose_source.identity] = _compose_decision(compose_source)

    def phase(decision: InternalRemovalDecision) -> int:
        """D7 phase rank; identity remains the stable tie breaker."""
        if decision.action == "stop":
            return 10
        if decision.action == "unpublish":
            return 20
        if decision.action == "forget":
            return 70
        ranks = {
            "environment": 30,
            "worktree": 30,
            "python_environment": 30,
            "database": 40,
            "filestore": 40,
            "backup": 50,
            "compose": 60,
            "volume": 60,
            "source": 65,
        }
        return ranks.get(decision.resource_type, 55)

    ordered = tuple(
        decisions[key]
        for key in sorted(
            decisions,
            key=lambda identity: (
                phase(decisions[identity]),
                identity,
            ),
        )
    )
    return ProjectRemovalProjection(
        project_id=project.project_id,
        registration_state=project.registration_state,
        repository_root=_canonical_path(project.repository_root),
        expected_confirmation=expected_confirmation_token(
            project.repository_root, project.project_id
        ),
        decisions=ordered,
        fingerprint=removal_fingerprint(
            project.project_id,
            project.registration_state,
            project.repository_root,
            ordered,
        ),
        complete=complete,
    )


def project_decision_identity(kind: str, value: str) -> str:
    """Expose the same stable identity rule to purge adapters."""
    return stable_resource_identity(kind, value)


__all__ = [
    "InternalRemovalDecision",
    "ProjectRegistration",
    "ProjectRemovalProjection",
    "RemovalComposeSource",
    "RemovalPathSource",
    "RemovalPublicationSource",
    "RemovalRuntimeSource",
    "build_project_removal_projection",
    "canonical_containment",
    "expected_confirmation_token",
    "project_decision_identity",
    "removal_fingerprint",
    "resolve_project_selector",
]
