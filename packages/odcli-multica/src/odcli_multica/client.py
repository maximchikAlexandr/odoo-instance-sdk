"""Thin typed composition of native Multica and core Odoo operations."""

from __future__ import annotations

import configparser
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar, cast
from urllib.parse import urlsplit

from multica_py.models.project_resources import GithubRepoResourceRef, ProjectResourceRecord

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
    ProjectConfig,
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
        self._compatibility = _observe_compatibility(multica_client)

    def _require_contract(self) -> None:
        identity = self._compatibility
        if not identity.observed:
            raise ContextVerificationError("typed Multica compatibility evidence is unavailable")
        if not identity.typed_checkout or not identity.typed_daemon_status:
            raise ContextVerificationError("required typed Multica capability is unavailable")
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
        if run.project_id is not None and run.project_id != project.id:
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
        core_repository_url = _verify_core_project(request.core_project)
        try:
            resources = self.multica.projects.resources.list(project.id)
        except Exception as exc:
            raise ContextVerificationError("project repository evidence is unavailable") from exc
        repository_url = _repository_url(
            resources,
            project.id,
            core_repository_url,
            request.core_repository_url or request.repository_url,
        )
        _verify_run_snapshot(run, project.id, repository_url)
        daemon = self.multica.daemon.status()
        daemon_id = _required(daemon.daemon_id, "daemon identity")
        _verify_daemon(
            daemon,
            workspace_id,
            runtime_id,
            task_root,
            checkout,
            expected_server_url=self.multica.config.server_url,
        )
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


def _observe_compatibility(multica: MulticaClient) -> MulticaCompatibility:
    """Observe the public package, capability, and daemon contract once."""
    try:
        daemon = multica.daemon.status()
    except AttributeError as exc:
        raise ContextVerificationError("typed daemon status capability is unavailable") from exc
    direct_url_text = metadata.distribution("multica-py").read_text("direct_url.json")
    if direct_url_text is None:
        raise ContextVerificationError("multica-py revision evidence is unavailable")
    direct_url = json.loads(direct_url_text)
    vcs_info = direct_url.get("vcs_info") if isinstance(direct_url, dict) else None
    revision = vcs_info.get("commit_id") if isinstance(vcs_info, dict) else None
    if not isinstance(revision, str):
        raise ContextVerificationError("multica-py revision evidence is unavailable")
    repositories = getattr(multica, "repositories", None)
    daemon_resource = getattr(multica, "daemon", None)
    return MulticaCompatibility(
        package_version=metadata.version("multica-py"),
        package_revision=revision,
        native_cli_version=daemon.cli_version or "",
        typed_checkout=callable(getattr(repositories, "checkout_command", None))
        and callable(getattr(repositories, "checkout", None)),
        typed_daemon_status=callable(getattr(daemon_resource, "status_command", None))
        and callable(getattr(daemon_resource, "status", None)),
        observed=True,
    )


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


def _verify_core_project(path: Path) -> str:
    if not path.is_dir():
        raise ContextVerificationError("core project path is not a directory")
    try:
        project = ProjectConfig.load(path)
    except Exception as exc:
        raise ContextVerificationError("core project repository identity is unavailable") from exc
    if project.repository_root.resolve() != path.resolve():
        raise ContextVerificationError("core project repository identity conflicts with its path")
    try:
        config_path = _git_config_path(path)
        parser = configparser.ConfigParser(interpolation=None)
        if not config_path.is_file():
            raise ContextVerificationError("core project Git identity is unavailable")
        parser.read(config_path, encoding="utf-8")
        origin = parser.get('remote "origin"', "url", fallback="").strip()
    except (OSError, configparser.Error) as exc:
        raise ContextVerificationError("core project Git identity is unavailable") from exc
    if not origin:
        raise ContextVerificationError("core project Git identity is unavailable")
    return origin


