from __future__ import annotations

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
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


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
