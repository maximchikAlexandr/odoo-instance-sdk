from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, get_type_hints
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.exceptions import PublicationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import PreparedAction, RunContext
from odoo_instance_sdk.internal.project_removal import (
    InternalRemovalDecision,
    ProjectRegistration,
    ProjectRemovalProjection,
)
from odoo_instance_sdk.internal.project_removal_coordinator import (
    ProjectRemovalAdapter,
    capture_project_removal_command,
    expected_project_confirmation_for_command,
    set_project_confirmation,
)
from odoo_instance_sdk.internal.project_removal_runtime import (
    EnvironmentPurgeAdapter,
    PublicationPurgeAdapter,
    RuntimeEnvironmentPurgeAdapters,
    RuntimePurgeAdapter,
)
from odoo_instance_sdk.internal.publication_routes import PublicationRoute
from odoo_instance_sdk.models.project_removal import ProjectResourceDecision, ProjectSelector
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

if TYPE_CHECKING:
    from pathlib import Path

    from odoo_instance_sdk.internal.project_removal_coordinator import ProjectRemovalAdapters


def _child(step_id: str) -> Command[None]:
    step = PreparedAction(step_id=step_id, action="guarded", description=step_id, mutating=True)

    def run(context: RunContext[None]) -> None:
        context.action(step_id)
        context.complete_action(step_id)

    return Command.create(
        ExecutionPlan(steps=(step.public_projection(),)),
        run,
        (step,),
    )


def _status_child(step_id: str, status: str, seen: list[str]) -> Command[dict[str, str]]:
    step = PreparedAction(step_id=step_id, action="guarded", description=step_id, mutating=True)

    def run(context: RunContext[dict[str, str]]) -> dict[str, str]:
        context.action(step_id)
        context.complete_action(step_id)
        seen.append(status)
        return {"status": status}

    return Command.create(
        ExecutionPlan(steps=(step.public_projection(),)),
        run,
        (step,),
    )


def _decision(
    identity: str,
    resource_type: str,
    action: str,
) -> ProjectResourceDecision:
    return ProjectResourceDecision(
        identity=identity,
        resource_type=resource_type,
        action=action,  # type: ignore[arg-type]
        reason="exclusive_sdk_ownership",
        target="<local>",
    )


def test_runtime_adapter_captures_prefixed_project_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime.ProjectConfig.load",
        lambda _path: object(),
    )
    client = MagicMock()
    client.get_catalog().get_project_for_lifecycle.return_value = {
        "repository_root": "/project",
    }
    instance = MagicMock()
    client.instance.from_project.return_value = instance
    instance.stop_runtime_command.return_value = _child("runtime.stop")

    adapter = RuntimePurgeAdapter(client)
    decision = _decision("runtime:project:project-a", "runtime", "stop")
    captured = adapter.capture(decision, project_id="project-a")

    assert captured.plan.steps[0].step_id == "runtime.stop"
    instance.stop_runtime_command.assert_called_once_with(
        timeout=10.0,
        step_prefix="project-removal.runtime.runtime_project_project-a.",
    )


@pytest.mark.parametrize(
    ("owner_kind", "runtime_state"),
    [
        ("project", "live"),
        ("project", "stale"),
        ("project", "absent"),
        ("environment", "live"),
        ("environment", "stale"),
        ("environment", "absent"),
    ],
    ids=(
        "project-live",
        "project-stale",
        "project-absent",
        "environment-live",
        "environment-stale",
        "environment-absent",
    ),
)
def test_runtime_adapter_preserves_live_stale_and_absent_runtime_evidence(
    owner_kind: Literal["project", "environment"],
    runtime_state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "project-a"
    environment_id = "11111111-1111-4111-8111-111111111111"
    owner_id = project_id if owner_kind == "project" else environment_id
    identity = f"runtime:{owner_kind}:{owner_id}"
    client = MagicMock()
    client.get_catalog().get_project_for_lifecycle.return_value = {
        "repository_root": "/project",
    }
    client.get_catalog().get_environment.return_value = {"project_id": project_id}
    environment = SimpleNamespace(id=environment_id)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime._row_to_env",
        lambda _row: environment,
    )
    seen: list[str] = []
    instance = MagicMock()
    instance.stop_runtime_command.return_value = _status_child(
        f"runtime.{owner_kind}.{runtime_state}", runtime_state, seen
    )
    client.instance.from_project.return_value = instance
    client.instance.from_environment.return_value = instance
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime.ProjectConfig.load",
        lambda _path: object(),
    )

    captured = RuntimePurgeAdapter(client).capture(
        _decision(identity, "runtime", "stop"), project_id=project_id
    )

    assert captured.run().identity == identity
    assert seen == [runtime_state]
    if owner_kind == "project":
        client.instance.from_project.assert_called_once()
        client.instance.from_environment.assert_not_called()
    else:
        client.instance.from_environment.assert_called_once_with(environment)
        client.instance.from_project.assert_not_called()


