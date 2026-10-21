from __future__ import annotations

from pathlib import Path
from typing import NoReturn, cast

import pytest

from odoo_instance_sdk.exceptions import ProjectRemovalError
from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import PreparedAction
from odoo_instance_sdk.internal.project_removal import (
    InternalRemovalDecision,
    ProjectRemovalProjection,
)
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


def test_forget_captures_one_mutating_terminal_action_and_preserves_resources(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_path(str(root)),
    )

    assert len(command.plan.steps) == 1
    assert isinstance(command.plan.steps[0], ActionStep)
    assert command.plan.steps[0].action == "forget"
    result = command.run()

    assert result.registration_state == "forgotten"
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    assert root.exists()
    catalog.close()


def test_forget_refuses_matching_legacy_environment_and_preserves_registration(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    _legacy_environment(catalog, "legacy-alpha", root)

    with pytest.raises(ProjectRemovalError, match="environment:legacy-alpha"):
        capture_project_removal_command(catalog, ProjectSelector.from_project_id(project_id))

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert catalog.get_environment("legacy-alpha") is not None
    catalog.close()


def test_forget_refuses_matching_legacy_runtime_and_preserves_runtime(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    _legacy_environment(catalog, "legacy-alpha", root)
    catalog._upsert_runtime(
        "environment",
        "legacy-alpha",
        root_pid=123,
        create_time=1.0,
        started_at="2026-01-01T00:00:00",
        checkout_branch="main",
        commit_sha="a" * 40,
        http_url="http://127.0.0.1:8069",
        http_port=8069,
        database_name="demo",
    )
    catalog._conn.execute(
        "UPDATE environments SET state = 'removed' WHERE id = ?", ("legacy-alpha",)
    )
    catalog._conn.commit()

    with pytest.raises(ProjectRemovalError, match="runtime:environment:legacy-alpha"):
        capture_project_removal_command(catalog, ProjectSelector.from_project_id(project_id))

    assert catalog.get_runtime("environment", "legacy-alpha") is not None
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_foreign_legacy_environment_does_not_block_alpha_forget(tmp_path: Path) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    beta_root = tmp_path / "beta"
    (beta_root / ".git").mkdir(parents=True)
    _legacy_environment(catalog, "legacy-beta", beta_root)

    result = capture_project_removal_command(
        catalog, ProjectSelector.from_project_id(project_id)
    ).run()

    assert result.registration_state == "forgotten"
    assert catalog.get_environment("legacy-beta") is not None
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    assert root.exists()
    catalog.close()


def test_forgotten_and_unknown_projects_keep_legacy_resources_unchanged(
    tmp_path: Path,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    _legacy_environment(catalog, "legacy-alpha", root)
    assert catalog.forget_project(project_id)

    result = capture_project_removal_command(
        catalog, ProjectSelector.from_project_id(project_id)
    ).run()
    assert result.registration_state == "forgotten"
    assert catalog.get_environment("legacy-alpha") is not None

    with pytest.raises(ValueError, match="unknown or non-unique"):
        capture_project_removal_command(catalog, ProjectSelector.from_project_id("project-unknown"))
    catalog.close()


def test_forget_refuses_reactivation_after_capture(tmp_path: Path) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    assert catalog.forget_project(project_id)
    command = capture_project_removal_command(catalog, ProjectSelector.from_project_id(project_id))
    assert catalog.reactivate_project(project_id, root, root / ".git")

    with pytest.raises(ProjectRemovalError, match="registration state changed"):
        command.run()

    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    assert root.exists()
    catalog.close()


def test_forget_refuses_nonblocking_resource_decision_drift(tmp_path: Path) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    command = capture_project_removal_command(catalog, ProjectSelector.from_project_id(project_id))
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


def test_purge_rejects_wrong_confirmation_before_lock_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation="wrong-token",
    )
    lock_entered = False

    def forbidden_lock(_path: Path) -> NoReturn:
        nonlocal lock_entered
        lock_entered = True
        raise AssertionError("confirmation must be checked before lock acquisition")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_coordinator.exclusive_lock",
        forbidden_lock,
    )
    with pytest.raises(ProjectRemovalError, match="confirmation"):
        command.run()

    assert not lock_entered
    assert root.exists()
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()


def test_purge_fails_closed_when_mutating_adapter_is_missing(tmp_path: Path) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=f"{root.name}:{project_id}",
    )

    with pytest.raises(ProjectRemovalError, match="captured adapter") as error:
        command.run()
    details = cast("dict[str, object]", error.value.details["removal"])
    assert details["registration_state"] == "registered"
    assert isinstance(details["remaining_total"], int)
    assert details["remaining_total"] >= 1
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
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


def test_purge_flattens_captured_child_plan_and_runs_its_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    captured: list[str] = []
    projection = ProjectRemovalProjection(
        project_id=project_id,
        registration_state="registered",
        repository_root=root,
        expected_confirmation=f"{root.name}:{project_id}",
        decisions=(
            InternalRemovalDecision(
                identity=f"project:{project_id}",
                resource_type="source",
                action="delete",
                reason="registered_primary_source",
                target="<local>",
            ),
        ),
        fingerprint="stable-test-projection",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_coordinator._projection",
        lambda _catalog, _registration, **_kwargs: projection,
    )

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
    assert isinstance(command.plan.steps[-1], ActionStep)
    assert command.plan.steps[-1].action == "forget"
    assert tuple(step.step_id for step in command.plan.steps[:-1]) == ("child.project.delete",)

    set_project_confirmation(command, expected_project_confirmation_for_command(command))
    result = command.run()
    assert result.completed[0].identity == f"project:{project_id}"
    assert result.completed[-1].action == "forget"
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()
