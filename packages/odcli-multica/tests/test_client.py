from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Generic, TypeVar, cast

import msgspec
import pytest
from multica_py import (
    ClientConfig,
    CommandCancelledError,
    Issue,
    MulticaClient,
    Page,
    Project,
    ProjectStatus,
    TaskRun,
    UnknownCommandError,
)
from multica_py.models.issue_activity import TaskProjectResourceData
from multica_py.models.system import DaemonStatus, DaemonWorkspace, RepositoryCheckoutResult
from odcli_multica import ContextRequest, MulticaOdooClient
from odcli_multica.models import (
    MULTICA_PY_REVISION,
    MULTICA_PY_VERSION,
    ContextVerificationError,
    MulticaCompatibility,
    PreparationRequest,
)

from odoo_instance_sdk import EnvironmentCheckoutOptions, OdooClient
from odoo_instance_sdk.commands.output import action_command
from odoo_instance_sdk.execution import Command
from odoo_instance_sdk.resources.environment import DevelopmentEnvironment

if TYPE_CHECKING:
    from multica_py.config import OperationOptions

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
        self.checkout_error: BaseException | None = None

    def checkout_command(
        self, _url: str, *, ref: str | None, fresh: bool, options: OperationOptions | None
    ) -> _Command[RepositoryCheckoutResult]:
        self.fresh_values.append(fresh)
        return _Command(RepositoryCheckoutResult(path="/task/checkout"))

    def checkout(
        self, _url: str, *, ref: str | None, fresh: bool, options: OperationOptions | None
    ) -> RepositoryCheckoutResult:
        self.fresh_values.append(fresh)
        if self.checkout_error is not None:
            raise self.checkout_error
        return RepositoryCheckoutResult(path="/task/checkout")


class _Environments:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, EnvironmentCheckoutOptions]] = []

    def adopt_command(
        self,
        project: Path,
        checkout: Path,
        *,
        options: EnvironmentCheckoutOptions,
    ) -> Command[DevelopmentEnvironment]:
        self.calls.append((project, checkout, options))
        return cast(
            "Command[DevelopmentEnvironment]",
            action_command(
                "test.adopt",
                lambda: cast("DevelopmentEnvironment", SimpleNamespace()),
                description="test adoption",
            ),
        )


def _fixture(
    tmp_path: Path,
) -> tuple[MulticaOdooClient, ContextRequest, _Repositories, _Environments]:
    task_root = tmp_path / "task"
    checkout = task_root / "checkout"
    checkout.mkdir(parents=True)
    project_root = tmp_path / "core"
    (project_root / ".odcli").mkdir(parents=True)
    (project_root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    multica_project = Project(
        "project", "Project", ProjectStatus.in_progress, workspace_id="workspace"
    )
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
        server_url="http://127.0.0.1:8765",
        cli_version="0.5.3",
        workspaces=(DaemonWorkspace(id="workspace", runtimes=("runtime",), path=str(tmp_path)),),
    )
    repositories = _Repositories()
    environments = _Environments()
    multica = SimpleNamespace(
        config=ClientConfig(workspace_id="workspace", server_url="http://127.0.0.1:8765"),
        projects=_Projects(multica_project),
        issues=_Issues(issue, Page(items=(run,), total=1)),
        daemon=_Daemon(daemon),
        repositories=repositories,
    )
    core = cast("OdooClient", SimpleNamespace(environments=environments))
    request = ContextRequest(
        checkout_path=checkout,
        core_project=project_root,
        multica_project="project",
        issue="issue",
        run="run",
        repository_url="https://example.test/repo",
    )
    compatibility = MulticaCompatibility(
        package_version=MULTICA_PY_VERSION,
        package_revision=MULTICA_PY_REVISION,
        native_cli_version="0.5.3",
        typed_checkout=True,
        typed_daemon_status=True,
        observed=True,
    )
    return (
        MulticaOdooClient(core, cast("MulticaClient", multica), compatibility=compatibility),
        request,
        repositories,
        environments,
    )


def test_context_verifies_typed_membership_and_containment(tmp_path: Path) -> None:
    client, request, _, _ = _fixture(tmp_path)

    result = client.context(request)

    assert result.workspace_id == "workspace"
    assert result.runtime_id == "runtime"
    assert result.checkout_path == str((tmp_path / "task" / "checkout").resolve())


def test_context_rejects_incomplete_run_page(tmp_path: Path) -> None:
    client, request, _, _ = _fixture(tmp_path)
    fake_multica = cast("_FakeMultica", client.multica)
    fake_multica.issues.runs_page = Page(items=(), total=2, has_more=True)

    with pytest.raises(ContextVerificationError, match="paginated"):
        client.context(request)


def test_native_checkout_never_forces_fresh(tmp_path: Path) -> None:
    client, _, repositories, _ = _fixture(tmp_path)

    result = client.checkout("https://example.test/repo", ref="main")

    assert result.path == "/task/checkout"
    assert repositories.fresh_values == [False]


