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
    ProjectRemovalProjection,
)
from odoo_instance_sdk.internal.project_removal_coordinator import (
    ProjectRemovalAdapter,
    capture_project_removal_command,
    expected_project_confirmation_for_command,
    set_project_confirmation,
)
from odoo_instance_sdk.internal.project_removal_terminal import (
    _forgotten_project_is_inert,
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


@pytest.mark.parametrize("failure_mode", ["captured-process", "in-process-action"])
def test_partial_purge_failure_is_publicly_truthful_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    """Inject faults only through existing process and guarded-action seams."""
    catalog, project_id, root = _catalog(tmp_path)
    completed_identity = "database:completed"
    failed_identity = "database:failed"
    adapters: Mapping[str, ProjectRemovalAdapter]

    if failure_mode == "captured-process":
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
        resources = (
            (completed_identity, "database"),
            (failed_identity, "database"),
        )
    else:
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
        adapters = {
            completed_identity: filesystem_adapter,
            failed_identity: filesystem_adapter,
        }
        resources = ((completed_identity, "config"), (failed_identity, "config"))

    projection = _projection(project_id, root, resources)
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
    assert details["completed_total"] == 1
    assert details["remaining_total"] == 2
    assert details["failed_identity"] == failed_identity
    assert details["completed"][0]["identity"] == completed_identity  # type: ignore[index]
    assert details["remaining"][0]["identity"] == failed_identity  # type: ignore[index]
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    if failure_mode == "captured-process":
        assert [step.step_id for step in executor.executed] == [
            f"project-removal.database.guarded-drop:{completed_identity}",
            f"project-removal.database.guarded-drop:{failed_identity}",
        ]
    else:
        assert completed_resource is not None
        assert not completed_resource.exists()
        assert failed_resource.exists()

    result = command.run()
    assert result.registration_state == "forgotten"
    assert tuple(item.identity for item in result.completed[:2]) == (
        completed_identity,
        failed_identity,
    )
    assert result.completed[-1].action == "forget"
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    if failure_mode == "in-process-action":
        assert not failed_resource.exists()
    catalog.close()
