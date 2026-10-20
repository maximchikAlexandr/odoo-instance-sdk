from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import msgspec
import pytest

import odoo_instance_sdk
from odoo_instance_sdk import (
    OdooClient,
    OdooClientConfig,
    ProjectRemovalResult,
    ProjectResource,
    ProjectResourceDecision,
    ProjectSelector,
)
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.internal.project_removal_coordinator import (
    capture_project_removal_command,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.resource_inventory import stable_resource_identity
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.unit.monitor_support import make_env


def _decision() -> ProjectResourceDecision:
    return ProjectResourceDecision(
        identity="project:abc",
        resource_type="source",
        action="retain",
        reason="caller_owned",
        target="/projects/example",
    )


def test_public_project_values_are_frozen_and_json_safe() -> None:
    selector = ProjectSelector.from_path("/projects/example")
    result = ProjectRemovalResult(
        project_id="project_abc",
        mode="forget",
        registration_state="forgotten",
        retained=(_decision(),),
    )

    assert msgspec.to_builtins(selector) == {"path": "/projects/example", "project_id": None}
    decision = msgspec.to_builtins(_decision())
    assert set(decision) == {"identity", "resource_type", "action", "reason", "target"}
    assert decision == {
        "identity": "project:abc",
        "resource_type": "source",
        "action": "retain",
        "reason": "caller_owned",
        "target": "/projects/example",
    }
    assert msgspec.to_builtins(result) == {
        "project_id": "project_abc",
        "mode": "forget",
        "registration_state": "forgotten",
        "completed": (),
        "retained": (
            {
                "identity": "project:abc",
                "resource_type": "source",
                "action": "retain",
                "reason": "caller_owned",
                "target": "/projects/example",
            },
        ),
        "remaining": (),
    }

    with pytest.raises((AttributeError, TypeError)):
        selector.path = "/other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    ({}, {"path": "/projects/example", "project_id": "project_abc"}),
)
def test_project_selector_requires_exactly_one_form(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ProjectSelector(**kwargs)


def test_project_resource_is_lazy_exported_and_client_wiring_is_inert() -> None:
    assert odoo_instance_sdk._LAZY_EXPORTS["ProjectResource"] == (
        "odoo_instance_sdk.resources.project",
        "ProjectResource",
    )
    assert odoo_instance_sdk.ProjectResource is ProjectResource

    client = OdooClient(config=OdooClientConfig(executable="python3"))
    assert isinstance(client.projects, ProjectResource)
    assert client._catalog is None

    assert callable(client.projects.remove_command)


def test_remove_delegates_to_the_captured_command() -> None:
    result = ProjectRemovalResult(
        project_id="project_abc",
        mode="forget",
        registration_state="forgotten",
    )
    selector = ProjectSelector.from_project_id("project_abc")

    class CommandStub:
        def run(self) -> ProjectRemovalResult:
            return result

    class ResourceStub(ProjectResource):
        def remove_command(
            self,
            received_selector: ProjectSelector,
            *,
            purge: bool = False,
            confirmation: str | None = None,
        ) -> CommandStub:
            assert received_selector is selector
            assert purge is True
            assert confirmation == "project_abc"
            return CommandStub()

    resource = ResourceStub(_client=None)  # type: ignore[arg-type]
    assert resource.remove(selector, purge=True, confirmation="project_abc") is result


def test_public_project_resource_composes_backup_and_terminal_catalogue_actions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "filestore").mkdir()
    (root / ".odcli" / "filestore" / "blob").write_text("data", encoding="utf-8")
    (root / ".odcli" / "project.toml").write_text(
        '[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8"
    )
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    catalog._register_project(project_id, root, root / ".git")
    backup_id = str(uuid.uuid4())
    backup_path = tmp_path / "owned.zip"
    backup_path.write_bytes(b"backup")
    catalog.start_download(
        backup_id,
        "https://example.test",
        "owned_db",
        "zip",
        False,
        backup_path,
        project_id=project_id,
    )
    catalog.success_download(
        backup_id,
        backup_path.name,
        backup_path.stat().st_size,
        "",
        downloaded_at=datetime.now(UTC) - timedelta(days=1),
    )
    assert catalog.get_by_id(backup_id)["state"] == "available"  # type: ignore[index]
    newer_id = str(uuid.uuid4())
    newer_path = tmp_path / "newer.zip"
    newer_path.write_bytes(b"newer")
    catalog.start_download(
        newer_id,
        "https://example.test",
        "owned_db",
        "zip",
        False,
        newer_path,
        project_id=project_id,
    )
    catalog.success_download(
        newer_id,
        newer_path.name,
        newer_path.stat().st_size,
        "",
        downloaded_at=datetime.now(UTC),
    )
    assert catalog.deletion_protection_reason(backup_id) is None

    client = OdooClient(config=OdooClientConfig(executable="python3", backups_directory=tmp_path))
    client._catalog = catalog
    command = client.projects.remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )
    actions = tuple(step.action for step in command.plan.steps)

    assert "backup.delete" in actions, tuple(step.step_id for step in command.plan.steps)
    assert "delete-filestore" in actions
    assert actions[-1] == "forget"
    assert command.plan.steps[-1].step_id == f"project-removal:forget:project-forget:{project_id}"
    catalog.close()


