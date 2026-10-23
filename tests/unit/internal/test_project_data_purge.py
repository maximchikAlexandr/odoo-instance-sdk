from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    ProcessResult,
    RecordingExecutor,
    RunContext,
)
from odoo_instance_sdk.internal.project_data_purge import (
    BackupPurgeAdapter,
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
from tests.unit.monitor_support import make_env

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


def _decision(identity: str, resource_type: str = "database") -> ProjectResourceDecision:
    return ProjectResourceDecision(
        identity=identity,
        resource_type=resource_type,
        action="delete",
        reason="exclusive_sdk_ownership",
        target="<local>/resource",
    )


def _compose_fixture(
    tmp_path: Path, *, dependency_check: Callable[[], bool] | None = None
) -> tuple[BackupCatalog, ComposeClusterPurgeTarget, str]:
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
        dependency_check=dependency_check,
    )
    return catalog, target, project_id


def test_database_adapter_flattens_guarded_child_without_rebuilding_it(tmp_path: Path) -> None:
    action = PreparedAction(
        step_id="database.child",
        action="drop-project-database",
        description="drop database",
        mutating=True,
    )
    calls: list[str] = []

    def factory(_target: DatabasePurgeTarget) -> Command[JsonValue]:
        def run(context: RunContext[JsonValue]) -> JsonValue:
            calls.append(action.step_id)
            context.action(action.step_id)
            context.complete_action(action.step_id)
            return None

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
        client=cast("OdooClient", object()),
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
        {
            identity: FilestorePurgeTarget(
                identity,
                "project-a",
                target_path,
                root,
                database_provenance_check=lambda: True,
            )
        }
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
        {
            "filestore:escaped": FilestorePurgeTarget(
                "filestore:escaped",
                "project-a",
                escaped,
                root,
                database_provenance_check=lambda: True,
            )
        }
    )
    with pytest.raises(ConfigError, match="containment"):
        unsafe.capture(_decision("filestore:escaped", "filestore"), project_id="project-a").run()  # type: ignore[union-attr]


def test_filestore_adapter_requires_live_database_provenance_and_allows_absent_retry(
    tmp_path: Path,
) -> None:
    root = tmp_path / "filestore"
    target_path = root / "owned_db"
    target_path.mkdir(parents=True)
    provenance = False
    identity = "filestore:owned"
    target = FilestorePurgeTarget(
        identity,
        "project-a",
        target_path,
        root,
        database_provenance_check=lambda: provenance,
    )
    adapter = FilestorePurgeAdapter({identity: target})
    command = adapter.capture(_decision(identity, "filestore"), project_id="project-a")
    assert command is not None
    with pytest.raises(ConfigError, match="provenance"):
        command.run()
    assert target_path.exists()

    provenance = True
    target_path.rmdir()
    assert command.run() == _decision(identity, "filestore")

    assert (
        FilestorePurgeAdapter(
            {identity: FilestorePurgeTarget(identity, "project-a", target_path, root)}
        ).capture(_decision(identity, "filestore"), project_id="project-a")
        is None
    )


def test_compose_adapter_requires_claim_and_exact_labels_then_removes_only_pair(
    tmp_path: Path,
) -> None:
    catalog, target, project_id = _compose_fixture(tmp_path, dependency_check=lambda: False)
    active = catalog._get_postgres_cluster(project_id)
    assert active is not None

    def result_factory(step):
        if step.step_id.endswith("inspect-volume"):
            stdout = json.dumps(
                {
                    "Name": "volume-a",
                    "Labels": {
                        "com.docker.compose.project": "compose-a",
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
                            "com.docker.compose.project": "compose-a",
                            "io.odoo-instance-sdk.cluster-id": str(active.cluster_id),
                            "io.odoo-instance-sdk.project-id": project_id,
                        }
                    },
                    "Mounts": [{"Type": "volume", "Name": "volume-a"}],
                }
            )
            return ProcessResult(step.argv, 0, stdout, "", 0.0, step.cwd, step.environment)
        if step.step_id.endswith(("verify-volume", "verify-container")):
            return ProcessResult(
                step.argv, 1, "", "Error: No such object", 0.0, step.cwd, step.environment
            )
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
    catalog, target, project_id = _compose_fixture(tmp_path)
    target = replace(target, dependencies=("database:retained",))
    adapter = ComposeClusterPurgeAdapter(catalog, {target.identity: target})
    assert adapter.capture(_decision(target.identity, "volume"), project_id=project_id) is None
    catalog.close()


