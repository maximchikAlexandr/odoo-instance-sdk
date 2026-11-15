from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Literal

from odoo_instance_sdk.exceptions import EnvironmentNotFoundError
from odoo_instance_sdk.models import RuntimeActionRequest, RuntimeActionResult
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.http.browser_login import LoginTarget
    from odoo_instance_sdk.http.monitor import SnapshotProvider


class RuntimeController:
    """Resolve catalog identities and delegate to the public instance commands."""

    def __init__(
        self,
        config: OdooClientConfig,
        monitor: SnapshotProvider,
        *,
        panel_origin: str | None = None,
    ) -> None:
        self.config = config
        self.client: OdooClient | None = None
        self.monitor = monitor
        self.lock = threading.Lock()
        self.panel_origin = panel_origin

    def resolve(self, owner_kind: Literal["project", "environment"], owner_id: str) -> LoginTarget:
        from odoo_instance_sdk.http.browser_login import LoginTarget
        from odoo_instance_sdk.models import RuntimeState

        if not self.panel_origin:
            raise LookupError("browser login unavailable")
        with self.lock:
            if self.client is None:
                from odoo_instance_sdk.client import OdooClient

                self.client = OdooClient(config=self.config)
            if owner_kind == "project":
                row = self.client.get_catalog().get_project(owner_id)
                if row is None:
                    raise LookupError("project not found")
                instance = self.client.instance.from_project(
                    ProjectConfig.load(row["repository_root"])
                )
                snapshot = self.monitor.snapshot(project_id=owner_id)
                project_record = next(
                    (item for item in snapshot.projects if item.id == owner_id), None
                )
                runtime = project_record.runtime if project_record else None
                publication = project_record.publication if project_record else None
            else:
                environment = self.client.environments.get(owner_id)
                instance = self.client.instance.from_environment(environment)
                snapshot = self.monitor.snapshot(project_id=environment.project_id)
                environment_record = next(
                    (item for item in snapshot.environments if item.id == owner_id), None
                )
                runtime = environment_record.runtime if environment_record else None
                publication = environment_record.publication if environment_record else None
            if (
                runtime is None
                or runtime.state != RuntimeState.READY
                or not runtime.database_name
                or publication is None
                or publication.state != "available"
                or not publication.external_url
            ):
                raise LookupError("browser login unavailable")
            identity = f"{runtime.root_pid}:{runtime.started_at}:{runtime.commit_sha}"
            return LoginTarget(
                owner_kind=owner_kind,
                owner_id=owner_id,
                runtime_id=identity,
                database=runtime.database_name,
                instance_url=publication.external_url,
                panel_origin=self.panel_origin,
                list_users=lambda query: instance.list_browser_login_users_command(query).run(),
                create_session=lambda user_id: (
                    instance.create_browser_session_command(user_id).run().sid
                ),
            )

    def __call__(
        self, action: Literal["start", "stop"], target: RuntimeActionRequest
    ) -> RuntimeActionResult:
        if action not in ("start", "stop"):
            raise ValueError(f"unsupported runtime action: {action}")
        # Serialize dashboard mutations, including the live-state check, so two
        # tabs cannot start the same stopped instance before its record appears.
        with self.lock:
            if self.client is None:
                from odoo_instance_sdk.client import OdooClient

                self.client = OdooClient(config=self.config)
            project_id: str | None
            if target.owner_kind == "project":
                row = self.client.get_catalog().get_project(target.owner_id)
                if row is None:
                    raise LookupError("project not found")
                instance = self.client.instance.from_project(
                    ProjectConfig.load(row["repository_root"])
                )
                project_id = target.owner_id
            else:
                try:
                    environment = self.client.environments.get(target.owner_id)
                except EnvironmentNotFoundError as exc:
                    raise LookupError("environment not found") from exc
                instance = self.client.instance.from_environment(environment)
                project_id = environment.project_id
            if action == "start":
                snapshot = self.monitor.snapshot(project_id=project_id)
                runtimes = (
                    [p.runtime for p in snapshot.projects if p.id == target.owner_id]
                    if target.owner_kind == "project"
                    else [e.runtime for e in snapshot.environments if e.id == target.owner_id]
                )
                if any(runtime is not None and runtime.state != "stopped" for runtime in runtimes):
                    raise RuntimeError("runtime is already running")
                instance.run_detached_command().run()
                return RuntimeActionResult(state="started")
            instance.stop_runtime_command().run()
            return RuntimeActionResult(state="stopped")
