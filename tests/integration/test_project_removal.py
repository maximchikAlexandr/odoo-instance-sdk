from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from odoo_instance_sdk.exceptions import ProjectRemovalError
from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    RunContext,
)
from odoo_instance_sdk.internal.project_data_purge import (
    ComposeClusterPurgeAdapter,
    DatabasePurgeAdapter,
    DatabasePurgeTarget,
)
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
from odoo_instance_sdk.internal.project_removal_terminal import (
    _forgotten_project_is_inert,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.resource_inventory import ActiveUse, OwnershipConfidence
from odoo_instance_sdk.models.database_inventory import (
    DatabaseInventoryItem,
    DatabaseInventoryResult,
)
from odoo_instance_sdk.models.project_removal import ProjectResourceDecision, ProjectSelector
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project_init import init_project
from odoo_instance_sdk.resources.project import ProjectResource
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


def _legacy_environment(catalog: BackupCatalog, environment_id: str, root: Path) -> None:
    catalog.create_environment(
        {
            "id": environment_id,
            "name": environment_id,
            "repository_root": str(root),
            "git_common_dir": str(root / ".git"),
            "branch": "main",
            "base_ref": "HEAD",
            "worktree_path": str(root / "worktree"),
            "generated_config_path": str(root / "odoo.conf"),
            "python_environment_path": str(root / ".venv"),
            "python_environment_owned": False,
            "dependency_lock_path": str(root / "lock"),
            "db_mode": "shared",
            "runtime_json": "{}",
            "state": "ready",
            "created_at": "2026-01-01T00:00:00",
        }
    )


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


def test_public_forget_refuses_matching_legacy_environment(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    _legacy_environment(catalog, "legacy-alpha", root)
    client = SimpleNamespace(get_catalog=lambda: catalog)

    with pytest.raises(ProjectRemovalError, match="environment:legacy-alpha"):
        ProjectResource(_client=cast("OdooClient", client)).remove(
            ProjectSelector.from_project_id(project_id)
        )

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert catalog.get_environment("legacy-alpha") is not None
    assert root.exists()
    catalog.close()


def test_public_init_then_immediate_forget_revalidates_same_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: catalog_path
    )
    root = tmp_path / "initialized-project"
    root.mkdir()

    init_project(
        root,
        ProjectConfig(repository_root=root, python="3.12", default_source_database="owned_db"),
        postgres_allocated=False,
        allow_partial=True,
    )
    catalog = BackupCatalog(db_path=catalog_path)
    row = catalog.list_projects()[0]
    project_id = str(row["project_id"])
    database_inventory = DatabaseInventoryResult(
        cluster="127.0.0.1:5432",
        databases=(
            DatabaseInventoryItem(
                cluster="127.0.0.1:5432",
                cluster_id=None,
                name="owned_db",
                logical_size_bytes=1,
                active_sessions=0,
                is_default=True,
            ),
        ),
        tracked=True,
    )
    instance = SimpleNamespace(
        databases=SimpleNamespace(list_inventory=lambda *_args, **_kwargs: database_inventory),
        _postgres_cluster=None,
    )
    client = SimpleNamespace(
        get_catalog=lambda: catalog,
        instance=SimpleNamespace(from_project=lambda _project: instance),
    )

    result = ProjectResource(_client=cast("OdooClient", client)).remove(
        ProjectSelector.from_path(str(root))
    )

    assert result.registration_state == "forgotten"
    assert root.exists()
    assert (root / ".odcli" / "project.toml").exists()
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]

    init_project(
        root,
        ProjectConfig(repository_root=root, python="3.12", default_source_database="owned_db"),
        postgres_allocated=False,
        allow_partial=True,
    )
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_forget_refuses_reactivation_after_capture(tmp_path: Path) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    assert catalog.forget_project(project_id)
    client = SimpleNamespace(get_catalog=lambda: catalog)
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id)
    )
    assert catalog.reactivate_project(project_id, root, root / ".git")

    with pytest.raises(ProjectRemovalError, match="registration state changed"):
        command.run()

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert root.exists()
    catalog.close()


def test_public_forget_refuses_nonblocking_resource_decision_drift(tmp_path: Path) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    client = SimpleNamespace(get_catalog=lambda: catalog)
    command = ProjectResource(_client=cast("OdooClient", client)).remove_command(
        ProjectSelector.from_project_id(project_id)
    )
    _legacy_environment(catalog, "removed-after-capture", root)
    catalog._conn.execute(
        "UPDATE environments SET state = 'removed' WHERE id = ?", ("removed-after-capture",)
    )
    catalog._conn.commit()

    with pytest.raises(ProjectRemovalError, match="plan is stale"):
        command.run()

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert catalog.get_environment("removed-after-capture") is not None
    catalog.close()


