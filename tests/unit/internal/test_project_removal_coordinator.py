from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ProjectRemovalError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import PreparedAction
from odoo_instance_sdk.internal.project_removal_coordinator import (
    capture_project_removal_command,
    expected_project_confirmation_for_command,
    set_project_confirmation,
)
from odoo_instance_sdk.internal.publication_routes import PublicationRoute
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models.project_removal import ProjectSelector
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _catalog(tmp_path: Path) -> tuple[BackupCatalog, str, Path]:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    catalog._register_project(project_id, root, root / ".git")
    return catalog, project_id, root


def test_forget_captures_one_mutating_terminal_action_and_preserves_resources(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_path(str(root)),
    )

    assert len(command.plan.steps) == 1
    assert command.plan.steps[0].action == "forget"
    result = command.run()

    assert result.registration_state == "forgotten"
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    assert root.exists()
    catalog.close()


def test_forget_refuses_project_runtime_before_registration_mutation(tmp_path: Path) -> None:
    catalog, project_id, _root = _catalog(tmp_path)
    catalog._upsert_runtime(
        "project",
        project_id,
        root_pid=123,
        create_time=1.0,
        started_at="2026-01-01T00:00:00",
        checkout_branch="main",
        commit_sha="a" * 40,
        http_url="http://127.0.0.1:8069",
        http_port=8069,
        database_name="demo",
    )

    with pytest.raises(ProjectRemovalError, match="active"):
        capture_project_removal_command(
            catalog,
            ProjectSelector.from_project_id(project_id),
        )
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_purge_requires_exact_confirmation_at_execution_and_dry_run_is_inert(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_project_id(project_id),
        purge=True,
    )
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]

    with pytest.raises(ProjectRemovalError, match="confirmation"):
        command.run()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert root.exists()
    catalog.close()


def test_forget_refuses_an_active_project_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_coordinator.read_owned_routes",
        lambda: (
            PublicationRoute(
                owner_kind="project",
                owner_id=project_id,
                project_id=project_id,
                environment_id=None,
                local_endpoint="http://127.0.0.1:8069",
                external_url="https://project.example.test",
                route_identity="route-project",
            ),
        ),
    )

    with pytest.raises(ProjectRemovalError, match=f"publication:project:{project_id}"):
        capture_project_removal_command(catalog, ProjectSelector.from_path(str(root)))

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_purge_flattens_captured_child_plan_and_runs_its_callback(tmp_path: Path) -> None:
    catalog, project_id, _root = _catalog(tmp_path)
    captured: list[str] = []

    class Adapter:
        def capture(self, decision, *, project_id):
            captured.append(project_id)
            step = PreparedAction(
                step_id="child.project.delete",
                action="delete-project-source",
                description="delete project source",
                mutating=True,
            )

            def run(context):
                context.action(step.step_id)
                context.complete_action(step.step_id)
                return decision

            return Command.create(ExecutionPlan(steps=(step.public_projection(),)), run, (step,))

    class Adapters:
        def for_decision(self, decision):
            return Adapter() if decision.identity == f"project:{project_id}" else None

    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=None,
        adapters=Adapters(),
    )
    assert captured == [project_id]
    assert tuple(step.step_id for step in command.plan.steps) == ("child.project.delete",)

    set_project_confirmation(command, expected_project_confirmation_for_command(command))
    result = command.run()
    assert result.completed[0].identity == f"project:{project_id}"
    catalog.close()
