from __future__ import annotations

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
    ProjectRemovalProjection,
)
from odoo_instance_sdk.internal.project_removal_coordinator import (
    ProjectRemovalAdapter,
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
    identity: str,
    resource_type: str,
) -> ProjectRemovalProjection:
    decision = InternalRemovalDecision(
        identity=identity,
        resource_type=resource_type,
        action="delete",
        reason="exclusive_sdk_ownership",
        target="<local>/resource",
        ownership=OwnershipConfidence.PROVEN,
        active_use=ActiveUse.INACTIVE,
    )
    return ProjectRemovalProjection(
        project_id=project_id,
        registration_state="registered",
        repository_root=root,
        expected_confirmation=f"{root.name}:{project_id}",
        decisions=(decision,),
        fingerprint=f"project-removal-{resource_type}",
    )


class _SingleAdapterRegistry:
    def __init__(self, identity: str, adapter: ProjectRemovalAdapter) -> None:
        self.identity = identity
        self.adapter = adapter

    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None:
        return self.adapter if decision.identity == self.identity else None


def _process_child(
    executor: RecordingExecutor,
) -> Command[object]:
    step = PreparedStep(
        step_id="project-removal.database.guarded-drop",
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


@pytest.mark.parametrize("failure_mode", ["captured-process", "in-process-action"])
def test_partial_purge_failure_is_publicly_truthful_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    """Inject faults only through existing process and guarded-action seams."""
    catalog, project_id, root = _catalog(tmp_path)

    if failure_mode == "captured-process":
        identity = "database:owned"
        executor = RecordingExecutor()
        failed = True

        def result_factory(step: object) -> ProcessResult:
            nonlocal failed
            prepared = cast("PreparedStep", step)
            if failed:
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
        target = DatabasePurgeTarget(
            identity=identity,
            project_id=project_id,
            project_root=root,
            database_name="owned_db",
        )
        adapter: ProjectRemovalAdapter = DatabasePurgeAdapter(
            cast("OdooClient", object()),
            {identity: target},
            command_factory=lambda _target: _process_child(executor),
        )
    else:
        identity = "filesystem:owned"
        resource = root / ".odcli" / "logs"
        resource.mkdir(parents=True)
        filesystem_target = FilesystemPurgeTarget(
            identity=identity,
            project_id=project_id,
            path=resource,
            allowed_root=root,
        )
        adapter = cast("ProjectRemovalAdapter", FilesystemPurgeAdapter((filesystem_target,)))

    projection = _projection(
        project_id, root, identity, "database" if failure_mode == "captured-process" else "config"
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_coordinator._projection",
        lambda _catalog, _registration: projection,
    )
    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_project_id(project_id),
        purge=True,
        adapters=_SingleAdapterRegistry(identity, adapter),
    )
    set_project_confirmation(command, expected_project_confirmation_for_command(command))

    if failure_mode == "in-process-action":

        def fail_guard(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("guarded in-process action failed")

        with monkeypatch.context() as guarded_patch:
            guarded_patch.setattr(
                "odoo_instance_sdk.internal.project_filesystem_purge._assert_safe_directory",
                fail_guard,
            )
            with pytest.raises(ProjectRemovalError, match="project removal step failed") as error:
                command.run()
    else:
        with pytest.raises(ProjectRemovalError, match="project removal step failed") as error:
            command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["registration_state"] == "registered"
    assert details["completed_total"] == 0
    assert details["remaining_total"] == 2
    assert details["failed_identity"] == identity
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    if failure_mode == "captured-process":
        assert len(executor.executed) == 1
    else:
        assert resource.exists()

    result = command.run()
    assert result.registration_state == "forgotten"
    assert result.completed[-1].action == "forget"
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    if failure_mode == "in-process-action":
        assert not resource.exists()
    catalog.close()
