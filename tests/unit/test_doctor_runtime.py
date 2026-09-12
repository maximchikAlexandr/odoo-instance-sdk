from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest
from click.testing import CliRunner

from odoo_instance_sdk import cli as cli_module
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.exceptions import EnvironmentResolutionError
from odoo_instance_sdk.internal import context as resolution
from odoo_instance_sdk.internal import doctor
from odoo_instance_sdk.internal.doctor import DoctorReport
from odoo_instance_sdk.models import (
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentState,
    PostgresClusterState,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster


@pytest.mark.unit
@pytest.mark.parametrize("selection_source", ["explicit", "cwd"])
def test_doctor_project_runtime_uses_runtime_view_and_reports_resolved_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    selection_source: Literal["explicit", "cwd"],
) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    source = tmp_path / "odoo.conf"
    source.write_text("[options]\ndb_name = demo\nhttp_port = 18069\n")
    project = ProjectConfig(
        repository_root=tmp_path,
        python="python3",
        odoo_bin=Path(sys.executable),
        source_config=source,
    )
    (manifest_dir / "project.toml").write_text(project.to_manifest())
    monkeypatch.setattr(doctor, "_database_available", lambda *_args: True)
    monkeypatch.setattr(doctor, "_http_available", lambda *_args: True)

    report = DoctorReport()
    doctor._check_project_runtime(
        report,
        cast("OdooClient", SimpleNamespace()),
        tmp_path,
        selection_source=selection_source,
    )

    runtime = next(check for check in report.checks if check.name == "runtime")
    assert runtime.facts["owner_kind"] == "project"
    assert runtime.facts["selection_source"] == selection_source
    configured = runtime.facts["configured"]
    resolved = runtime.facts["resolved"]
    available = runtime.facts["available"]
    assert isinstance(configured, dict) and configured["python"] == "python3"
    assert (
        isinstance(resolved, dict)
        and Path(str(resolved["python"])).resolve() == Path(sys.executable).resolve()
    )
    assert isinstance(available, dict)
    assert available == {
        "python": True,
        "odoo_bin": True,
        "config": True,
        "database": True,
        "http": True,
    }


@pytest.mark.unit
def test_doctor_environment_runtime_uses_environment_runtime_view(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    venv = tmp_path / "venv"
    python_bin = venv / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("")
    config = tmp_path / "odoo.conf"
    config.write_text("[options]\ndb_name = demo\nhttp_port = 18070\n")
    environment = DevelopmentEnvironment(
        id=uuid.uuid4(),
        name="env-1",
        repository_root=str(tmp_path),
        git_common_dir=str(tmp_path / ".git"),
        branch="main",
        base_ref="HEAD",
        worktree_path=str(tmp_path),
        generated_config_path=str(config),
        python_environment_path=str(venv),
        python_environment_owned=False,
        dependency_lock_path=str(tmp_path / "lock"),
        http_interface="127.0.0.1",
        http_port=18070,
        db_mode=EnvironmentDatabaseMode.SHARED,
        state=EnvironmentState.READY,
        created_at=datetime.now(UTC),
    )
    catalog = SimpleNamespace(
        get_environment_runtime=lambda _environment_id: {"odoo_bin": sys.executable}
    )
    client = SimpleNamespace(get_catalog=lambda: catalog)
    monkeypatch.setattr(doctor, "_database_available", lambda *_args: True)
    monkeypatch.setattr(doctor, "_http_available", lambda *_args: True)

    report = DoctorReport()
    doctor._check_environment_runtime(report, cast("OdooClient", client), environment)

    runtime = next(check for check in report.checks if check.name == "runtime")
    assert runtime.facts["owner_kind"] == "environment"
    assert runtime.facts["selection_source"] == "worktree"
    resolved = runtime.facts["resolved"]
    assert isinstance(resolved, dict) and resolved["python"] == str(python_bin)
    available = runtime.facts["available"]
    assert isinstance(available, dict) and all(value is True for value in available.values())


@pytest.mark.unit
def test_doctor_project_runtime_preserves_facts_when_python_resolution_fails(
    tmp_path: Path,
) -> None:
    project = ProjectConfig(
        repository_root=tmp_path,
        python="missing-python-selector",
        odoo_bin=Path(sys.executable),
    )
    (tmp_path / ".odcli").mkdir()
    (tmp_path / ".odcli" / "project.toml").write_text(project.to_manifest())

    report = DoctorReport()
    doctor._check_project_runtime(report, cast("OdooClient", SimpleNamespace()), tmp_path)

    runtime = next(check for check in report.checks if check.name == "runtime")
    assert runtime.status == doctor.STATUS_WARN
    assert isinstance(runtime.facts["configured"], dict)
    assert runtime.facts["configured"]["python"] == "missing-python-selector"
    assert isinstance(runtime.facts["resolved"], dict)
    assert runtime.facts["resolved"]["python"] is None
    assert isinstance(runtime.facts["available"], dict)
    assert runtime.facts["available"]["python"] is False
    assert runtime.facts["resolution_error"]


def _patch_public_doctor_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "_check_uv",
        "_check_optional_executables",
        "_check_catalog",
        "_check_orphaned",
        "_check_postgres",
    ):
        monkeypatch.setattr(doctor, name, lambda *args: None)
    monkeypatch.setattr(doctor, "_check_manifest", lambda *args: None)
    monkeypatch.setattr(doctor, "_database_available", lambda *_args: True)
    monkeypatch.setattr(doctor, "_database_status", lambda *_args: "available")
    monkeypatch.setattr(doctor, "_http_available", lambda *_args: True)


