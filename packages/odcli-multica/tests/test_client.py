from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Generic, TypeVar, cast

import pytest
from multica_py import Issue, Page, Project, TaskRun
from multica_py.models.issue_activity import TaskProjectResourceData
from multica_py.models.system import DaemonStatus, DaemonWorkspace, RepositoryCheckoutResult
from odcli_multica import ContextRequest, MulticaOdooClient
from odcli_multica.models import ContextVerificationError

from odoo_instance_sdk import OdooClient

T = TypeVar("T")


@dataclass
class _Command(Generic[T]):
    value: T

    def run(self) -> T:
        return self.value


class _Issues:
    def __init__(self, issue: Issue, runs: Page[TaskRun]) -> None:
        self.issue = issue
        self.runs_page = runs

    def get(self, _issue_id: str) -> Issue:
        return self.issue

    def runs(self, _issue_id: str) -> Page[TaskRun]:
        return self.runs_page


class _Projects:
    def __init__(self, project: Project) -> None:
        self.project = project

    def get(self, _project_id: str) -> Project:
        return self.project


class _Daemon:
    def __init__(self, status: DaemonStatus) -> None:
        self.status_value = status

    def status(self) -> DaemonStatus:
        return self.status_value


class _Repositories:
    def __init__(self) -> None:
        self.fresh_values: list[bool] = []

    def checkout_command(
        self, _url: str, *, ref: str | None, fresh: bool, options: object
    ) -> _Command[RepositoryCheckoutResult]:
        self.fresh_values.append(fresh)
        return _Command(RepositoryCheckoutResult(path="/task/checkout"))

    def checkout(
        self, _url: str, *, ref: str | None, fresh: bool, options: object
    ) -> RepositoryCheckoutResult:
        self.fresh_values.append(fresh)
        return RepositoryCheckoutResult(path="/task/checkout")


def _fixture(tmp_path: Path) -> tuple[MulticaOdooClient, ContextRequest, _Repositories]:
    task_root = tmp_path / "task"
    checkout = task_root / "checkout"
    checkout.mkdir(parents=True)
    project_root = tmp_path / "core"
    project_root.mkdir()
    multica_project = Project("project", "Project", "active", workspace_id="workspace")
    issue = Issue("issue", "Issue", "in_progress", project_id="project")
    run = TaskRun(
        "run",
        "running",
        issue_id="issue",
        project_id="project",
        workspace_id="workspace",
        runtime_id="runtime",
        work_dir=str(task_root),
        durable_work_dir=str(task_root),
        project_resources=(
            TaskProjectResourceData(
                id="repository",
                resource_type="github_repo",
                resource_ref="https://example.test/repo",
            ),
        ),
    )
    daemon = DaemonStatus(
        status="ready",
        daemon_id="daemon",
        workspaces=(DaemonWorkspace(id="workspace", runtimes=("runtime",), path=str(tmp_path)),),
    )
    repositories = _Repositories()
    multica = SimpleNamespace(
        config=SimpleNamespace(workspace_id="workspace"),
        projects=_Projects(multica_project),
        issues=_Issues(issue, Page(items=(run,), total=1)),
        daemon=_Daemon(daemon),
        repositories=repositories,
    )
    core = cast("OdooClient", SimpleNamespace())
    request = ContextRequest(
        checkout_path=checkout,
        core_project=project_root,
        multica_project="project",
        issue="issue",
        run="run",
        repository_url="https://example.test/repo",
    )
    return MulticaOdooClient(core, cast("object", multica)), request, repositories


def test_context_verifies_typed_membership_and_containment(tmp_path: Path) -> None:
    client, request, _ = _fixture(tmp_path)

    result = client.context(request)

    assert result.workspace_id == "workspace"
    assert result.runtime_id == "runtime"
    assert result.checkout_path == str((tmp_path / "task" / "checkout").resolve())


def test_context_rejects_incomplete_run_page(tmp_path: Path) -> None:
    client, request, _ = _fixture(tmp_path)
    client.multica.issues.runs_page = Page(items=(), total=2, has_more=True)

    with pytest.raises(ContextVerificationError, match="paginated"):
        client.context(request)


def test_native_checkout_never_forces_fresh(tmp_path: Path) -> None:
    client, _, repositories = _fixture(tmp_path)

    result = client.checkout("https://example.test/repo", ref="main")

    assert result.path == "/task/checkout"
    assert repositories.fresh_values == [False]


def test_context_rejects_forwarded_or_missing_filesystem_evidence(tmp_path: Path) -> None:
    client, request, _ = _fixture(tmp_path)
    client.multica.daemon.status_value = DaemonStatus(status="ready", daemon_id="daemon")

    with pytest.raises(ContextVerificationError, match="filesystem evidence"):
        client.context(request)