def test_compose_adapter_rejects_inspect_transport_failure_without_mutation(
    tmp_path: Path,
) -> None:
    catalog, target, project_id = _compose_fixture(tmp_path, dependency_check=lambda: False)

    def result_factory(step):
        if step.step_id.endswith("inspect-volume"):
            return ProcessResult(
                step.argv,
                1,
                "",
                "permission denied while contacting Docker daemon",
                0.0,
                step.cwd,
                step.environment,
            )
        return ProcessResult(step.argv, 0, "", "", 0.0, step.cwd, step.environment)

    executor = RecordingExecutor(result_factory=result_factory)
    command = ComposeClusterPurgeAdapter(
        catalog, {target.identity: target}, executor=executor
    ).capture(_decision(target.identity, "volume"), project_id=project_id)
    assert command is not None
    with pytest.raises(ConfigError, match="absence is unproven"):
        command.run()
    assert not any(step.mutating for step in executor.executed)
    catalog.close()


def test_compose_adapter_rejects_postcondition_transport_failure(
    tmp_path: Path,
) -> None:
    catalog, target, project_id = _compose_fixture(tmp_path, dependency_check=lambda: False)

    def result_factory(step):
        if step.step_id.endswith("inspect-volume"):
            stdout = json.dumps(
                {
                    "Name": target.volume_name,
                    "Labels": {
                        "com.docker.compose.project": target.compose_project,
                        "io.odoo-instance-sdk.cluster-id": target.cluster_id,
                        "io.odoo-instance-sdk.project-id": project_id,
                    },
                }
            )
            return ProcessResult(step.argv, 0, stdout, "", 0.0, step.cwd, step.environment)
        if step.step_id.endswith("inspect-container"):
            stdout = json.dumps(
                {
                    "Name": f"/{target.container_name}",
                    "Config": {
                        "Labels": {
                            "com.docker.compose.project": target.compose_project,
                            "io.odoo-instance-sdk.cluster-id": target.cluster_id,
                            "io.odoo-instance-sdk.project-id": project_id,
                        }
                    },
                    "Mounts": [{"Type": "volume", "Name": target.volume_name}],
                }
            )
            return ProcessResult(step.argv, 0, stdout, "", 0.0, step.cwd, step.environment)
        if step.step_id.endswith("verify-volume"):
            return ProcessResult(
                step.argv,
                1,
                "",
                "permission denied while contacting Docker daemon",
                0.0,
                step.cwd,
                step.environment,
            )
        if step.step_id.endswith("verify-container"):
            return ProcessResult(
                step.argv, 1, "", "Error: No such object", 0.0, step.cwd, step.environment
            )
        return ProcessResult(step.argv, 0, "", "", 0.0, step.cwd, step.environment)

    executor = RecordingExecutor(result_factory=result_factory)
    command = ComposeClusterPurgeAdapter(
        catalog, {target.identity: target}, executor=executor
    ).capture(_decision(target.identity, "volume"), project_id=project_id)
    assert command is not None
    with pytest.raises(ConfigError, match="postcondition failed"):
        command.run()
    catalog.close()


def test_compose_adapter_rechecks_exclusive_use_at_execution(tmp_path: Path) -> None:
    states = iter((False, True))
    catalog, target, project_id = _compose_fixture(tmp_path, dependency_check=lambda: next(states))
    command = ComposeClusterPurgeAdapter(catalog, {target.identity: target}).capture(
        _decision(target.identity, "volume"), project_id=project_id
    )
    assert command is not None
    with pytest.raises(ConfigError, match="dependency"):
        command.run()
    catalog.close()