def test_public_doctor_keeps_invalid_project_python_as_runtime_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "odoo.conf"
    source.write_text("[options]\ndb_name = demo\n")
    project = ProjectConfig(
        repository_root=tmp_path,
        python="missing-python-selector-for-doctor",
        odoo_bin=Path(sys.executable),
        source_config=source,
    )

    class FakeClient:
        def __init__(self, *, config: object) -> None:
            self.config = config
            self.environments = SimpleNamespace(list=lambda **_kwargs: [])
            self.instance = SimpleNamespace(
                from_project=lambda _project: (_ for _ in ()).throw(
                    RuntimeError("invalid configured Python")
                )
            )

        def get_catalog(self) -> object:
            return SimpleNamespace(
                get_environment_runtime=lambda _environment_id: None,
                get_environment=lambda _environment_id: None,
            )

    monkeypatch.setattr(cli_context, "_client_class", lambda: FakeClient)
    monkeypatch.setattr(
        cli_context,
        "resolve_environment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EnvironmentResolutionError("no environment")
        ),
    )
    monkeypatch.setattr(cli_context, "_project_for_context", lambda _context: project)
    _patch_public_doctor_dependencies(monkeypatch)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--project", str(tmp_path), "doctor", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    runtime = next(item for item in payload["result"]["checks"] if item["name"] == "runtime")
    assert runtime["facts"]["owner_kind"] == "project"
    assert runtime["facts"]["selection_source"] == "explicit"
    assert runtime["facts"]["configured"]["python"] == "missing-python-selector-for-doctor"
    assert runtime["facts"]["resolved"]["python"] is None
    assert runtime["facts"]["available"]["python"] is False
    assert "missing-python-selector-for-doctor" in runtime["facts"]["resolution_error"]
    assert runtime["remediations"]


def test_public_doctor_keeps_missing_environment_artifact_as_runtime_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    environment = DevelopmentEnvironment(
        id=uuid.uuid4(),
        name="env-missing-runtime",
        repository_root=str(tmp_path),
        git_common_dir=str(tmp_path / ".git"),
        branch="main",
        base_ref="HEAD",
        worktree_path=str(tmp_path),
        generated_config_path=str(tmp_path / "missing-odoo.conf"),
        python_environment_path=str(tmp_path / "missing-venv"),
        python_environment_owned=False,
        dependency_lock_path=str(tmp_path / "lock"),
        http_interface="127.0.0.1",
        http_port=18069,
        db_mode=EnvironmentDatabaseMode.SHARED,
        state=EnvironmentState.READY,
        created_at=datetime.now(UTC),
    )

    class FakeClient:
        def __init__(self, *, config: object) -> None:
            self.config = config
            self.environments = SimpleNamespace(list=lambda **_kwargs: [])
            self.instance = SimpleNamespace(
                from_environment=lambda _environment: (_ for _ in ()).throw(
                    RuntimeError("missing generated runtime artifact")
                )
            )

        def get_catalog(self) -> object:
            return SimpleNamespace(
                get_environment_runtime=lambda _environment_id: None,
                get_environment=lambda _environment_id: None,
            )

    monkeypatch.setattr(cli_context, "_client_class", lambda: FakeClient)
    monkeypatch.setattr(cli_context, "resolve_environment", lambda *_args, **_kwargs: environment)
    monkeypatch.setattr(
        resolution,
        "_verify_env_runtime",
        lambda _environment: (_ for _ in ()).throw(
            RuntimeError("missing generated runtime artifact")
        ),
    )
    _patch_public_doctor_dependencies(monkeypatch)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--env", str(environment.id), "doctor", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    runtime = next(item for item in payload["result"]["checks"] if item["name"] == "runtime")
    assert runtime["environment_id"] == str(environment.id)
    assert runtime["facts"]["owner_kind"] == "environment"
    assert runtime["facts"]["selection_source"] == "explicit"
    assert runtime["facts"]["configured"]["config"] == environment.generated_config_path
    assert runtime["facts"]["resolved"]
    assert runtime["facts"]["available"]["config"] is False
    assert runtime["facts"]["resolution_error"] == "missing generated runtime artifact"
    assert runtime["remediations"]


