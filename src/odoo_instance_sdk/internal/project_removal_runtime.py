"""Runtime, publication, and environment adapters for project purge.

The adapters only compose existing guarded commands.  They do not terminate a
process, rewrite a route file, or remove an environment while a plan is being
captured; those effects remain behind the existing command ledgers, locks,
identity checks, and postconditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    RunContext,
    prepared_command,
)
from odoo_instance_sdk.internal.project_removal_coordinator import ProjectRemovalAdapter
from odoo_instance_sdk.internal.publication_routes import PublicationRoute
from odoo_instance_sdk.models.project_removal import ProjectResourceDecision
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_artifacts import _row_to_env

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.publication import PublicationResult


_T = TypeVar("_T")


def _prefix(family: str, identity: str) -> str:
    """Make a stable, collision-free private step namespace."""

    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in identity)
    return f"project-removal.{family}.{safe}."


def _decision_command(
    command: Command[_T],
    decision: ProjectResourceDecision,
) -> Command[ProjectResourceDecision]:
    """Retain a captured child plan while adapting its typed result."""

    prepared = command._prepared()

    def run(context: RunContext[ProjectResourceDecision]) -> ProjectResourceDecision:
        prepared.callback(cast("RunContext[_T]", context))
        return decision

    return Command.from_prepared(
        command.plan,
        prepared_command(run, prepared.steps, executor=prepared.executor),
    )


def _noop_command(
    decision: ProjectResourceDecision,
    *,
    step_id: str,
) -> Command[ProjectResourceDecision]:
    step = PreparedAction(
        step_id=step_id,
        action=decision.action,
        description=f"{decision.action} {decision.identity}",
        mutating=False,
    )

    def run(context: RunContext[ProjectResourceDecision]) -> ProjectResourceDecision:
        context.action(step.step_id)
        context.complete_action(step.step_id)
        return decision

    return Command.create(
        ExecutionPlan(steps=(step.public_projection(),)),
        run,
        (step,),
    )


def _runtime_target(identity: str) -> tuple[str, str]:
    prefix, separator, remainder = identity.partition(":")
    if prefix != "runtime" or separator == "" or ":" not in remainder:
        raise ValueError(f"invalid runtime identity: {identity}")
    owner_kind, owner_id = remainder.split(":", 1)
    if owner_kind not in {"project", "environment"} or not owner_id:
        raise ValueError(f"invalid runtime identity: {identity}")
    return owner_kind, owner_id


def _publication_target(identity: str) -> tuple[str, str]:
    prefix, separator, remainder = identity.partition(":")
    if prefix != "publication" or separator == "" or ":" not in remainder:
        raise ValueError(f"invalid publication identity: {identity}")
    owner_kind, owner_id = remainder.split(":", 1)
    if owner_kind not in {"project", "environment"} or not owner_id:
        raise ValueError(f"invalid publication identity: {identity}")
    return owner_kind, owner_id


@dataclass(slots=True)
class RuntimePurgeAdapter:
    """Compose one exact project- or environment-owned runtime stop."""

    client: OdooClient
    timeout: float = 10.0

    def capture(
        self,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> Command[ProjectResourceDecision]:
        owner_kind, owner_id = _runtime_target(decision.identity)
        if owner_kind == "project" and owner_id != project_id:
            raise ValueError("runtime is outside the selected project")
        catalog = self.client.get_catalog()
        if owner_kind == "environment":
            row = catalog.get_environment(owner_id)
            if row is None or str(row["project_id"]) != project_id:
                raise ValueError("runtime environment is outside the selected project")
            environment = _row_to_env(row)
            instance = self.client.instance.from_environment(environment)
        else:
            row = catalog.get_project_for_lifecycle(project_id)
            if row is None:
                raise ValueError("project registration disappeared")
            instance = self.client.instance.from_project(
                ProjectConfig.load(Path(str(row["repository_root"])))
            )
        command = instance.stop_runtime_command(
            timeout=self.timeout,
            step_prefix=_prefix("runtime", decision.identity),
        )
        return _decision_command(command, decision)


@dataclass(slots=True)
class PublicationPurgeAdapter:
    """Compose one exact owned-route removal with route identity revalidation."""

    client: OdooClient

    def capture(
        self,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
        route_snapshot: PublicationRoute | None = None,
    ) -> Command[ProjectResourceDecision]:
        owner_kind, owner_id = _publication_target(decision.identity)
        if owner_kind == "project" and owner_id != project_id:
            raise ValueError("publication is outside the selected project")
        # An absent captured route is an idempotent terminal state.
        if route_snapshot is None:
            return _noop_command(
                decision,
                step_id=f"{_prefix('publication', decision.identity)}absent",
            )
        if (
            route_snapshot.owner_kind != owner_kind
            or route_snapshot.owner_id != owner_id
            or route_snapshot.project_id != project_id
        ):
            raise ValueError("publication route is outside the selected project")
        command = self.client.publication.unpublish_command(
            (owner_kind, owner_id),
            step_prefix=_prefix("publication", decision.identity),
            expected_route_identity=route_snapshot.route_identity,
            expected_current_route=route_snapshot,
        )
        prepared = command._prepared()

        def run(context: RunContext[ProjectResourceDecision]) -> ProjectResourceDecision:
            prepared.callback(cast("RunContext[PublicationResult]", context))
            return decision

        return Command.from_prepared(
            command.plan,
            prepared_command(run, prepared.steps, executor=prepared.executor),
        )


@dataclass(slots=True)
class EnvironmentPurgeAdapter:
    """Compose one environment's existing guarded cleanup command."""

    client: OdooClient
    force_connections: bool = False
    repository_root: Path | None = None
    git_common_dir: Path | None = None

    def capture(
        self,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> Command[ProjectResourceDecision]:
        prefix, separator, environment_id = decision.identity.partition(":")
        if prefix != "environment" or separator == "" or not environment_id:
            raise ValueError(f"invalid environment identity: {decision.identity}")
        catalog = self.client.get_catalog()
        row = catalog.get_environment(environment_id)
        project_owned = row is not None and str(row["project_id"]) == project_id
        legacy_owned = (
            row is not None
            and row["project_id"] is None
            and self.repository_root is not None
            and self.git_common_dir is not None
            and str(row["repository_root"]) == str(self.repository_root)
            and str(row["git_common_dir"]) == str(self.git_common_dir)
        )
        if not (project_owned or legacy_owned):
            raise ValueError("environment is outside the selected project")
        assert row is not None
        environment = _row_to_env(row)
        command = self.client.environments.remove_command(
            environment,
            force_connections=self.force_connections,
            step_prefix=_prefix("environment", decision.identity),
        )
        return _decision_command(command, decision)


@dataclass(slots=True)
class RuntimeEnvironmentPurgeAdapters:
    """Adapter registry consumed by the project-removal coordinator."""

    client: OdooClient
    runtime_timeout: float = 10.0
    force_environment_connections: bool = False
    repository_root: Path | None = None
    git_common_dir: Path | None = None
    _runtime: RuntimePurgeAdapter = field(init=False, repr=False)
    _publication: PublicationPurgeAdapter = field(init=False, repr=False)
    _environment: EnvironmentPurgeAdapter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._runtime = RuntimePurgeAdapter(self.client, timeout=self.runtime_timeout)
        self._publication = PublicationPurgeAdapter(self.client)
        self._environment = EnvironmentPurgeAdapter(
            self.client,
            force_connections=self.force_environment_connections,
            repository_root=self.repository_root,
            git_common_dir=self.git_common_dir,
        )

    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None:
        if decision.action == "stop" and decision.resource_type == "runtime":
            return self._runtime
        if decision.action == "unpublish" and decision.resource_type == "publication":
            return self._publication
        if decision.action == "delete" and decision.resource_type == "environment":
            return self._environment
        return None


__all__ = [
    "EnvironmentPurgeAdapter",
    "PublicationPurgeAdapter",
    "RuntimeEnvironmentPurgeAdapters",
    "RuntimePurgeAdapter",
]