def _git_config_path(path: Path) -> Path:
    git_marker = path / ".git"
    if git_marker.is_dir():
        return git_marker / "config"
    if not git_marker.is_file():
        raise ContextVerificationError("core project Git identity is unavailable")
    marker = git_marker.read_text(encoding="utf-8").strip()
    if not marker.lower().startswith("gitdir:"):
        raise ContextVerificationError("core project Git identity is unavailable")
    git_dir = (path / marker.split(":", 1)[1].strip()).resolve()
    common_dir = git_dir / "commondir"
    if common_dir.is_file():
        git_dir = (git_dir / common_dir.read_text(encoding="utf-8").strip()).resolve()
    return git_dir / "config"


def _repository_url(
    page: Page[ProjectResourceRecord], project_id: str, core_url: str, selected: str | None
) -> str:
    if page.has_more or page.next_cursor is not None:
        raise ContextVerificationError("project repository evidence is paginated and incomplete")
    if page.offset not in (None, 0) or page.total is None or page.total != len(page.items):
        raise ContextVerificationError("project repository evidence is incomplete")
    values = tuple(
        resource.resource_ref.url
        for resource in page.items
        if resource.project_id == project_id
        and resource.resource_type == "github_repo"
        and isinstance(resource.resource_ref, GithubRepoResourceRef)
    )
    if len(values) != 1:
        raise ContextVerificationError("repository identity is absent or ambiguous")
    native = values[0]
    if _normalize_url(core_url) != _normalize_url(native):
        raise ContextVerificationError("core repository does not match the native repository")
    if selected is not None and _normalize_url(selected) != _normalize_url(core_url):
        raise ContextVerificationError("selected repository does not match the core repository")
    return native


def _verify_run_snapshot(run: TaskRun, project_id: str, repository_url: str) -> None:
    if run.project_id is not None and run.project_id != project_id:
        raise ContextVerificationError("run project identity conflicts with the request")
    if not run.project_resources:
        return
    values = tuple(
        resource.resource_ref
        for resource in run.project_resources
        if resource.resource_type in {"github_repo", "git_repository", "repository"}
        and isinstance(resource.resource_ref, str)
    )
    if len(values) != 1 or _normalize_url(values[0]) != _normalize_url(repository_url):
        raise ContextVerificationError("run repository snapshot conflicts with project resources")


def _normalize_url(value: str) -> str:
    raw = value.strip()
    if "://" in raw:
        parsed = urlsplit(raw)
        return f"{parsed.netloc.lower()}/{parsed.path.strip('/').removesuffix('.git').lower()}"
    if raw.startswith("git@") and ":" in raw:
        authority, path = raw.split(":", 1)
        return (
            f"{authority.split('@', 1)[1].lower()}/{path.strip('/').removesuffix('.git').lower()}"
        )
    return raw.rstrip("/").removesuffix(".git").lower()


def _verify_daemon(
    daemon: DaemonStatus,
    workspace_id: str,
    runtime_id: str,
    task_root: Path,
    checkout: Path,
    *,
    expected_server_url: str | None,
) -> None:
    _verify_daemon_identity(daemon, expected_server_url)
    _verify_daemon_filesystem(daemon, workspace_id, runtime_id, task_root, checkout)


def _verify_daemon_identity(daemon: DaemonStatus, expected_server_url: str | None) -> None:
    if daemon.status.lower() not in {"ready", "running", "active"}:
        raise ContextVerificationError("owning daemon is not ready")
    if daemon.cli_version is None:
        raise ContextVerificationError("daemon CLI compatibility evidence is unavailable")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", daemon.cli_version)
    if match is None or tuple(int(part) for part in match.groups()) < (0, 5, 3):
        raise ContextVerificationError("native Multica CLI is below the supported floor")
    if daemon.server_url is None or not daemon.server_url.strip():
        raise ContextVerificationError("daemon server identity is unavailable")
    if expected_server_url is not None and _normalize_url(daemon.server_url) != _normalize_url(
        expected_server_url
    ):
        raise ContextVerificationError("daemon server identity conflicts with the client scope")


def _verify_daemon_filesystem(
    daemon: DaemonStatus,
    workspace_id: str,
    runtime_id: str,
    task_root: Path,
    checkout: Path,
) -> None:
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