@pytest.mark.parametrize(
    "decision",
    [
        _decision("runtime:project:other-project", "runtime", "stop"),
        _decision(
            "runtime:environment:66666666-6666-4666-8666-666666666666",
            "runtime",
            "stop",
        ),
    ],
    ids=("foreign-project-runtime", "foreign-environment-runtime"),
)
def test_runtime_adapter_rejects_unrelated_runtime_ownership(
    decision: ProjectResourceDecision,
) -> None:
    client = MagicMock()
    client.get_catalog().get_environment.return_value = {"project_id": "other-project"}

    with pytest.raises(ValueError, match="outside the selected project"):
        RuntimePurgeAdapter(client).capture(decision, project_id="project-a")


def test_publication_adapter_rejects_route_identity_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    original = PublicationRoute(
        owner_kind="project",
        owner_id="project-a",
        project_id="project-a",
        environment_id=None,
        local_endpoint="http://127.0.0.1:8069",
        external_url="https://project-a.example.test",
        route_identity="original",
    )
    changed = PublicationRoute(
        owner_kind="project",
        owner_id="project-a",
        project_id="project-a",
        environment_id=None,
        local_endpoint="http://127.0.0.1:8070",
        external_url="https://project-a.example.test",
        route_identity="changed",
    )
    routes = iter(((original,), (changed,)))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime.read_owned_routes",
        lambda: next(routes),
    )
    client = MagicMock()
    client.publication.unpublish_command.return_value = _child("publication.remove")
    adapter = PublicationPurgeAdapter(client)
    decision = _decision("publication:project:project-a", "publication", "unpublish")
    captured = adapter.capture(decision, project_id="project-a")

    with pytest.raises(PublicationError, match="identity changed"):
        captured.run()


@pytest.mark.parametrize(
    ("owner_kind", "route_present"),
    [("project", True), ("project", False), ("environment", True), ("environment", False)],
    ids=("project-present", "project-absent", "environment-present", "environment-absent"),
)
def test_publication_adapter_handles_present_and_already_absent_owned_routes(
    owner_kind: Literal["project", "environment"],
    route_present: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "project-a"
    owner_id = project_id if owner_kind == "project" else "22222222-2222-4222-8222-222222222222"
    route = PublicationRoute(
        owner_kind=owner_kind,
        owner_id=owner_id,
        project_id=project_id,
        environment_id=owner_id if owner_kind == "environment" else None,
        local_endpoint="http://127.0.0.1:8069",
        external_url="https://project-a.example.test",
        route_identity="owned-route",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime.read_owned_routes",
        lambda: (route,) if route_present else (),
    )
    client = MagicMock()
    client.publication.unpublish_command.return_value = _child("publication.unpublish")
    decision = _decision(f"publication:{owner_kind}:{owner_id}", "publication", "unpublish")

    captured = PublicationPurgeAdapter(client).capture(decision, project_id=project_id)

    assert captured.run() == decision
    if route_present:
        client.publication.unpublish_command.assert_called_once_with(
            (owner_kind, owner_id),
            step_prefix=f"project-removal.publication.publication_{owner_kind}_{owner_id}.",
        )
    else:
        client.publication.unpublish_command.assert_not_called()
        assert captured.plan.steps[0].step_id.endswith("absent")


def test_publication_adapter_rejects_route_owned_by_another_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = PublicationRoute(
        owner_kind="environment",
        owner_id="55555555-5555-4555-8555-555555555555",
        project_id="other-project",
        environment_id="55555555-5555-4555-8555-555555555555",
        local_endpoint="http://127.0.0.1:8069",
        external_url="https://other-project.example.test",
        route_identity="foreign-route",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime.read_owned_routes",
        lambda: (route,),
    )

    with pytest.raises(ValueError, match="outside the selected project"):
        PublicationPurgeAdapter(MagicMock()).capture(
            _decision(
                "publication:environment:55555555-5555-4555-8555-555555555555",
                "publication",
                "unpublish",
            ),
            project_id="project-a",
        )


def test_environment_adapter_uses_full_id_and_project_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.get_catalog().get_environment.return_value = {
        "project_id": "project-a",
    }
    environment = SimpleNamespace(id="env-b")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime._row_to_env",
        lambda _row: environment,
    )
    client.environments.remove_command.return_value = _child("environment.remove")
    adapter = EnvironmentPurgeAdapter(client)
    decision = _decision("environment:env-b", "environment", "delete")
    captured = adapter.capture(decision, project_id="project-a")

    assert captured.plan.steps[0].step_id == "environment.remove"
    client.environments.remove_command.assert_called_once()
    kwargs = client.environments.remove_command.call_args.kwargs
    assert kwargs["step_prefix"] == "project-removal.environment.environment_env-b."


