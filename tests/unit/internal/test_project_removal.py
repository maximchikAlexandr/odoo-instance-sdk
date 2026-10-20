from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.internal.project_removal import (
    ProjectRegistration,
    RemovalComposeSource,
    RemovalPathSource,
    RemovalPublicationSource,
    RemovalRuntimeSource,
    build_project_removal_projection,
    canonical_containment,
    expected_confirmation_token,
    resolve_project_selector,
)
from odoo_instance_sdk.internal.resource_inventory import (
    ActiveUse,
    OwnershipConfidence,
    ProjectResourceSource,
    build_resource_inventory,
)
from odoo_instance_sdk.models.project_removal import ProjectSelector


def test_selector_requires_exact_full_id_or_canonical_path(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    registration = ProjectRegistration("project-full-id", root)

    assert (
        resolve_project_selector(
            ProjectSelector.from_project_id("project-full-id"), (registration,)
        )
        == registration
    )
    assert (
        resolve_project_selector(ProjectSelector.from_path(str(root)), (registration,))
        == registration
    )
    with pytest.raises(ValueError, match="unknown"):
        resolve_project_selector(ProjectSelector.from_project_id("project-full"), (registration,))


def test_missing_source_is_recoverable_only_by_full_id(tmp_path: Path) -> None:
    missing = tmp_path / "removed-project"
    registration = ProjectRegistration("project-full-id", missing)
    assert (
        resolve_project_selector(ProjectSelector.from_path(str(missing)), (registration,))
        == registration
    )
    assert (
        resolve_project_selector(
            ProjectSelector.from_project_id("project-full-id"), (registration,)
        )
        == registration
    )


def test_projection_is_ordered_deduplicated_and_fingerprinted(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    inventory = build_resource_inventory(
        projects=(ProjectResourceSource("project-full-id", root),),
    )
    projection = build_project_removal_projection(
        registration=ProjectRegistration("project-full-id", root),
        inventory=inventory,
        runtimes=(
            RemovalRuntimeSource(
                project_id="project-full-id",
                identity="runtime:one",
                active_use=ActiveUse.ACTIVE,
                ownership=OwnershipConfidence.PROVEN,
            ),
        ),
        publications=(
            RemovalPublicationSource(
                project_id="project-full-id",
                identity="route:one",
                active_use=ActiveUse.ACTIVE,
                ownership=OwnershipConfidence.PROVEN,
            ),
        ),
        compose=(
            RemovalComposeSource(
                project_id="project-full-id",
                identity="compose:one",
                ownership=OwnershipConfidence.PROVEN,
                active_use=ActiveUse.INACTIVE,
                labels_match=True,
            ),
        ),
    )
    assert projection.expected_confirmation == "project:project-full-id"
    assert [item.action for item in projection.decisions[:3]] == ["stop", "unpublish", "delete"]
    assert len({item.identity for item in projection.decisions}) == len(projection.decisions)
    repeat = build_project_removal_projection(
        registration=ProjectRegistration("project-full-id", root),
        inventory=inventory,
        runtimes=(
            RemovalRuntimeSource(
                project_id="project-full-id",
                identity="runtime:one",
                active_use=ActiveUse.ACTIVE,
                ownership=OwnershipConfidence.PROVEN,
            ),
        ),
        publications=(
            RemovalPublicationSource(
                project_id="project-full-id",
                identity="route:one",
                active_use=ActiveUse.ACTIVE,
                ownership=OwnershipConfidence.PROVEN,
            ),
        ),
        compose=(
            RemovalComposeSource(
                project_id="project-full-id",
                identity="compose:one",
                ownership=OwnershipConfidence.PROVEN,
                active_use=ActiveUse.INACTIVE,
                labels_match=True,
            ),
        ),
    )
    assert projection.fingerprint != ""
    assert projection.fingerprint == repeat.fingerprint
    assert len(projection.bounded_decisions(2)) == 2


def test_shared_and_unproven_compose_resources_are_retained(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    inventory = build_resource_inventory(
        projects=(ProjectResourceSource("project-full-id", root),),
    )
    projection = build_project_removal_projection(
        registration=ProjectRegistration("project-full-id", root),
        inventory=inventory,
        compose=(
            RemovalComposeSource(
                project_id="project-full-id",
                identity="compose:shared",
                ownership=OwnershipConfidence.PROVEN,
                active_use=ActiveUse.INACTIVE,
                labels_match=True,
                dependencies=("database:shared",),
            ),
        ),
        paths=(
            RemovalPathSource(
                project_id="project-full-id",
                identity="file:unknown",
                path=root / "unknown.txt",
                target="<local>/unknown.txt",
                allowed_root=root,
                ownership=OwnershipConfidence.UNKNOWN,
                unknown=True,
            ),
        ),
    )
    by_id = {item.identity: item for item in projection.decisions}
    assert by_id["compose:shared"].action == "retain"
    assert by_id["compose:shared"].reason == "compose_identity_or_dependency_unproven"
    assert by_id["file:unknown"].reason == "unknown_file"


def test_caller_owned_worktree_and_linked_source_are_retained(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    inventory = build_resource_inventory(
        environments=(),
        projects=(ProjectResourceSource("project-full-id", root),),
    )
    projection = build_project_removal_projection(
        registration=ProjectRegistration("project-full-id", root, caller_owned_checkout=True),
        inventory=inventory,
        paths=(
            RemovalPathSource(
                project_id="project-full-id",
                identity="worktree:caller",
                path=root / "checkout",
                target="<local>/checkout",
                allowed_root=root,
                ownership=OwnershipConfidence.EXTERNAL,
            ),
        ),
    )
    by_id = {item.identity: item for item in projection.decisions}
    assert by_id["project:project-full-id"].reason == "source_git_provenance_unmatched"
    assert by_id["worktree:caller"].reason == "external_ownership"


def test_primary_source_requires_matching_git_common_dir(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".git").mkdir()
    inventory = build_resource_inventory(
        projects=(ProjectResourceSource("project-full-id", root),),
    )
    registration = ProjectRegistration(
        "project-full-id",
        root,
        git_common_dir=root / ".git",
    )
    projection = build_project_removal_projection(
        registration=registration,
        inventory=inventory,
    )
    source = next(
        item for item in projection.decisions if item.identity == "project:project-full-id"
    )
    assert source.action == "delete"
    assert source.reason == "registered_primary_source"


def test_symlink_escape_is_never_contained_or_deletable(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    assert canonical_containment(link, root) is False

    inventory = build_resource_inventory(
        projects=(ProjectResourceSource("project-full-id", root),),
    )
    projection = build_project_removal_projection(
        registration=ProjectRegistration("project-full-id", root),
        inventory=inventory,
        paths=(
            RemovalPathSource(
                project_id="project-full-id",
                identity="file:escape",
                path=link,
                target="<local>/escape",
                allowed_root=root,
                ownership=OwnershipConfidence.PROVEN,
            ),
        ),
    )
    decision = next(item for item in projection.decisions if item.identity == "file:escape")
    assert decision.action == "retain"
    assert decision.reason == "symlink_or_path_escape"


def test_confirmation_and_targets_are_secret_free(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    assert expected_confirmation_token(root, "project-full-id") == "project:project-full-id"
    inventory = build_resource_inventory(
        projects=(ProjectResourceSource("project-full-id", root),),
    )
    projection = build_project_removal_projection(
        registration=ProjectRegistration("project-full-id", root),
        inventory=inventory,
        paths=(
            RemovalPathSource(
                project_id="project-full-id",
                identity="file:secret",
                path=root / "secrets",
                target="/private/project?password=super-secret",
                allowed_root=root,
                ownership=OwnershipConfidence.PROVEN,
            ),
        ),
    )
    decision = next(item for item in projection.public_decisions if item.identity == "file:secret")
    assert "super-secret" not in decision.target
    assert "/private" not in decision.target
    assert "password" not in decision.target
    assert all(not Path(item.target).is_absolute() for item in projection.public_decisions)