def test_public_forget_preserves_dirty_source_decision_across_execution(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    subprocess.run(("git", "init", str(root)), check=True, capture_output=True, text=True)
    odcli = root / ".odcli"
    odcli.mkdir()
    (odcli / "project.toml").write_text(
        '[project]\ndefault_source_database = "binta_db"\n', encoding="utf-8"
    )
    (odcli / "odoo.conf").write_text("[options]\n", encoding="utf-8")
    (odcli / ".env").write_text("BINTA_DIAGNOSTIC=redacted\n", encoding="utf-8")
    (odcli / "filestore").mkdir()
    untracked = root / "untracked-addon.py"
    untracked.write_text("# preserved\n", encoding="utf-8")
    client = SimpleNamespace(get_catalog=lambda: catalog)

    result = ProjectResource(_client=cast("OdooClient", client)).remove(
        ProjectSelector.from_project_id(project_id)
    )

    assert result.registration_state == "forgotten"
    assert untracked.exists()
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


class _AdapterRegistry:
    def __init__(self, adapters: Mapping[str, ProjectRemovalAdapter]) -> None:
        self.adapters = dict(adapters)

    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None:
        return self.adapters.get(decision.identity)


def _process_child(
    executor: RecordingExecutor,
    identity: str,
) -> Command[JsonValue]:
    step = PreparedStep(
        step_id=f"project-removal.database.guarded-drop:{identity}",
        argv=("psql", "--guarded-project-drop"),
        mutating=True,
    )

    def run(context: RunContext[JsonValue]) -> JsonValue:
        context.process(step.step_id)
        return None

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
        lambda _catalog, _registration, **_kwargs: projection,
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
        lambda _catalog, _registration, **_kwargs: projection,
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
    manifest = manifest_dir / "project.toml"
    manifest.write_text('[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8")
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
    assert volume.reason == "observation_incomplete"
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


def test_public_remove_command_executes_observed_database_and_compose_before_forget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    subprocess.run(("git", "init", str(root)), check=True, capture_output=True)
    manifest_dir = root / ".odcli"
    manifest_dir.mkdir()
    manifest = manifest_dir / "project.toml"
    manifest.write_text('[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8")
    (root / ".gitignore").write_text(".odcli/\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(root), "add", ".gitignore"), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "baseline",
        ),
        check=True,
        capture_output=True,
    )
    claim = catalog._ensure_postgres_cluster_pending(project_id, "compose-project", "volume")
    catalog._activate_postgres_cluster(claim.cluster_id, project_id, "compose-project", "volume")
    database_inventory = DatabaseInventoryResult(
        cluster="127.0.0.1:5432",
        databases=(
            DatabaseInventoryItem(
                cluster="127.0.0.1:5432",
                cluster_id=None,
                name="owned_db",
                logical_size_bytes=128,
                active_sessions=0,
                is_default=True,
            ),
        ),
        tracked=True,
    )
    cluster = SimpleNamespace(
        owned=True,
        compose_runner=SimpleNamespace(),
        resource_snapshot=lambda: SimpleNamespace(
            container=None,
            metrics=SimpleNamespace(volume_usage_bytes=1024),
            unavailability_reason="stopped",
        ),
    )
    instance = SimpleNamespace(
        databases=SimpleNamespace(list_inventory=lambda *_args, **_kwargs: database_inventory),
        _postgres_cluster=cluster,
    )
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(from_project=lambda _project: instance),
        ),
    )
    database_executor = RecordingExecutor()
    compose_executor = RecordingExecutor()
    real_database_capture = DatabasePurgeAdapter.capture
    real_compose_capture = ComposeClusterPurgeAdapter.capture

    def capture_database(
        _adapter: DatabasePurgeAdapter,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> Command[ProjectResourceDecision] | None:
        _adapter._command_factory = lambda target: _process_child(
            database_executor, target.identity
        )
        return real_database_capture(_adapter, decision, project_id=project_id)

    def compose_result_factory(step: PreparedProcess) -> ProcessResult:
        step = cast("PreparedStep", step)
        if step.step_id.endswith("inspect-volume"):
            stdout = json.dumps(
                {
                    "Name": "volume",
                    "Labels": {
                        "com.docker.compose.project": "compose-project",
                        "io.odoo-instance-sdk.cluster-id": str(claim.cluster_id),
                        "io.odoo-instance-sdk.project-id": project_id,
                    },
                }
            )
        elif (
            step.step_id.endswith("inspect-container")
            or step.step_id.endswith("verify-volume")
            or step.step_id.endswith("verify-container")
        ):
            stdout = ""
            return ProcessResult(
                argv=step.argv,
                returncode=1,
                stdout=stdout,
                stderr="No such object",
                duration=0.0,
                cwd=step.cwd,
                environment=step.environment,
            )
        else:
            stdout = ""
        return ProcessResult(
            argv=step.argv,
            returncode=0,
            stdout=stdout,
            stderr="",
            duration=0.0,
            cwd=step.cwd,
            environment=step.environment,
        )

    compose_executor.result_factory = compose_result_factory

    def capture_compose(
        _adapter: ComposeClusterPurgeAdapter,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> Command[ProjectResourceDecision] | None:
        _adapter._executor = compose_executor
        return real_compose_capture(_adapter, decision, project_id=project_id)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.inspect_volume_identity",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(DatabasePurgeAdapter, "capture", capture_database)
    monkeypatch.setattr(ComposeClusterPurgeAdapter, "capture", capture_compose)

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    result = command.run()

    assert [step.step_id for step in database_executor.executed] == [
        "project-removal.database.guarded-drop:database:owned_db"
    ]
    assert any(step.step_id.endswith("remove-volume") for step in compose_executor.executed)
    assert tuple(
        item.resource_type
        for item in result.completed
        if item.resource_type in {"database", "volume"}
    ) == ("database", "volume")
    assert result.completed[-1].action == "forget"
    assert not root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


def test_public_remove_command_blocks_forget_when_required_observation_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest_dir = root / ".odcli"
    manifest_dir.mkdir()
    manifest = manifest_dir / "project.toml"
    manifest.write_text('[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8")
    claim = catalog._ensure_postgres_cluster_pending(project_id, "compose-project", "volume")
    catalog._activate_postgres_cluster(claim.cluster_id, project_id, "compose-project", "volume")

    def unavailable_inventory(*_args: object, **_kwargs: object) -> DatabaseInventoryResult:
        raise OSError("database observation unavailable")

    cluster = SimpleNamespace(
        owned=True,
        compose_runner=SimpleNamespace(),
        resource_snapshot=lambda: (_ for _ in ()).throw(OSError("Compose observation unavailable")),
    )
    instance = SimpleNamespace(
        databases=SimpleNamespace(list_inventory=unavailable_inventory),
        _postgres_cluster=cluster,
    )
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(from_project=lambda _project: instance),
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.inspect_volume_identity",
        lambda *_args, **_kwargs: False,
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert manifest.exists()
    assert root.exists()
    catalog.close()


def test_public_remove_command_blocks_malformed_manifest_before_mutation(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text("[project\n", encoding="utf-8")
    before = manifest.read_bytes()
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(),
        ),
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert manifest.read_bytes() == before
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_remove_command_blocks_unreadable_manifest_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text("[project]\n", encoding="utf-8")
    before = manifest.read_bytes()

    from odoo_instance_sdk.project import ProjectConfig

    def unreadable(_path: Path) -> ProjectConfig:
        raise OSError("manifest permission denied")

    monkeypatch.setattr(ProjectConfig, "load", unreadable)
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(),
        ),
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert manifest.read_bytes() == before
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_remove_command_refuses_manifest_drift_before_mutation(
    tmp_path: Path,
) -> None:
    """A manifest becoming malformed after capture cannot reach a child action."""
    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text("[project]\n", encoding="utf-8")
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(from_project=lambda _project: None),
        ),
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    manifest.write_text("[project\n", encoding="utf-8")
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert manifest.read_text(encoding="utf-8") == "[project\n"
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_remove_command_refuses_manifest_drift_when_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest becoming unreadable after capture cannot reach a child action."""
    catalog, project_id, root = _catalog(tmp_path)
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text("[project]\n", encoding="utf-8")
    before = manifest.read_bytes()
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(from_project=lambda _project: None),
        ),
    )
    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )

    from odoo_instance_sdk.project import ProjectConfig

    def unreadable(_path: Path) -> ProjectConfig:
        raise OSError("manifest permission denied")

    monkeypatch.setattr(ProjectConfig, "load", unreadable)
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert manifest.read_bytes() == before
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_public_remove_command_refuses_registered_root_symlink(
    tmp_path: Path,
) -> None:
    """A dangling registered root is present lexically and cannot be forgotten."""
    catalog, project_id, root = _catalog(tmp_path)
    (root / ".git").rmdir()
    root.rmdir()
    missing_target = tmp_path / "missing-target"
    root.symlink_to(missing_target, target_is_directory=True)
    client = cast(
        "OdooClient",
        SimpleNamespace(
            get_catalog=lambda: catalog,
            instance=SimpleNamespace(),
        ),
    )

    command = ProjectResource(_client=client).remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    assert expected_project_confirmation_for_command(command) == f"{root.name}:{project_id}"
    with pytest.raises(ProjectRemovalError, match="observation") as error:
        command.run()

    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["failed_step"] == "project-removal:observations"
    assert root.is_symlink()
    assert not root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()
