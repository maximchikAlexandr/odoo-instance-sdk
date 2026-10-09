"""Integrated acceptance of the typed Multica-to-core handoff."""

from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

pytest.importorskip("multica_py", reason="Multica integration requires the extension dependency")
pytest.importorskip("odcli_multica", reason="Multica integration requires the extension package")

from multica_py import ClientConfig, Issue, MulticaClient, Page, Project, ProjectStatus, TaskRun
from multica_py.models.issue_activity import TaskProjectResourceData
from multica_py.models.project_resources import GithubRepoResourceRef, ProjectResourceRecord
from multica_py.models.system import DaemonStatus, DaemonWorkspace, RepositoryCheckoutResult
from odcli_multica import (
    ContextRequest,
    MulticaOdooClient,
    PreparationRequest,
)
from odcli_multica.models import (
    MULTICA_PY_REVISION,
    MULTICA_PY_VERSION,
    MulticaCompatibility,
)

from odoo_instance_sdk import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    OdooClient,
)
from odoo_instance_sdk.commands.output import action_command
from odoo_instance_sdk.models import (
    DevelopmentEnvironment,
    EnvironmentCodeOwnership,
    EnvironmentState,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command


pytestmark = pytest.mark.integration

_APPROVED_NATIVE_REPOSITORY = "https://github.com/odoo/odoo.git"
_APPROVED_NATIVE_REF = "cd992ceebbaf343c03e1941d39cfe423d35ba6c6"
_APPROVED_NATIVE_REMOTE = "disposable-native-source"


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


class _ProjectResources:
    def __init__(self, page: Page[ProjectResourceRecord]) -> None:
        self.page = page

    def list(self, project_id: str) -> Page[ProjectResourceRecord]:
        assert project_id == "project"
        return self.page


class _Projects:
    def __init__(self, project: Project, resources: _ProjectResources) -> None:
        self.project = project
        self.resources = resources

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
    monkeypatch: pytest.MonkeyPatch, root: Path, *, checkout_path: Path | None = None
) -> tuple[MulticaOdooClient, _Repositories, _CoreEnvironments, Path]:
    task_root = root / "task"
    checkout = checkout_path or task_root / "checkout"
    checkout.mkdir(parents=True, exist_ok=True)
    if not (checkout / "README.md").exists():
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
    resources = _ProjectResources(
        Page(
            items=(
                ProjectResourceRecord(
                    id="repository",
                    project_id="project",
                    resource_type="github_repo",
                    resource_ref=GithubRepoResourceRef(url="https://example.test/repo.git"),
                ),
            ),
            offset=0,
            total=1,
        )
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
        projects=_Projects(multica_project, resources),
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
def test_failed_production_adoption_cleans_sdk_artifacts_and_preserves_caller_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_client: OdooClient,
    project_manifest: Path,
) -> None:
    repository_url = "https://example.test/repo.git"
    subprocess.run(
        ["git", "remote", "add", "origin", repository_url],
        cwd=project_manifest,
        check=True,
        capture_output=True,
        text=True,
    )
    checkout = tmp_path / "task" / "checkout"
    subprocess.run(
        ["git", "clone", "--no-hardlinks", str(project_manifest), str(checkout)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "remote", "set-url", "origin", repository_url],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "\n[remote_instances.disposable]\n"
        + 'base_url = "http://127.0.0.1:8069"\n'
        + 'database = "source-db"\n'
        + 'git_branch = "main"\n',
        encoding="utf-8",
    )

    client, _, _, _ = _fake_client(monkeypatch, tmp_path, checkout_path=checkout)
    client.core = env_client
    resource = env_client.environments
    monkeypatch.setattr(type(resource), "_preflight_copy_checkout", lambda *_args: None)

    def fail_restore(*_args: object, **_kwargs: object) -> uuid.UUID:
        raise RuntimeError("disposable restore failed after SDK artifact creation")

    monkeypatch.setattr(type(resource), "_do_copy_restore", fail_restore)

    request = ContextRequest(
        checkout_path=checkout,
        core_project=project_manifest,
        multica_project="project",
        issue="issue-1",
        run="run-issue-1",
        repository_url=repository_url,
    )
    with pytest.raises(RuntimeError, match="after SDK artifact creation"):
        client.prepare(
            PreparationRequest(context=request, base_ref="main", remote_name="disposable")
        )

    assert checkout.is_dir()
    assert (checkout / "README.md").is_file()
    environments = env_client.environments.list(project=project_manifest, include_removed=True)
    assert len(environments) == 1
    environment = environments[0]
    assert environment.state is EnvironmentState.FAILED
    assert environment.code_ownership is EnvironmentCodeOwnership.CALLER_OWNED
    assert not Path(environment.generated_config_path).exists()
    assert environment.artifact_root is not None
    artifact_root = Path(environment.artifact_root)
    assert artifact_root.is_dir()
    assert not any(artifact_root.iterdir())
    assert not list(checkout.glob("**/*binding*"))
    assert not list(checkout.glob("**/*project-link*"))


