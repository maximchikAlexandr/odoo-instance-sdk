"""Thin typed composition of native Multica and core Odoo operations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar, cast

from odcli_multica.models import (
    MULTICA_PY_REVISION,
    MULTICA_PY_VERSION,
    ContextRequest,
    ContextVerificationError,
    MulticaCompatibility,
    PreparationRequest,
    VerifiedTaskContext,
)
from odoo_instance_sdk import (
    Command,
    DevelopmentEnvironment,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    OdooClient,
)
from odoo_instance_sdk.commands.output import action_command
from odoo_instance_sdk.execution import ExecutionPlan

if TYPE_CHECKING:
    from multica_py import Issue, MulticaClient, OperationOptions, Page, Project, TaskRun
    from multica_py.models.system import DaemonStatus, RepositoryCheckoutResult

T_co = TypeVar("T_co", covariant=True)


class _Runnable(Protocol[T_co]):
    def run(self) -> T_co: ...


@dataclass(frozen=True, slots=True)
class PrepareCommand:
    """Inspectable context plus the exact captured core adoption command."""

    context: VerifiedTaskContext
    adoption_command: Command[DevelopmentEnvironment]

    @property
    def plan(self) -> ExecutionPlan:
        """Expose the core plan without introducing another plan model."""
        return self.adoption_command.plan

    def run(self) -> DevelopmentEnvironment:
        """Delegate execution to the already captured core command."""
        return self.adoption_command.run()


class MulticaOdooClient:
    """Stateless native-checkout/context/preparation client."""

    def __init__(
        self,
        core_client: OdooClient,
        multica_client: MulticaClient,
        *,
        compatibility: MulticaCompatibility | None = None,
    ) -> None:
        self.core = core_client
        self.multica = multica_client
        self.compatibility = compatibility or MulticaCompatibility()

    def _require_contract(self) -> None:
        identity = self.compatibility
        if identity.package_version != MULTICA_PY_VERSION:
            raise ContextVerificationError("multica-py package version is ambiguous")
        if identity.package_revision != MULTICA_PY_REVISION:
            raise ContextVerificationError("multica-py package revision is ambiguous")
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", identity.native_cli_version)
        if match is None or tuple(int(part) for part in match.groups()) < (0, 5, 3):
            raise ContextVerificationError("native Multica CLI is below the supported floor")

    def checkout_command(
        self,
        url: str,
        *,
        ref: str | None = None,
        options: OperationOptions | None = None,
    ) -> _Runnable[RepositoryCheckoutResult]:
        """Return the typed native checkout command, never forcing ``fresh``."""
        self._require_contract()
        return self.multica.repositories.checkout_command(
            url,
            ref=ref,
            fresh=False,
            options=options,
        )

    def checkout(
        self,
        url: str,
        *,
        ref: str | None = None,
        options: OperationOptions | None = None,
    ) -> RepositoryCheckoutResult:
        """Delegate the typed native checkout convenience operation."""
        self._require_contract()
        return self.multica.repositories.checkout(url, ref=ref, fresh=False, options=options)

    def context_command(self, request: ContextRequest) -> Command[VerifiedTaskContext]:
        """Capture bounded typed reads for one explicit task context."""
        self._require_contract()
        return cast(
            "Command[VerifiedTaskContext]",
            action_command(
                "multica.context",
                lambda: self._read_context(request),
                description="Verify the native checkout, issue, run, project, and daemon context",
            ),
        )

    def context(self, request: ContextRequest) -> VerifiedTaskContext:
        """Run the finite context observation."""
        return self.context_command(request).run()

    def prepare_command(self, request: PreparationRequest) -> PrepareCommand:
        """Preflight context, then capture one exact core adoption command."""
        context = self.context(request.context)
        if not request.base_ref.strip():
            raise ContextVerificationError("preparation requires an explicit base_ref")
        selectors = sum(
            value is not None
            for value in (request.remote_name, request.backup_id, request.source_database)
        )
        if selectors != 1:
            raise ContextVerificationError(
                "preparation requires exactly one of remote_name, backup_id, or source_database"
            )
        if request.source_database is not None:
            raise ContextVerificationError(
                "caller-owned adoption supports remote_name or backup_id only"
            )
        options = EnvironmentCheckoutOptions(
            base_ref=request.base_ref,
            db_mode=EnvironmentDatabaseMode.COPY,
            remote_name=request.remote_name,
            backup_id=request.backup_id,
        )
        adoption = self.core.environments.adopt_command(
            request.context.core_project,
            Path(context.checkout_path),
            options=options,
        )
        return PrepareCommand(context=context, adoption_command=adoption)

    def prepare(self, request: PreparationRequest) -> DevelopmentEnvironment:
        """Delegate execution to the captured adoption command."""
        return self.prepare_command(request).run()

    def daemon_status_command(
        self, *, options: OperationOptions | None = None
    ) -> _Runnable[DaemonStatus]:
        """Expose the typed daemon command for callers that need inspection."""
        self._require_contract()
        return self.multica.daemon.status_command(options=options)

    def daemon_status(self, *, options: OperationOptions | None = None) -> DaemonStatus:
        """Expose the typed daemon status without decoding output locally."""
        self._require_contract()
        return self.multica.daemon.status(options=options)

    def _read_context(self, request: ContextRequest) -> VerifiedTaskContext:
        checkout = _canonical_checkout(request.checkout_path)
        project: Project = self.multica.projects.get(request.multica_project)
        issue: Issue = self.multica.issues.get(request.issue)
        if issue.project_id != project.id:
            raise ContextVerificationError("issue is not a member of the selected Multica project")
        if issue.project_id != request.multica_project:
            raise ContextVerificationError("issue project identity conflicts with the request")
        run = _find_run(self.multica.issues.runs(request.issue), request.run)
        if run.issue_id not in (None, request.issue):
            raise ContextVerificationError("run issue identity conflicts with the request")
        if run.project_id != project.id:
            raise ContextVerificationError("run project identity conflicts with the request")
        workspace_id = _required(run.workspace_id, "run workspace identity")
        if request.workspace_id is not None and request.workspace_id != workspace_id:
            raise ContextVerificationError("requested workspace conflicts with the run")
        if project.workspace_id != workspace_id:
            raise ContextVerificationError("project and run workspace identities conflict")
        configured_workspace = self.multica.config.workspace_id
        if configured_workspace is not None and configured_workspace != workspace_id:
            raise ContextVerificationError("scoped Multica workspace conflicts with the run")
        runtime_id = _required(run.runtime_id, "run runtime identity")
        if request.runtime_id is not None and request.runtime_id != runtime_id:
            raise ContextVerificationError("requested runtime conflicts with the run")
        task_root = _task_root(run, checkout)
        repository_url = _repository_url(run, request.repository_url)
        daemon = self.multica.daemon.status()
        daemon_id = _required(daemon.daemon_id, "daemon identity")
        _verify_daemon(daemon, workspace_id, runtime_id, task_root, checkout)
        if not request.core_project.is_dir():
            raise ContextVerificationError("core project path is not a directory")
        return VerifiedTaskContext(
            checkout_path=str(checkout),
            task_root=str(task_root),
            repository_url=repository_url,
            workspace_id=workspace_id,
            multica_project_id=project.id,
            issue_id=request.issue,
            run_id=request.run,
            runtime_id=runtime_id,
            daemon_id=daemon_id,
            observed_at=datetime.now(UTC),
        )


def _canonical_checkout(value: Path) -> Path:
    try:
        checkout = value.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ContextVerificationError("checkout path is unavailable") from exc
    if not checkout.is_dir():
        raise ContextVerificationError("checkout path is not a directory")
    return checkout


def _find_run(page: Page[TaskRun], run_id: str) -> TaskRun:
    if page.has_more or (page.total is not None and page.total > len(page.items)):
        raise ContextVerificationError("run evidence is paginated and incomplete")
    for run in page.items:
        if run.id == run_id:
            return run
    raise ContextVerificationError("run is not present in the selected issue")


def _required(value: str | None, label: str) -> str:
    if value is None or not value.strip():
        raise ContextVerificationError(f"{label} is unavailable")
    return value


def _task_root(run: TaskRun, checkout: Path) -> Path:
    candidates = (run.work_dir, run.durable_work_dir)
    for value in candidates:
        if value is None:
            continue
        root = Path(value)
        if not root.is_absolute():
            continue
        try:
            canonical = root.resolve(strict=True)
        except OSError:
            continue
        if checkout == canonical or checkout.is_relative_to(canonical):
            return canonical
    raise ContextVerificationError(
        "checkout is not contained by an absolute current or durable task directory"
    )


def _repository_url(run: TaskRun, requested: str | None) -> str:
    values = tuple(
        resource.resource_ref
        for resource in run.project_resources
        if resource.resource_type in {"github_repo", "git_repository", "repository"}
        and isinstance(resource.resource_ref, str)
    )
    if requested is not None:
        if requested not in values:
            raise ContextVerificationError("selected repository is not a project resource")
        return requested
    if len(values) != 1:
        raise ContextVerificationError("repository identity is absent or ambiguous")
    return values[0]


def _verify_daemon(
    daemon: DaemonStatus,
    workspace_id: str,
    runtime_id: str,
    task_root: Path,
    checkout: Path,
) -> None:
    if daemon.status.lower() not in {"ready", "running", "active"}:
        raise ContextVerificationError("owning daemon is not ready")
    if daemon.workspaces is None:
        raise ContextVerificationError("daemon filesystem evidence is unavailable")
    workspace = next((item for item in daemon.workspaces if item.id == workspace_id), None)
    if workspace is None or runtime_id not in workspace.runtimes:
        raise ContextVerificationError("daemon does not own the selected runtime")
    if workspace.path is None or not Path(workspace.path).is_absolute():
        raise ContextVerificationError("daemon workspace has no absolute filesystem evidence")
    try:
        workspace_root = Path(workspace.path).resolve(strict=True)
    except OSError as exc:
        raise ContextVerificationError(
            "daemon workspace filesystem evidence is unavailable"
        ) from exc
    if not task_root.is_relative_to(workspace_root):
        raise ContextVerificationError("task directory is outside the daemon workspace")
    if not checkout.is_relative_to(task_root):
        raise ContextVerificationError("checkout is outside the verified task directory")


__all__ = ["MulticaOdooClient", "PrepareCommand"]
