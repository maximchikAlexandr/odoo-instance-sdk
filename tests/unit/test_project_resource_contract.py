from __future__ import annotations

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


def _decision() -> ProjectResourceDecision:
    return ProjectResourceDecision(
        identity="project:abc",
        resource_type="source",
        action="retain",
        reason="caller_owned",
        target="/projects/example",
        order=0,
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
                "order": 0,
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

    with pytest.raises(NotImplementedError, match="coordinator"):
        client.projects.remove_command(ProjectSelector.from_project_id("project_abc"))


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
