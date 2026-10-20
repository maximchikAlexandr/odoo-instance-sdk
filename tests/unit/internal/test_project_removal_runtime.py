from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.exceptions import PublicationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import PreparedAction
from odoo_instance_sdk.internal.project_removal_runtime import (
    EnvironmentPurgeAdapter,
    PublicationPurgeAdapter,
    RuntimeEnvironmentPurgeAdapters,
    RuntimePurgeAdapter,
)
from odoo_instance_sdk.internal.publication_routes import PublicationRoute
from odoo_instance_sdk.models.project_removal import ProjectResourceDecision


def _child(step_id: str) -> Command[None]:
    step = PreparedAction(step_id=step_id, action="guarded", description=step_id, mutating=True)

    def run(context: Any) -> None:
        context.action(step_id)
        context.complete_action(step_id)

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