def test_context_rejects_forwarded_or_missing_filesystem_evidence(tmp_path: Path) -> None:
    client, request, _, _ = _fixture(tmp_path)
    fake_multica = cast("_FakeMultica", client.multica)
    fake_multica.daemon.status_value = DaemonStatus(
        status="ready", daemon_id="daemon", server_url="http://127.0.0.1:8765", cli_version="0.5.3"
    )

    with pytest.raises(ContextVerificationError, match="filesystem evidence"):
        client.context(request)


class _FakeMultica:
    config: ClientConfig
    issues: _Issues
    daemon: _Daemon


def test_context_rejects_repository_mismatch_and_ambiguity(tmp_path: Path) -> None:
    client, request, _, _ = _fixture(tmp_path)

    with pytest.raises(ContextVerificationError, match="does not match"):
        client.context(msgspec.structs.replace(request, repository_url="https://other.test/repo"))

    fake_multica = cast("_FakeMultica", client.multica)
    run = fake_multica.issues.runs_page.items[0]
    fake_multica.issues.runs_page = Page(
        items=(msgspec.structs.replace(run, project_resources=run.project_resources * 2),),
        total=1,
    )
    with pytest.raises(ContextVerificationError, match="ambiguous"):
        client.context(request)


def test_context_rejects_missing_core_identity_and_manifest(tmp_path: Path) -> None:
    client, request, _, _ = _fixture(tmp_path)
    missing = msgspec.structs.replace(request, repository_url=None)
    with pytest.raises(ContextVerificationError, match="core repository identity"):
        client.context(missing)

    request.core_project.joinpath(".odcli", "project.toml").unlink()
    with pytest.raises(ContextVerificationError, match="core project repository identity"):
        client.context(request)


def test_context_rejects_scope_runtime_server_and_boundary_conflicts(tmp_path: Path) -> None:
    client, request, _, _ = _fixture(tmp_path)
    with pytest.raises(ContextVerificationError, match="workspace"):
        client.context(msgspec.structs.replace(request, workspace_id="other"))
    with pytest.raises(ContextVerificationError, match="runtime"):
        client.context(msgspec.structs.replace(request, runtime_id="other"))

    fake_multica = cast("_FakeMultica", client.multica)
    fake_multica.config = ClientConfig(workspace_id="workspace", server_url="https://other.test")
    with pytest.raises(ContextVerificationError, match="server identity"):
        client.context(request)

    fake_multica.config = ClientConfig(workspace_id="workspace", server_url="http://127.0.0.1:8765")
    task_root = tmp_path / "task-other"
    checkout = task_root / "checkout"
    checkout.mkdir(parents=True)
    boundary_request = msgspec.structs.replace(request, checkout_path=checkout)
    with pytest.raises(ContextVerificationError, match="contained"):
        client.context(boundary_request)


def test_compatibility_is_required_and_daemon_fields_are_observed(tmp_path: Path) -> None:
    client, request, _, _ = _fixture(tmp_path)
    client.compatibility = None
    with pytest.raises(ContextVerificationError, match="compatibility evidence"):
        client.context(request)

    client.compatibility = MulticaCompatibility(
        package_version=MULTICA_PY_VERSION,
        package_revision=MULTICA_PY_REVISION,
        native_cli_version="0.5.3",
        typed_checkout=True,
        typed_daemon_status=True,
        observed=True,
    )
    fake_multica = cast("_FakeMultica", client.multica)
    fake_multica.daemon.status_value = DaemonStatus(
        status="ready",
        daemon_id="daemon",
        server_url="http://127.0.0.1:8765",
        cli_version="0.5.2",
    )
    with pytest.raises(ContextVerificationError, match="below"):
        client.context(request)


@pytest.mark.parametrize(
    "error", [CommandCancelledError("cancelled"), UnknownCommandError("unknown")]
)
def test_checkout_preserves_cancellation_and_unknown_outcomes(
    tmp_path: Path, error: BaseException
) -> None:
    client, _, repositories, _ = _fixture(tmp_path)

    repositories.checkout_error = error
    with pytest.raises(type(error)):
        client.checkout("https://example.test/repo")


def test_prepare_delegates_one_selector_and_captures_recovery_command(tmp_path: Path) -> None:
    client, request, _, environments = _fixture(tmp_path)
    prepared = client.prepare_command(
        PreparationRequest(context=request, base_ref="main", remote_name="origin")
    )
    assert len(environments.calls) == 1
    assert environments.calls[0][2].base_ref == "main"
    prepared.run()
    prepared.run()
    assert len(environments.calls) == 1

    with pytest.raises(ContextVerificationError, match="exactly one"):
        client.prepare_command(PreparationRequest(context=request, base_ref="main"))
    with pytest.raises(ContextVerificationError, match="exactly one"):
        client.prepare_command(
            PreparationRequest(
                context=request, base_ref="main", remote_name="origin", backup_id="b"
            )
        )