def test_environment_adapter_preserves_cleanup_failed_and_already_removed_retry_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "project-a"
    environment_id = "33333333-3333-4333-8333-333333333333"
    environment = SimpleNamespace(id=environment_id)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime._row_to_env",
        lambda _row: environment,
    )
    client = MagicMock()
    client.get_catalog().get_environment.return_value = {"project_id": project_id}
    outcomes = iter(("cleanup_failed", "already_removed"))
    seen: list[str] = []

    def remove_command(
        selected: object, *, force_connections: bool, step_prefix: str
    ) -> Command[dict[str, str]]:
        assert selected is environment
        assert force_connections is True
        return _status_child(f"{step_prefix}remove", next(outcomes), seen)

    client.environments.remove_command.side_effect = remove_command
    decision = _decision(f"environment:{environment_id}", "environment", "delete")
    adapter = EnvironmentPurgeAdapter(client, force_connections=True)

    first = adapter.capture(decision, project_id=project_id)
    second = adapter.capture(decision, project_id=project_id)
    assert first.run() == decision
    assert second.run() == decision
    assert seen == ["cleanup_failed", "already_removed"]
    assert first.plan.steps[0].step_id == second.plan.steps[0].step_id
    assert environment_id in client.environments.remove_command.call_args_list[0].args[0].id


def test_environment_adapter_rejects_unrelated_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment_id = "77777777-7777-4777-8777-777777777777"
    client = MagicMock()
    client.get_catalog().get_environment.return_value = {"project_id": "other-project"}
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime._row_to_env",
        lambda _row: SimpleNamespace(id=environment_id),
    )

    with pytest.raises(ValueError, match="outside the selected project"):
        EnvironmentPurgeAdapter(client).capture(
            _decision(f"environment:{environment_id}", "environment", "delete"),
            project_id="project-a",
        )


def test_environment_adapter_accepts_legacy_row_only_for_exact_project_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment_id = "88888888-8888-4888-8888-888888888888"
    repository_root = tmp_path / "project"
    git_common_dir = repository_root / ".git"
    client = MagicMock()
    client.get_catalog().get_environment.return_value = {
        "project_id": None,
        "repository_root": str(repository_root),
        "git_common_dir": str(git_common_dir),
    }
    environment = SimpleNamespace(id=environment_id)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime._row_to_env",
        lambda _row: environment,
    )
    client.environments.remove_command.return_value = _child("environment.remove")
    adapter = EnvironmentPurgeAdapter(
        client,
        repository_root=repository_root,
        git_common_dir=git_common_dir,
    )

    captured = adapter.capture(
        _decision(f"environment:{environment_id}", "environment", "delete"),
        project_id="project-a",
    )

    assert captured.plan.steps[0].step_id == "environment.remove"
    client.environments.remove_command.assert_called_once()


def test_registry_only_selects_wp05_decisions() -> None:
    registry = RuntimeEnvironmentPurgeAdapters(MagicMock())

    assert isinstance(
        registry.for_decision(_decision("runtime:project:p", "runtime", "stop")),
        RuntimePurgeAdapter,
    )
    assert isinstance(
        registry.for_decision(_decision("publication:project:p", "publication", "unpublish")),
        PublicationPurgeAdapter,
    )
    assert isinstance(
        registry.for_decision(_decision("environment:e", "environment", "delete")),
        EnvironmentPurgeAdapter,
    )
    assert registry.for_decision(_decision("database:d", "database", "delete")) is None


def test_runtime_registry_satisfies_project_removal_adapters_protocol() -> None:
    annotations = get_type_hints(RuntimeEnvironmentPurgeAdapters.for_decision)
    assert annotations["return"] == ProjectRemovalAdapter | None
    if TYPE_CHECKING:
        _assert_project_removal_adapters(RuntimeEnvironmentPurgeAdapters(MagicMock()))