@pytest.mark.unit
def test_doctor_project_runtime_marks_ambiguous_database_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "odoo.conf"
    source.write_text("[options]\ndb_name = alpha,beta\n")
    project = ProjectConfig(
        repository_root=tmp_path,
        python=Path(sys.executable),
        odoo_bin=Path(sys.executable),
        source_config=source,
    )
    (tmp_path / ".odcli").mkdir()
    (tmp_path / ".odcli" / "project.toml").write_text(project.to_manifest())
    monkeypatch.setattr(doctor, "_http_available", lambda *_args: True)

    report = DoctorReport()
    doctor._check_project_runtime(report, cast("OdooClient", SimpleNamespace()), tmp_path)

    runtime = next(check for check in report.checks if check.name == "runtime")
    assert runtime.facts["database_status"] == "ambiguous"
    available = runtime.facts["available"]
    assert isinstance(available, dict) and available["database"] is False


@pytest.mark.unit
def test_doctor_database_status_distinguishes_stopped_cluster(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StoppedCluster:
        def status(self) -> PostgresClusterState:
            return PostgresClusterState.STOPPED

    monkeypatch.setattr(
        PostgresCluster,
        "from_project",
        classmethod(lambda _cls, _root: StoppedCluster()),
    )

    assert doctor._database_status(tmp_path, "demo") == "unavailable"
    assert doctor._database_status(tmp_path, "") == "missing"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "owner_kind", "selection_source"),
    [
        (("--env", "env-42", "doctor"), "environment", "explicit"),
        (("doctor",), "environment", "worktree"),
        (("--project", "/selected-project", "doctor"), "project", "explicit"),
        (("doctor",), "project", "cwd"),
    ],
)
def test_cli_doctor_uses_effective_owner_from_ready_instance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    args: tuple[str, ...],
    owner_kind: str,
    selection_source: str,
) -> None:
    ready_calls: list[object] = []
    doctor_calls: list[dict[str, object]] = []
    resolved = SimpleNamespace(client=SimpleNamespace(), project_root=tmp_path)

    def ready_instance(context: object) -> object:
        ready_calls.append(context)
        return resolved

    def run_selected(
        client: object,
        project_root: Path,
        *,
        resolved_context: object,
    ) -> DoctorReport:
        doctor_calls.append(
            {
                "client": client,
                "project_root": project_root,
                "resolved_context": resolved_context,
            }
        )
        return DoctorReport(
            checks=[
                doctor.CheckResult(
                    name="runtime",
                    status=doctor.STATUS_OK,
                    detail="selected",
                    facts={
                        "owner_kind": owner_kind,
                        "selection_source": selection_source,
                    },
                )
            ]
        )

    monkeypatch.setattr(cli_context, "ready_instance", ready_instance)
    monkeypatch.setattr(cli_module, "run_doctor", run_selected)

    result = CliRunner().invoke(cli_module.cli, [*args, "--format", "json"])

    assert result.exit_code == 0, result.output
    assert len(ready_calls) == 1
    assert len(doctor_calls) == 1
    assert doctor_calls[0]["resolved_context"] is resolved
    payload = json.loads(result.stdout)
    facts = payload["result"]["checks"][0]["facts"]
    assert facts == {"owner_kind": owner_kind, "selection_source": selection_source}