def test_public_project_resource_orders_compose_after_scheduled_environment_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text(
        '[project]\ndefault_source_database = "owned_db"\n', encoding="utf-8"
    )
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    catalog._register_project(project_id, root, root / ".git")
    claim = catalog._ensure_postgres_cluster_pending(project_id, "owned-compose", "owned-volume")
    catalog._activate_postgres_cluster(
        claim.cluster_id,
        project_id,
        claim.compose_project,
        claim.volume_name,
    )
    catalog.create_environment(
        make_env(
            "retained-environment",
            project_id=project_id,
            repository_root=str(root),
            git_common_dir=str(root / ".git"),
        )
    )

    client = OdooClient(config=OdooClientConfig(executable="python3"))
    client._catalog = catalog
    compose_decision = ProjectResourceDecision(
        identity=stable_resource_identity("volume", f"{project_id}\x00{claim.volume_name}"),
        resource_type="compose",
        action="delete",
        reason="exclusive_sdk_ownership",
        target="compose cluster",
    )
    captured: dict[str, object] = {}
    real_capture = capture_project_removal_command

    def capture_with_compose_probe(*args: object, **kwargs: object) -> object:
        adapters = kwargs["adapters"]
        compose = adapters.for_decision(compose_decision)  # type: ignore[attr-defined]
        assert compose is not None

        def result_factory(step):
            if step.step_id.endswith("inspect-volume"):
                return ProcessResult(
                    step.argv,
                    0,
                    json.dumps(
                        {
                            "Name": "owned-volume",
                            "Labels": {
                                "com.docker.compose.project": "owned-compose",
                                "io.odoo-instance-sdk.cluster-id": str(claim.cluster_id),
                                "io.odoo-instance-sdk.project-id": project_id,
                            },
                        }
                    ),
                    "",
                    0.0,
                    step.cwd,
                    step.environment,
                )
            if step.step_id.endswith("inspect-container"):
                return ProcessResult(
                    step.argv,
                    0,
                    json.dumps(
                        {
                            "Name": "/owned-compose-postgres-1",
                            "Config": {
                                "Labels": {
                                    "com.docker.compose.project": "owned-compose",
                                    "io.odoo-instance-sdk.cluster-id": str(claim.cluster_id),
                                    "io.odoo-instance-sdk.project-id": project_id,
                                }
                            },
                            "Mounts": [{"Type": "volume", "Name": "owned-volume"}],
                        }
                    ),
                    "",
                    0.0,
                    step.cwd,
                    step.environment,
                )
            if step.step_id.endswith(("verify-volume", "verify-container")):
                return ProcessResult(
                    step.argv,
                    1,
                    "",
                    "Error: No such object",
                    0.0,
                    step.cwd,
                    step.environment,
                )
            return ProcessResult(step.argv, 0, "", "", 0.0, step.cwd, step.environment)

        compose._executor = RecordingExecutor(result_factory=result_factory)  # type: ignore[attr-defined]
        captured["compose"] = compose
        captured["child"] = compose.capture(compose_decision, project_id=project_id)
        return real_capture(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_coordinator.capture_project_removal_command",
        capture_with_compose_probe,
    )
    client.projects.remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )

    assert captured["compose"] is not None
    child = captured["child"]
    assert child is not None
    catalog.update_environment_state("retained-environment", "removed")
    assert child.run() == compose_decision

    catalog.create_environment(
        make_env(
            "new-retained-environment",
            project_id=project_id,
            repository_root=str(root),
            git_common_dir=str(root / ".git"),
            state="creating",
        )
    )
    assert (
        captured["compose"].capture(  # type: ignore[union-attr]
            compose_decision, project_id=project_id
        )
        is None
    )
    catalog.close()
