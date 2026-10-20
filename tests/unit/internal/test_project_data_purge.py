from __future__ import annotations

import json
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import PreparedAction, ProcessResult, RecordingExecutor
from odoo_instance_sdk.internal.project_data_purge import (
    ComposeClusterPurgeAdapter,
    ComposeClusterPurgeTarget,
    DatabasePurgeAdapter,
    DatabasePurgeTarget,
    FilestorePurgeAdapter,
    FilestorePurgeTarget,
    ProjectDataPurgeAdapters,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models.project_removal import ProjectResourceDecision
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _decision(identity: str, resource_type: str = "database") -> ProjectResourceDecision:
    return ProjectResourceDecision(
        identity=identity,
        resource_type=resource_type,
        action="delete",
        reason="exclusive_sdk_ownership",
        target="<local>/resource",
    )


def test_database_adapter_flattens_guarded_child_without_rebuilding_it(tmp_path: Path) -> None:
    action = PreparedAction(
        step_id="database.child",
        action="drop-project-database",
        description="drop database",
        mutating=True,
    )
    calls: list[str] = []

    def factory(_target: DatabasePurgeTarget) -> Command[object]:
        def run(context):
            calls.append(action.step_id)
            context.action(action.step_id)
            context.complete_action(action.step_id)
            return object()

        return Command.create(
            ExecutionPlan(steps=(action.public_projection(),)),
            run,
            (action,),
        )

    identity = "database:known"
    target = DatabasePurgeTarget(
        identity=identity,
        project_id="project-a",
        project_root=tmp_path,
        database_name="owned_db",
    )
    adapter = DatabasePurgeAdapter(
        client=object(),
        targets={identity: target},
        command_factory=factory,
    )
    decision = _decision(identity)
    command = adapter.capture(decision, project_id="project-a")
    assert command is not None
    assert tuple(step.step_id for step in command.plan.steps) == (action.step_id,)
    assert command.run() == decision
    assert calls == [action.step_id]


def test_filestore_adapter_requires_containment_and_verifies_deletion(tmp_path: Path) -> None:
    root = tmp_path / "filestore"
    target_path = root / "owned_db"
    target_path.mkdir(parents=True)
    (target_path / "blob").write_text("data", encoding="utf-8")
    identity = "filestore:owned"
    adapter = FilestorePurgeAdapter(
        {identity: FilestorePurgeTarget(identity, "project-a", target_path, root)}
    )
    command = adapter.capture(_decision(identity, "filestore"), project_id="project-a")
    assert command is not None
    command.run()
    assert not target_path.exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = root / "escaped"
    escaped.symlink_to(outside, target_is_directory=True)
    unsafe = FilestorePurgeAdapter(
        {"filestore:escaped": FilestorePurgeTarget("filestore:escaped", "project-a", escaped, root)}
    )
    with pytest.raises(ConfigError, match="containment"):
        unsafe.capture(_decision("filestore:escaped", "filestore"), project_id="project-a").run()  # type: ignore[union-attr]


def test_compose_adapter_requires_claim_and_exact_labels_then_removes_only_pair(
    tmp_path: Path,
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog._register_project(project_id, root, root / ".git")
    claim = catalog._ensure_postgres_cluster_pending(project_id, "compose-a", "volume-a")
    active = catalog._activate_postgres_cluster(
        claim.cluster_id, project_id, "compose-a", "volume-a"
    )
    target = ComposeClusterPurgeTarget(
        identity="volume:owned",
        project_id=project_id,
        cluster_id=str(active.cluster_id),
        compose_project="compose-a",
        container_name="container-a",
        volume_name="volume-a",
    )

    def result_factory(step):
        if step.step_id.endswith("inspect-volume"):
            stdout = json.dumps(
                {
                    "Name": "volume-a",
                    "Labels": {
                        "io.odoo-instance-sdk.cluster-id": str(active.cluster_id),
                        "io.odoo-instance-sdk.project-id": project_id,
                    },
                }
            )
            return ProcessResult(step.argv, 0, stdout, "", 0.0, step.cwd, step.environment)
        if step.step_id.endswith("inspect-container"):
            stdout = json.dumps(
                {
                    "Name": "/container-a",
                    "Config": {
                        "Labels": {
                            "io.odoo-instance-sdk.cluster-id": str(active.cluster_id),
                            "io.odoo-instance-sdk.project-id": project_id,
                        }
                    },
                    "Mounts": [{"Type": "volume", "Name": "volume-a"}],
                }
            )
            return ProcessResult(step.argv, 0, stdout, "", 0.0, step.cwd, step.environment)
        if step.step_id.endswith(("verify-volume", "verify-container")):
            return ProcessResult(step.argv, 1, "", "missing", 0.0, step.cwd, step.environment)
        return ProcessResult(step.argv, 0, "", "", 0.0, step.cwd, step.environment)

    executor = RecordingExecutor(result_factory=result_factory)
    adapter = ComposeClusterPurgeAdapter(catalog, {target.identity: target}, executor=executor)
    decision = _decision(target.identity, "volume")
    command = adapter.capture(decision, project_id=project_id)
    assert command is not None
    assert command.run() == decision
    assert [step.argv[:3] for step in executor.executed if step.mutating] == [
        ("docker", "stop", "container-a"),
        ("docker", "rm", "--force"),
        ("docker", "volume", "rm"),
    ]
    catalog.close()


def test_compose_adapter_retains_on_dependency_or_label_drift(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog._register_project(project_id, root, root / ".git")
    claim = catalog._ensure_postgres_cluster_pending(project_id, "compose-a", "volume-a")
    catalog._activate_postgres_cluster(claim.cluster_id, project_id, "compose-a", "volume-a")
    target = ComposeClusterPurgeTarget(
        identity="volume:owned",
        project_id=project_id,
        cluster_id=str(claim.cluster_id),
        compose_project="compose-a",
        container_name="container-a",
        volume_name="volume-a",
        dependencies=("database:retained",),
    )
    adapter = ComposeClusterPurgeAdapter(catalog, {target.identity: target})
    assert adapter.capture(_decision(target.identity, "volume"), project_id=project_id) is None
    catalog.close()


def test_data_adapter_dispatches_only_owned_families() -> None:
    adapters = ProjectDataPurgeAdapters()
    assert adapters.for_decision(_decision("database:a")) is None
    assert adapters.for_decision(_decision("volume:a", "volume")) is None