def _assert_project_removal_adapters(adapters: ProjectRemovalAdapters) -> None:
    del adapters


def test_runtime_adapter_integration_preserves_order_identity_and_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, project_id, root = _catalog(tmp_path)
    environment_id = "44444444-4444-4444-8444-444444444444"
    registration = catalog.get_project_for_lifecycle(project_id)
    assert registration is not None
    decisions = (
        InternalRemovalDecision(
            identity=f"runtime:project:{project_id}",
            resource_type="runtime",
            action="stop",
            reason="exclusive_sdk_ownership",
            target="<local>",
        ),
        InternalRemovalDecision(
            identity=f"publication:project:{project_id}",
            resource_type="publication",
            action="unpublish",
            reason="exclusive_sdk_ownership",
            target="<local>",
        ),
        InternalRemovalDecision(
            identity=f"environment:{environment_id}",
            resource_type="environment",
            action="delete",
            reason="exclusive_sdk_ownership",
            target="<local>",
        ),
    )
    projection = ProjectRemovalProjection(
        project_id=project_id,
        registration_state="registered",
        repository_root=root,
        expected_confirmation="project-removal-confirmation",
        decisions=decisions,
        fingerprint="stable-project-removal-fingerprint",
    )

    def projection_without_git_status(
        _catalog: BackupCatalog,
        _registration: ProjectRegistration,
        *,
        git_status_step_id: str | None = None,
        git_status_hardened: bool = False,
    ) -> ProjectRemovalProjection:
        del git_status_step_id
        del git_status_hardened
        return projection

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_coordinator._projection",
        projection_without_git_status,
    )
    route = PublicationRoute(
        owner_kind="project",
        owner_id=project_id,
        project_id=project_id,
        environment_id=None,
        local_endpoint="http://127.0.0.1:8069",
        external_url="https://project-a.example.test",
        route_identity="stable-project-route",
    )
    unrelated_route = PublicationRoute(
        owner_kind="project",
        owner_id="unrelated-project",
        project_id="unrelated-project",
        environment_id=None,
        local_endpoint="http://127.0.0.1:8070",
        external_url="https://unrelated.example.test",
        route_identity="unrelated-route",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime.read_owned_routes",
        lambda: (route, unrelated_route),
    )
    client = MagicMock()
    client.get_catalog().get_project_for_lifecycle.return_value = {
        "repository_root": str(root),
    }
    client.get_catalog().get_environment.return_value = {"project_id": project_id}
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime.ProjectConfig.load",
        lambda _path: object(),
    )
    environment = SimpleNamespace(id=environment_id)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_removal_runtime._row_to_env",
        lambda _row: environment,
    )
    client.instance.from_project.return_value.stop_runtime_command.side_effect = (
        lambda *, timeout, step_prefix: _child(f"{step_prefix}stop")
    )
    client.instance.from_environment.return_value = client.instance.from_project.return_value
    client.publication.unpublish_command.side_effect = lambda target, *, step_prefix: _child(
        f"{step_prefix}unpublish"
    )
    client.environments.remove_command.side_effect = (
        lambda selected, *, force_connections, step_prefix: _child(f"{step_prefix}remove")
    )

    command = capture_project_removal_command(
        catalog,
        ProjectSelector.from_project_id(project_id),
        purge=True,
        adapters=RuntimeEnvironmentPurgeAdapters(client),
    )
    set_project_confirmation(command, expected_project_confirmation_for_command(command))

    step_ids = tuple(step.step_id for step in command.plan.steps)
    assert step_ids == (
        f"project-removal.runtime.runtime_project_{project_id}.stop",
        f"project-removal.publication.publication_project_{project_id}.unpublish",
        f"project-removal.environment.environment_{environment_id}.remove",
        f"project-removal:forget:project-forget:{project_id}",
    )
    assert len(step_ids) == len(set(step_ids))
    result = command.run()
    assert tuple(item.identity for item in result.completed) == (
        *(item.identity for item in projection.public_decisions),
        f"project-forget:{project_id}",
    )
    assert result.registration_state == "forgotten"
    client.publication.unpublish_command.assert_called_once()
    assert unrelated_route.owner_id not in " ".join(step_ids)
    catalog.close()


def _catalog(tmp_path: Path) -> tuple[BackupCatalog, str, Path]:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    from odoo_instance_sdk.internal.repo_key import repo_key

    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    catalog._register_project(project_id, root, root / ".git")
    return catalog, project_id, root
