from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from odoo_instance_sdk.exceptions import ProjectRemovalError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    RunContext,
)
from odoo_instance_sdk.internal.project_data_purge import DatabasePurgeAdapter, DatabasePurgeTarget
from odoo_instance_sdk.internal.project_filesystem_purge import (
    FilesystemPurgeAdapter,
    FilesystemPurgeTarget,
)
from odoo_instance_sdk.internal.project_removal import (
    InternalRemovalDecision,
    ProjectRegistration,
    ProjectRemovalProjection,
)
from odoo_instance_sdk.internal.project_removal_coordinator import (
    ProjectRemovalAdapter,
    _projection as coordinator_projection,
    capture_project_removal_command,
    expected_project_confirmation_for_command,
    set_project_confirmation,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.resource_inventory import ActiveUse, OwnershipConfidence
from odoo_instance_sdk.models.project_removal import ProjectResourceDecision, ProjectSelector
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient

pytestmark = pytest.mark.integration


def _catalog(tmp_path: Path) -> tuple[BackupCatalog, str, Path]:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    catalog._register_project(project_id, root, root / ".git")
    return catalog, project_id, root


def _projection(
    project_id: str,
    root: Path,
    resources: tuple[tuple[str, str], ...],
) -> ProjectRemovalProjection:
    decisions = tuple(
        InternalRemovalDecision(
            identity=identity,
            resource_type=resource_type,
            action="delete",
            reason="exclusive_sdk_ownership",
            target="<local>/resource",
            ownership=OwnershipConfidence.PROVEN,
            active_use=ActiveUse.INACTIVE,
        )
        for identity, resource_type in resources
    )
    return ProjectRemovalProjection(
        project_id=project_id,
        registration_state="registered",
        repository_root=root,
        expected_confirmation=f"{root.name}:{project_id}",
        decisions=decisions,
        fingerprint="project-removal-matrix",
    )


class _AdapterRegistry:
    def __init__(self, adapters: Mapping[str, ProjectRemovalAdapter]) -> None:
        self.adapters = dict(adapters)

    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None:
        return self.adapters.get(decision.identity)


def _process_child(
    executor: RecordingExecutor,
    identity: str,
) -> Command[object]:
    step = PreparedStep(
        step_id=f"project-removal.database.guarded-drop:{identity}",
        argv=("psql", "--guarded-project-drop"),
        mutating=True,
    )

    def run(context: RunContext[object]) -> object:
        context.process(step.step_id)
        return object()

    return Command.create(
        ExecutionPlan(steps=(step.public_projection(),)),
        run,
        (step,),
        executor=executor,
    )


def _assert_partial_purge_failure_details(
    catalog: BackupCatalog,
    project_id: str,
    completed_identity: str,
    failed_identity: str,
    error: pytest.ExceptionInfo[ProjectRemovalError],
) -> dict[str, object]:
    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["registration_state"] == "registered"
    assert details["completed_total"] == 1
    assert details["remaining_total"] == 2
    assert details["failed_identity"] == failed_identity
    assert details["completed"][0]["identity"] == completed_identity  # type: ignore[index]
    assert details["remaining"][0]["identity"] == failed_identity  # type: ignore[index]
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    return details


def test_partial_purge_failure_captured_process_is_publicly_truthful_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A captured process can fail once and retry from the public ledger."""
    catalog, project_id, root = _catalog(tmp_path)
    completed_identity = "database:completed"
    failed_identity = "database:failed"
    executor = RecordingExecutor()
    failed = True

    def result_factory(step: object) -> ProcessResult:
        nonlocal failed
        prepared = cast("PreparedStep", step)
        if failed and prepared.step_id.endswith(failed_identity):
            failed = False
            raise RuntimeError("guarded process failed after capture")
        return ProcessResult(
            argv=prepared.argv,
            returncode=0,
            stdout="",
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    executor.result_factory = result_factory
    database_targets = {
        identity: DatabasePurgeTarget(
            identity=identity,
            project_id=project_id,
            project_root=root,
            database_name=identity.rsplit(":", 1)[-1],
        )
        for identity in (completed_identity, failed_identity)
    }
    adapters = {
        completed_identity: DatabasePurgeAdapter(
            cast("OdooClient", object()),
            database_targets,
            command_factory=lambda target: _process_child(executor, target.identity),
        ),
        failed_identity: DatabasePurgeAdapter(
            cast("OdooClient", object()),
            database_targets,
            command_factory=lambda target: _process_child(executor, target.identity),
        ),
    }
    projection = _projection(
        project_id,
        root,
        ((completed_identity, "database"), (failed_identity, "database")),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_coordinator._projection",
        lambda _catalog, _registration: projection,
    )
    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_project_id(project_id),
        purge=True,
        adapters=_AdapterRegistry(adapters),
    )
    set_project_confirmation(command, expected_project_confirmation_for_command(command))

    with pytest.raises(ProjectRemovalError, match="project removal step failed") as error:
        command.run()
    _assert_partial_purge_failure_details(
        catalog, project_id, completed_identity, failed_identity, error
    )
    assert [step.step_id for step in executor.executed] == [
        f"project-removal.database.guarded-drop:{completed_identity}",
        f"project-removal.database.guarded-drop:{failed_identity}",
    ]

    result = command.run()
    assert result.registration_state == "forgotten"
    assert tuple(item.identity for item in result.completed[:2]) == (
        completed_identity,
        failed_identity,
    )
    assert result.completed[-1].action == "forget"
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


def test_partial_purge_failure_in_process_action_is_publicly_truthful_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guarded in-process action can fail once and retry from the ledger."""
    catalog, project_id, root = _catalog(tmp_path)
    completed_identity = "filesystem:completed"
    failed_identity = "filesystem:failed"
    completed_resource = root / ".odcli" / "project.toml"
    completed_resource.parent.mkdir(parents=True)
    completed_resource.write_text("owned config\n", encoding="utf-8")
    failed_resource = root / ".odcli" / "logs"
    failed_resource.mkdir()
    filesystem_targets = {
        completed_identity: FilesystemPurgeTarget(
            identity=completed_identity,
            project_id=project_id,
            path=completed_resource,
            allowed_root=root,
        ),
        failed_identity: FilesystemPurgeTarget(
            identity=failed_identity,
            project_id=project_id,
            path=failed_resource,
            allowed_root=root,
        ),
    }
    filesystem_adapter = FilesystemPurgeAdapter(tuple(filesystem_targets.values()))
    projection = _projection(
        project_id,
        root,
        ((completed_identity, "config"), (failed_identity, "config")),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_coordinator._projection",
        lambda _catalog, _registration: projection,
    )
    adapters = {
        completed_identity: filesystem_adapter,
        failed_identity: filesystem_adapter,
    }
    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_project_id(project_id),
        purge=True,
        adapters=_AdapterRegistry(adapters),
    )
    set_project_confirmation(command, expected_project_confirmation_for_command(command))

    def fail_guard(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("guarded in-process action failed")

    with monkeypatch.context() as guarded_patch:
        guarded_patch.setattr(
            "odoo_instance_sdk.internal.project_filesystem_purge._assert_safe_directory",
            fail_guard,
        )
        with pytest.raises(ProjectRemovalError, match="project removal step failed") as error:
            command.run()
    _assert_partial_purge_failure_details(
        catalog, project_id, completed_identity, failed_identity, error
    )
    assert not completed_resource.exists()
    assert failed_resource.exists()

    result = command.run()
    assert result.registration_state == "forgotten"
    assert tuple(item.identity for item in result.completed[:2]) == (
        completed_identity,
        failed_identity,
    )
    assert result.completed[-1].action == "forget"
    assert not failed_resource.exists()
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


def test_projection_result_is_bounded_without_changing_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    projection = _projection(
        "project-full-id",
        root,
        (("database:one", "database"), ("backup:one", "backup")),
    )
    retained = InternalRemovalDecision(
        identity="volume:retained",
        resource_type="volume",
        action="retain",
        reason="observation_incomplete",
        target="<local>/volume",
    )
    projection = ProjectRemovalProjection(
        project_id=projection.project_id,
        registration_state=projection.registration_state,
        repository_root=projection.repository_root,
        expected_confirmation=projection.expected_confirmation,
        decisions=(*projection.decisions, retained),
        fingerprint=projection.fingerprint,
    )

    assert len(projection.public_decisions) == 3
    assert tuple(item.identity for item in projection.retained) == ("volume:retained",)
    assert len(projection.bounded_decisions(2)) == 2
    result = projection.result(registration_state="registered", limit=2)
    assert result.registration_state == "registered"
    assert tuple(item.identity for item in result.remaining) == ("database:one", "backup:one")
    assert result.retained == ()
    with pytest.raises(ValueError, match="decision limit"):
        projection.bounded_decisions(0)


def test_projection_fails_closed_for_unobserved_database_and_compose_volume(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest_dir = root / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        '[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8"
    )
    claim = catalog._ensure_postgres_cluster_pending(project_id, "compose-project", "volume")
    catalog._activate_postgres_cluster(claim.cluster_id, project_id, "compose-project", "volume")

    projection = coordinator_projection(
        catalog,
        ProjectRegistration(project_id, root, root / ".git"),
    )
    database = next(item for item in projection.decisions if item.resource_type == "database")
    volume = next(item for item in projection.decisions if item.resource_type == "volume")
    assert database.action == "retain"
    assert database.reason == "observation_incomplete"
    assert volume.action == "retain"
    assert volume.reason == "active_use_unproven"
    assert any(
        relationship.startswith("database:")
        for relationship in next(
            item for item in projection.decisions if item.identity == f"project:{project_id}"
        ).relationships
    )
    catalog.close()


def test_projection_retains_dirty_primary_source_at_capture(tmp_path: Path) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    projection = coordinator_projection(
        catalog,
        ProjectRegistration(project_id, root, root / ".git"),
    )
    source = next(item for item in projection.decisions if item.resource_type == "source")
    assert source.action == "retain"
    assert source.reason == "source_git_provenance_unmatched"
    catalog.close()