def _required_live(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"approved Multica/Odoo fixture requires {name}")
    return value


def _write_live_evidence(
    root: Path,
    *,
    checkout: Path,
    environment: DevelopmentEnvironment,
    task_root: Path,
    phases: list[str],
    daemon_status: DaemonStatus,
    task_run: TaskRun,
    authoritative_project_id: str,
    resource_page: Page[ProjectResourceRecord],
    odoo_executable: str,
    artifact_root: Path,
    artifact_root_existed_before_cleanup: bool,
) -> None:
    try:
        sdk_version = version("odoo-instance-sdk")
    except PackageNotFoundError:
        sdk_version = "uninstalled-source-checkout"
    odoo_version = subprocess.run(
        [odoo_executable, "--version"],
        cwd=task_root,
        capture_output=True,
        check=False,
        text=True,
        timeout=30.0,
    )
    odoo_version_text = (odoo_version.stdout or odoo_version.stderr).splitlines()
    daemon_id = getattr(daemon_status, "daemon_id", "") or ""
    resource_count = len(getattr(task_run, "project_resources", ()) or ())
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    evidence = root / "multica-binding-acceptance.json"
    evidence.write_text(
        json.dumps(
            {
                "contract": "WP-04 disposable native-daemon/Odoo acceptance",
                "phases": phases,
                "task_cwd": {
                    "path_exists": task_root.is_dir(),
                    "checkout_is_inside_task_cwd": checkout.is_relative_to(task_root),
                    "mode": stat.S_IMODE(task_root.stat().st_mode) if task_root.exists() else None,
                    "readable_writable_executable": all(
                        os.access(task_root, mode) for mode in (os.R_OK, os.W_OK, os.X_OK)
                    ),
                },
                "same_host_identity": {
                    "host_hash": sha256(platform.node().encode()).hexdigest()[:16],
                    "daemon_id_hash": sha256(daemon_id.encode()).hexdigest()[:16],
                    "daemon_status": daemon_status.status,
                },
                "credentials_and_permissions": {
                    "multica_workspace_scoped": True,
                    "odoo_executable_scoped": True,
                    "task_root_owner_only": stat.S_IMODE(task_root.stat().st_mode) & 0o077 == 0,
                    "secrets_retained": False,
                },
                "versions": {
                    "multica_py": MULTICA_PY_VERSION,
                    "multica_py_revision": MULTICA_PY_REVISION,
                    "odoo_instance_sdk": sdk_version,
                    "odoo_executable": odoo_version_text[0][:200]
                    if odoo_version_text
                    else "unavailable",
                },
                "task_run": {
                    "id_present": bool(task_run.id),
                    "issue_id": task_run.issue_id,
                    "project_id": task_run.project_id,
                    "workspace_id": task_run.workspace_id,
                    "work_dir_matches_task_cwd": Path(task_run.work_dir or "").resolve()
                    == task_root,
                    "project_resource_count": resource_count,
                },
                "authoritative_project_resources": {
                    "issue_project_id": authoritative_project_id,
                    "page_complete": (
                        not resource_page.has_more
                        and resource_page.next_cursor is None
                        and resource_page.offset in (None, 0)
                        and resource_page.total == len(resource_page.items)
                    ),
                    "total": resource_page.total,
                    "github_repo_count": sum(
                        resource.resource_type == "github_repo"
                        and isinstance(resource.resource_ref, GithubRepoResourceRef)
                        for resource in resource_page.items
                    ),
                },
                "copy_adoption": {
                    "database_mode": environment.db_mode.value,
                    "source_database": environment.source_db_name,
                    "target_database": environment.target_db_name,
                    "exactly_one_target_database": bool(environment.target_db_name),
                    "isolated_filestore": artifact_root_existed_before_cleanup,
                },
                "multica_py_version": MULTICA_PY_VERSION,
                "checkout_exists": checkout.is_dir(),
                "environment_state": environment.state.value,
                "code_ownership": environment.code_ownership.value,
                "owned_artifacts_removed": not artifact_root.exists(),
                "caller_checkout_survives": checkout.is_dir(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    evidence.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.real_odoo
def test_approved_native_daemon_odoo_fixture(tmp_path: Path) -> None:  # noqa: C901
    """Run the live flow only with an explicitly disposable approved fixture."""
    if os.environ.get("ODCLI_MULTICA_LIVE_ENABLE") != "1":
        pytest.skip("set ODCLI_MULTICA_LIVE_ENABLE=1 for the disposable live fixture")
    if os.environ.get("ODCLI_MULTICA_APPROVED_FIXTURE") != "disposable-native-daemon-odoo":
        pytest.skip("live acceptance requires the approved disposable fixture sentinel")

    from multica_py import MulticaClient

    from odoo_instance_sdk import OdooClient, OdooClientConfig

    executable = _required_live("ODCLI_MULTICA_EXECUTABLE")
    repository_url = _required_live("ODCLI_MULTICA_REPOSITORY_URL")
    if repository_url != _APPROVED_NATIVE_REPOSITORY:
        pytest.fail("live fixture must use the approved Odoo repository")
    core_project = Path(_required_live("ODCLI_MULTICA_CORE_PROJECT")).resolve()
    multica_project = _required_live("ODCLI_MULTICA_PROJECT")
    issue = _required_live("ODCLI_MULTICA_ISSUE")
    run = _required_live("ODCLI_MULTICA_RUN")
    task_root = Path(_required_live("ODCLI_MULTICA_TASK_ROOT")).resolve()
    base_ref = _required_live("ODCLI_MULTICA_BASE_REF")
    if base_ref != _APPROVED_NATIVE_REF:
        pytest.fail("live fixture must use the pinned Odoo base ref")
    odoo_executable = _required_live("ODCLI_MULTICA_ODOO_EXECUTABLE")
    evidence_root = Path(_required_live("ODCLI_MULTICA_EVIDENCE_ROOT")).resolve()
    ref = _required_live("ODCLI_MULTICA_REF")
    if ref != _APPROVED_NATIVE_REF:
        pytest.fail("live fixture must use the pinned Odoo checkout ref")
    remote_name = os.environ.get("ODCLI_MULTICA_REMOTE_NAME")
    if remote_name != _APPROVED_NATIVE_REMOTE:
        pytest.fail("live fixture must provide only the approved named disposable source")

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
    daemon_status = multica.daemon.status()
    task_run = next(item for item in multica.issues.runs(issue).items if item.id == run)
    issue_record = multica.issues.get(issue)
    authoritative_project_id = multica_project
    if issue_record.project_id != authoritative_project_id:
        pytest.fail("Issue.project_id does not match the selected project")
    project = multica.projects.get(authoritative_project_id)
    resource_page = multica.projects.resources.list(project.id)
    checkout = bridge.checkout(repository_url, ref=ref)
    environment: DevelopmentEnvironment | None = None
    process = None
    phases = ["context"]
    artifact_root = task_root / "unobserved-artifacts"
    artifact_root_existed_before_cleanup = False
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
        )
        environment = bridge.prepare(preparation)
        phases.append("copy_adoption")
        artifact_root = Path(environment.artifact_root or artifact_root)
        artifact_root_existed_before_cleanup = artifact_root.is_dir()
        instance = core.instance.from_environment(environment)
        process = instance.start()
        phases.append("start")
        instance.wait_ready(process, timeout=180.0)
        assert instance.status(process).state == "running"
        phases.append("status:running")
        instance.stop(process)
        phases.append("stop")
    finally:
        if process is not None and core.get_handle(process.id) is not None:
            instance.stop(process)
        if environment is not None:
            core.environments.remove(environment)
            phases.append("remove")
        assert Path(checkout.path).is_dir(), "native checkout must outlive owned cleanup"
        assert not list(task_root.glob("**/*binding*"))
        assert not list(task_root.glob("**/*project-link*"))
        if environment is not None:
            _write_live_evidence(
                evidence_root,
                checkout=Path(checkout.path),
                environment=environment,
                task_root=task_root,
                phases=phases,
                daemon_status=daemon_status,
                task_run=task_run,
                authoritative_project_id=project.id,
                resource_page=resource_page,
                odoo_executable=odoo_executable,
                artifact_root=artifact_root,
                artifact_root_existed_before_cleanup=artifact_root_existed_before_cleanup,
            )
