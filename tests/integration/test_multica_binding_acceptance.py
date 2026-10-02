"""Integrated acceptance of the typed Multica-to-core handoff."""

from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from multica_py import ClientConfig, Issue, MulticaClient, Page, Project, ProjectStatus, TaskRun
from multica_py.models.issue_activity import TaskProjectResourceData
from multica_py.models.system import DaemonStatus, DaemonWorkspace, RepositoryCheckoutResult
from odcli_multica import (  # type: ignore[import-untyped]
    ContextRequest,
    MulticaOdooClient,
    PreparationRequest,
)
from odcli_multica.models import (  # type: ignore[import-untyped]
    MULTICA_PY_REVISION,
    MULTICA_PY_VERSION,
    MulticaCompatibility,
)

from odoo_instance_sdk import EnvironmentCheckoutOptions, EnvironmentDatabaseMode
from odoo_instance_sdk.commands.output import action_command
from odoo_instance_sdk.models import (
    DevelopmentEnvironment,
    EnvironmentCodeOwnership,
    EnvironmentState,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command


pytestmark = pytest.mark.integration


class _TypedCommand:
    def __init__(self, callback: Any) -> None:
        self._callback = callback

    def run(self) -> Any:
        return self._callback()


class _Repositories:
    def __init__(self, checkout: Path) -> None:
        self.checkout_path = checkout
        self.calls: list[tuple[str, str | None, bool]] = []

    def checkout_command(
        self,
        url: str,
        *,
        ref: str | None,
        fresh: bool,
        options: object | None = None,
    ) -> _TypedCommand:
        del options
        self.calls.append((url, ref, fresh))
        return _TypedCommand(lambda: RepositoryCheckoutResult(path=str(self.checkout_path)))

    def checkout(
        self,
        url: str,
        *,
        ref: str | None,
        fresh: bool,
        options: object | None = None,
    ) -> RepositoryCheckoutResult:
        return cast(
            "RepositoryCheckoutResult",
            self.checkout_command(url, ref=ref, fresh=fresh, options=options).run(),
        )


class _Daemon:
    def __init__(self, status: DaemonStatus) -> None:
        self.status_value = status
        self.status_calls = 0

    def status(self) -> DaemonStatus:
        self.status_calls += 1
        return self.status_value

    def status_command(self, *, options: object | None = None) -> _TypedCommand:
        del options
        return _TypedCommand(self.status)


class _Projects:
    def __init__(self, project: Project) -> None:
        self.project = project

    def get(self, project_id: str) -> Project:
        assert project_id == self.project.id
        return self.project


class _Issues:
    def __init__(self, issues: dict[str, Issue], runs: dict[str, TaskRun]) -> None:
        self.issues = issues
        self.runs_by_issue = runs

    def get(self, issue_id: str) -> Issue:
        return self.issues[issue_id]

    def runs(self, issue_id: str) -> Page[TaskRun]:
        return Page(items=(self.runs_by_issue[issue_id],), total=1)


@dataclass
class _Record:
    environment: DevelopmentEnvironment
    filestore: Path
    started: bool = False
    removed: bool = False


class _CoreEnvironments:
    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root
        self.fail_after_artifacts = False
        self.records: dict[tuple[str, str, str, str], _Record] = {}
        self.adoption_calls: list[tuple[Path, Path, EnvironmentCheckoutOptions]] = []
        self.restore_count = 0
        self.lifecycle: list[str] = []

    def adopt_command(
        self,
        project: Path,
        checkout: Path,
        *,
        options: EnvironmentCheckoutOptions,
    ) -> Command[DevelopmentEnvironment]:
        self.adoption_calls.append((project, checkout, options))
        key = (
            str(project),
            str(checkout),
            options.base_ref or "",
            options.remote_name or str(options.backup_id),
        )

        def adopt() -> DevelopmentEnvironment:
            existing = self.records.get(key)
            if existing is not None:
                return existing.environment
            self.restore_count += 1
            environment_id = uuid.uuid5(uuid.NAMESPACE_URL, "multica-acceptance-environment")
            artifact_root = self.artifact_root / str(environment_id)
            artifact_root.mkdir(parents=True)
            config = artifact_root / "odoo.conf"
            config.write_text("[options]\ndb_name = target-db\n", encoding="utf-8")
            filestore = artifact_root / "filestore"
            filestore.mkdir()
            if self.fail_after_artifacts:
                config.unlink()
                filestore.rmdir()
                artifact_root.rmdir()
                raise RuntimeError("disposable adoption failure after SDK artifact creation")
            environment = DevelopmentEnvironment(
                id=environment_id,
                name="native-task",
                repository_root=str(project),
                git_common_dir=str(project / ".git"),
                branch="main",
                base_ref=options.base_ref or "",
                worktree_path=str(checkout),
                generated_config_path=str(config),
                python_environment_path=str(artifact_root / "venv"),
                python_environment_owned=False,
                dependency_lock_path=str(artifact_root / "uv.lock"),
                http_interface="127.0.0.1",
                http_port=18069,
                db_mode=EnvironmentDatabaseMode.COPY,
                source_db_name="source-db",
                target_db_name="target-db",
                backup_id=uuid.uuid5(uuid.NAMESPACE_URL, "multica-acceptance-backup"),
                state=EnvironmentState.READY,
                created_at=datetime.now(UTC),
                project_id="project_core",
                checkout_repository_root=str(checkout),
                checkout_git_common_dir=str(checkout / ".git"),
                checkout_commit_sha="deadbeef",
                code_ownership=EnvironmentCodeOwnership.CALLER_OWNED,
                artifact_root=str(artifact_root),
            )
            self.records[key] = _Record(environment, filestore)
            return environment

        return cast("Command[DevelopmentEnvironment]", action_command("environment.adopt", adopt))

    def start(self, environment: DevelopmentEnvironment, *, cwd: Path) -> None:
        record = self._record(environment)
        assert cwd == Path(environment.worktree_path).parent
        record.started = True
        self.lifecycle.append("start")

    def status(self, environment: DevelopmentEnvironment) -> str:
        record = self._record(environment)
        self.lifecycle.append("status")
        return "running" if record.started and not record.removed else "stopped"

    def stop(self, environment: DevelopmentEnvironment) -> None:
        record = self._record(environment)
        record.started = False
        self.lifecycle.append("stop")

    def remove(self, environment: DevelopmentEnvironment) -> None:
        record = self._record(environment)
        assert not record.started
        record.removed = True
        self.lifecycle.append("remove")
        for path in (record.environment.generated_config_path, record.filestore):
            target = Path(path)
            if target.is_dir():
                target.rmdir()
            else:
                target.unlink(missing_ok=True)
        self.artifact_root.joinpath(str(environment.id)).rmdir()

    def _record(self, environment: DevelopmentEnvironment) -> _Record:
        return next(
            record for record in self.records.values() if record.environment.id == environment.id
        )


def _fake_client(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> tuple[MulticaOdooClient, _Repositories, _CoreEnvironments, Path]:
    task_root = root / "task"
    checkout = task_root / "checkout"
    checkout.mkdir(parents=True)
    (checkout / "README.md").write_text("caller-owned\n", encoding="utf-8")
    project = root / "core"
    (project / ".odcli").mkdir(parents=True)
    (project / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://example.test/repo.git\n', encoding="utf-8"
    )
    multica_project = Project(
        "project", "Project", ProjectStatus.in_progress, workspace_id="workspace"
    )
    issues = {}
    runs = {}
    for issue_id in ("issue-1", "issue-2"):
        issues[issue_id] = Issue(issue_id, issue_id, "in_progress", project_id="project")
        runs[issue_id] = TaskRun(
            f"run-{issue_id}",
            "running",
            issue_id=issue_id,
            project_id="project",
            workspace_id="workspace",
            runtime_id="runtime",
            work_dir=str(task_root),
            durable_work_dir=str(task_root),
            project_resources=(
                TaskProjectResourceData(
                    id="repository",
                    resource_type="github_repo",
                    resource_ref="https://example.test/repo.git",
                ),
            ),
        )
    repositories = _Repositories(checkout)
    daemon = _Daemon(
        DaemonStatus(
            status="ready",
            daemon_id="daemon",
            server_url="http://127.0.0.1:8765",
            cli_version="0.5.3",
            workspaces=(DaemonWorkspace(id="workspace", runtimes=("runtime",), path=str(root)),),
        )
    )
    multica = SimpleNamespace(
        config=ClientConfig(workspace_id="workspace", server_url="http://127.0.0.1:8765"),
        projects=_Projects(multica_project),
        issues=_Issues(issues, runs),
        daemon=daemon,
        repositories=repositories,
    )
    core_environments = _CoreEnvironments(root / "artifacts")
    core_environments.artifact_root.mkdir()
    core = SimpleNamespace(environments=core_environments)
    monkeypatch.setattr(
        "odcli_multica.client._observe_compatibility",
        lambda _client: MulticaCompatibility(
            MULTICA_PY_VERSION,
            MULTICA_PY_REVISION,
            "0.5.3",
            typed_checkout=True,
            typed_daemon_status=True,
            observed=True,
        ),
    )
    return (
        MulticaOdooClient(cast("Any", core), cast("MulticaClient", multica)),
        repositories,
        core_environments,
        project,
    )


@pytest.mark.integration
def test_fake_boundaries_cover_checkout_context_adoption_and_owned_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, repositories, environments, project = _fake_client(monkeypatch, tmp_path)
    native = client.checkout("https://example.test/repo.git", ref="main")
    assert native.path.endswith("/task/checkout")
    assert repositories.calls == [("https://example.test/repo.git", "main", False)]

    prepared = []
    for issue_id in ("issue-1", "issue-2"):
        context = ContextRequest(
            checkout_path=Path(native.path),
            core_project=project,
            multica_project="project",
            issue=issue_id,
            run=f"run-{issue_id}",
            repository_url="https://example.test/repo.git",
        )
        prepared.append(
            client.prepare(
                PreparationRequest(context=context, base_ref="main", remote_name="disposable")
            )
        )

    assert prepared[0].id == prepared[1].id
    assert len(environments.records) == 1
    assert environments.restore_count == 1
    assert len(environments.adoption_calls) == 2
    assert all(
        options.db_mode is EnvironmentDatabaseMode.COPY
        for _, _, options in environments.adoption_calls
    )
    assert all(options.remote_name == "disposable" for _, _, options in environments.adoption_calls)

    environment = prepared[0]
    task_root = Path(environment.worktree_path).parent
    environments.start(environment, cwd=task_root)
    assert environments.status(environment) == "running"
    environments.stop(environment)
    environments.remove(environment)

    assert environments.lifecycle == ["start", "status", "stop", "remove"]
    assert Path(native.path).is_dir()
    assert (Path(native.path) / "README.md").read_text(encoding="utf-8") == "caller-owned\n"
    assert not list(task_root.glob("**/*binding*"))
    assert not list(task_root.glob("**/*project-link*"))
    assert not list(tmp_path.joinpath("artifacts").glob("**/*"))


@pytest.mark.integration
def test_failed_adoption_cleans_sdk_artifacts_and_preserves_caller_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, _, environments, project = _fake_client(monkeypatch, tmp_path)
    environments.fail_after_artifacts = True
    checkout = tmp_path / "task" / "checkout"
    sentinel = checkout / "README.md"

    request = ContextRequest(
        checkout_path=checkout,
        core_project=project,
        multica_project="project",
        issue="issue-1",
        run="run-issue-1",
        repository_url="https://example.test/repo.git",
    )
    with pytest.raises(RuntimeError, match="after SDK artifact creation"):
        client.prepare(
            PreparationRequest(context=request, base_ref="main", remote_name="disposable")
        )

    assert sentinel.read_text(encoding="utf-8") == "caller-owned\n"
    assert checkout.is_dir()
    assert not list(tmp_path.joinpath("artifacts").glob("**/*"))


def _required_live(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"approved Multica/Odoo fixture requires {name}")
    return value


def _write_live_evidence(
    root: Path, *, checkout: Path, environment: DevelopmentEnvironment
) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    evidence = root / "multica-binding-acceptance.json"
    evidence.write_text(
        json.dumps(
            {
                "multica_py_version": MULTICA_PY_VERSION,
                "multica_py_revision": MULTICA_PY_REVISION,
                "checkout_exists": checkout.is_dir(),
                "environment_state": environment.state.value,
                "db_mode": environment.db_mode.value,
                "code_ownership": environment.code_ownership.value,
                "artifact_root_present": bool(environment.artifact_root),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    evidence.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.real_odoo
def test_approved_native_daemon_odoo_fixture(tmp_path: Path) -> None:
    """Run the live flow only with an explicitly disposable approved fixture."""
    if os.environ.get("ODCLI_MULTICA_LIVE_ENABLE") != "1":
        pytest.skip("set ODCLI_MULTICA_LIVE_ENABLE=1 for the disposable live fixture")
    if os.environ.get("ODCLI_MULTICA_APPROVED_FIXTURE") != "disposable-native-daemon-odoo":
        pytest.skip("live acceptance requires the approved disposable fixture sentinel")

    from multica_py import MulticaClient

    from odoo_instance_sdk import OdooClient, OdooClientConfig

    executable = _required_live("ODCLI_MULTICA_EXECUTABLE")
    repository_url = _required_live("ODCLI_MULTICA_REPOSITORY_URL")
    core_project = Path(_required_live("ODCLI_MULTICA_CORE_PROJECT")).resolve()
    multica_project = _required_live("ODCLI_MULTICA_PROJECT")
    issue = _required_live("ODCLI_MULTICA_ISSUE")
    run = _required_live("ODCLI_MULTICA_RUN")
    task_root = Path(_required_live("ODCLI_MULTICA_TASK_ROOT")).resolve()
    base_ref = _required_live("ODCLI_MULTICA_BASE_REF")
    odoo_executable = _required_live("ODCLI_MULTICA_ODOO_EXECUTABLE")
    evidence_root = Path(_required_live("ODCLI_MULTICA_EVIDENCE_ROOT")).resolve()
    ref = os.environ.get("ODCLI_MULTICA_REF")
    remote_name = os.environ.get("ODCLI_MULTICA_REMOTE_NAME")
    backup_id = os.environ.get("ODCLI_MULTICA_BACKUP_ID")
    if (remote_name is None) == (backup_id is None):
        pytest.fail(
            "live fixture must provide exactly one of ODCLI_MULTICA_REMOTE_NAME or ODCLI_MULTICA_BACKUP_ID"
        )

    multica = MulticaClient(
        ClientConfig(
            executable=executable,
            server_url=_required_live("ODCLI_MULTICA_SERVER_URL"),
            workspace_id=_required_live("ODCLI_MULTICA_WORKSPACE_ID"),
            cwd=task_root,
        )
    )
    core = OdooClient(config=OdooClientConfig(executable=odoo_executable))
    bridge = MulticaOdooClient(core, multica)
    checkout = bridge.checkout(repository_url, ref=ref)
    environment: DevelopmentEnvironment | None = None
    process = None
    try:
        request = ContextRequest(
            checkout_path=Path(checkout.path),
            core_project=core_project,
            multica_project=multica_project,
            issue=issue,
            run=run,
            repository_url=repository_url,
        )
        preparation = PreparationRequest(
            context=request,
            base_ref=base_ref,
            remote_name=remote_name,
            backup_id=backup_id,
        )
        environment = bridge.prepare(preparation)
        instance = core.instance.from_environment(environment)
        process = instance.start()
        instance.wait_ready(process, timeout=180.0)
        assert instance.status(process).state == "running"
        instance.stop(process)
        _write_live_evidence(evidence_root, checkout=Path(checkout.path), environment=environment)
    finally:
        if process is not None and core.get_handle(process.id) is not None:
            instance.stop(process)
        if environment is not None:
            core.environments.remove(environment)
        assert Path(checkout.path).is_dir(), "native checkout must outlive owned cleanup"
        assert not list(task_root.glob("**/*binding*"))
        assert not list(task_root.glob("**/*project-link*"))