def test_compose_adapter_requires_live_exclusive_use_check(tmp_path: Path) -> None:
    catalog, target, project_id = _compose_fixture(tmp_path)
    assert (
        ComposeClusterPurgeAdapter(catalog, {target.identity: target}).capture(
            _decision(target.identity, "volume"), project_id=project_id
        )
        is None
    )
    catalog.close()


def test_compose_adapter_requires_compose_label_and_preserves_unrelated_identity(
    tmp_path: Path,
) -> None:
    catalog, target, project_id = _compose_fixture(tmp_path, dependency_check=lambda: False)

    def result_factory(step):
        if step.step_id.endswith("inspect-volume"):
            stdout = json.dumps(
                {
                    "Name": target.volume_name,
                    "Labels": {
                        "com.docker.compose.project": "another-project",
                        "io.odoo-instance-sdk.cluster-id": target.cluster_id,
                        "io.odoo-instance-sdk.project-id": project_id,
                    },
                }
            )
            return ProcessResult(step.argv, 0, stdout, "", 0.0, step.cwd, step.environment)
        if step.step_id.endswith("inspect-container"):
            stdout = json.dumps(
                {
                    "Name": f"/{target.container_name}",
                    "Config": {"Labels": {}},
                    "Mounts": [],
                }
            )
            return ProcessResult(step.argv, 0, stdout, "", 0.0, step.cwd, step.environment)
        return ProcessResult(step.argv, 0, "", "", 0.0, step.cwd, step.environment)

    executor = RecordingExecutor(result_factory=result_factory)
    command = ComposeClusterPurgeAdapter(
        catalog, {target.identity: target}, executor=executor
    ).capture(_decision(target.identity, "volume"), project_id=project_id)
    assert command is not None
    with pytest.raises(ConfigError, match="labels"):
        command.run()
    assert not any(step.mutating for step in executor.executed)
    catalog.close()


def test_backup_adapter_retains_borrowed_and_referenced_backups(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog._register_project(project_id, root, root / ".git")
    older = str(uuid.uuid4())
    newer = str(uuid.uuid4())
    first_time = datetime.now(UTC) - timedelta(days=2)
    second_time = datetime.now(UTC) - timedelta(days=1)
    for backup_id, timestamp in ((older, first_time), (newer, second_time)):
        path = tmp_path / f"{backup_id}.zip"
        catalog.start_download(
            backup_id,
            "https://example.test",
            "owned_db",
            "zip",
            True,
            path,
            project_id=project_id,
        )
        catalog.success_download(backup_id, path.name, 1, "", downloaded_at=timestamp)

    action = PreparedAction(
        step_id="backup.delete",
        action="delete-backup",
        description="delete backup",
        mutating=True,
    )

    def factory(_backup):
        def run(context):
            context.action(action.step_id)
            context.complete_action(action.step_id)
            return object()

        return Command.create(ExecutionPlan(steps=(action.public_projection(),)), run, (action,))

    adapter = BackupPurgeAdapter(cast("OdooClient", object()), catalog, command_factory=factory)
    assert (
        adapter.capture(_decision(f"backup:{older}", "backup"), project_id=project_id) is not None
    )

    catalog.create_environment(make_env("borrowed", backup_id=older, state="ready"))
    assert adapter.capture(_decision(f"backup:{older}", "backup"), project_id=project_id) is None
    assert (
        adapter.capture(_decision(f"backup:{older}", "backup"), project_id="project-foreign")
        is None
    )
    catalog.close()


def test_data_adapter_dispatches_only_owned_families() -> None:
    adapters = ProjectDataPurgeAdapters()
    assert adapters.for_decision(_decision("database:a")) is None
    assert adapters.for_decision(_decision("volume:a", "volume")) is None
