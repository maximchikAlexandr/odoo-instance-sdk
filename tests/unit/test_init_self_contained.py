from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.dbprep.bootstrap import (
    BOOTSTRAP_DATABASE,
    BootstrapFailedError,
    BootstrapOutcome,
    bootstrap_tmp_plan,
    tmp_bootstrap_command,
)
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessHandle,
    ProcessResult,
    RecordingExecutor,
)
from odoo_instance_sdk.internal.project_init import (
    _INIT_REMOTE_NAMES_ACTION_ID,
    evaluate_init_completeness,
    project_owned_data_dir,
    verify_project_owned_data_dir,
)
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.venv_origin import origin_record_path
from odoo_instance_sdk.models import Backup, DatabaseRefreshOptions, StartConfig
from odoo_instance_sdk.project import (
    PostgresProjectConfig,
    ProjectConfig,
    RemoteSourceConfig,
    TestInstanceProjectConfig as RemoteTestInstanceConfig,
)
from odoo_instance_sdk.project_init import (
    _BootstrapFollowup,
    _compose_followup_steps,
    _execute_bootstrap_followup_phase,
    init_project,
    init_project_command,
)
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.resources.instance import (
    AuxiliaryRestoreSession,
    OdooInstance,
    auxiliary_restore_session,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


@pytest.fixture(autouse=True)
def _cleanup_magicmock_cwd_artifacts() -> object:
    """Remove accidental MagicMock path strings written as files in cwd."""
    yield
    for path in Path.cwd().glob("<MagicMock*"):
        if path.is_file():
            path.unlink()


@pytest.fixture
def stub_psql_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Inject a disposable native-client path for public CLI command tests."""
    psql = tmp_path / "psql"
    psql.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    psql.chmod(0o755)
    path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", os.pathsep.join((str(tmp_path), path)))
    assert shutil.which("psql") == str(psql)


@pytest.fixture
def stub_compose_init_followup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose init chains postgres start and tmp bootstrap after scaffold."""

    def _noop_ensure_running(
        self: PostgresCluster,
        timeout: float = 60.0,
        *,
        temporary_path: Path | None = None,
        step_ids: object = None,
    ) -> None:
        return None

    def _skip_bootstrap_tmp(
        context: object,
        plan: object,
    ) -> BootstrapOutcome:
        context.skip(plan.spawn_step.step_id)  # type: ignore[attr-defined]
        context.skip(plan.probe_step.step_id)  # type: ignore[attr-defined]
        context.skip(plan.ready_step.step_id)  # type: ignore[attr-defined]
        if plan.create_step is not None:  # type: ignore[attr-defined]
            context.skip(plan.create_step.step_id)  # type: ignore[attr-defined]
        return BootstrapOutcome.ALREADY_READY

    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", _noop_ensure_running)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _skip_bootstrap_tmp,
    )


def _test_instance(**kwargs: object) -> RemoteTestInstanceConfig:
    return RemoteTestInstanceConfig(**cast("Any", kwargs))


def _skip_bootstrap_process_steps(context: object, plan: object) -> None:
    context.skip(plan.probe_step.step_id)  # type: ignore[attr-defined]
    if plan.create_step is not None:  # type: ignore[attr-defined]
        context.skip(plan.create_step.step_id)  # type: ignore[attr-defined]
    context.skip(plan.spawn_step.step_id)  # type: ignore[attr-defined]
    context.skip(plan.ready_step.step_id)  # type: ignore[attr-defined]


def _base_args(
    tmp_path: Path, *, json_output: bool = False, allow_partial: bool = False
) -> list[str]:
    args = [
        "init",
        "--no-input",
        "--odoo-bin",
        "/opt/odoo/odoo-bin",
        "--python",
        "python3",
        "--project",
        str(tmp_path),
    ]
    if allow_partial:
        args.insert(2, "--allow-partial")
    if json_output:
        args.extend(["--format", "json"])
    return args


def _compose_config(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        source_config=tmp_path / ".odcli" / "odoo.conf",
        postgres=PostgresProjectConfig(
            mode="compose",
            image="pgvector/pgvector:pg16",
            port=5468,
            user="odoo",
        ),
        test_instance=_test_instance(
            base_url="http://example.test:8069",
            database="remote_db",
            git_branch="main",
        ),
    )


def _ready_probe_result(step: object) -> ProcessResult:
    return ProcessResult(
        argv=step.argv,  # type: ignore[attr-defined]
        returncode=0,
        stdout="installed",
        stderr="",
        duration=0.0,
        cwd=getattr(step, "cwd", None),
        environment=cast(
            "tuple[tuple[str, str], ...]",
            getattr(step, "environment", ()),
        ),
    )


def _failed_probe_result(step: object) -> ProcessResult:
    return ProcessResult(
        argv=step.argv,  # type: ignore[attr-defined]
        returncode=0,
        stdout="uninstalled",
        stderr="",
        duration=0.0,
        cwd=getattr(step, "cwd", None),
        environment=cast(
            "tuple[tuple[str, str], ...]",
            getattr(step, "environment", ()),
        ),
    )


@dataclass(frozen=True)
class _PrimaryVenvScenario:
    args: list[str]
    source: Path
    original_cfg: Path
    original_interpreter: Path
    prepared_interpreter: Path


def _set_uv_metadata_version(cfg: Path, version: str) -> None:
    lines = cfg.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("uv = "):
            lines[index] = f"uv = {version}\n"
            cfg.write_text("".join(lines), encoding="utf-8")
            return
    raise AssertionError(f"uv metadata is missing from {cfg}")


def _uv_metadata_version(cfg: Path) -> str:
    for line in cfg.read_text(encoding="utf-8").splitlines():
        if line.startswith("uv = "):
            return line.partition("=")[2].strip()
    raise AssertionError(f"uv metadata is missing from {cfg}")


def _build_primary_venv_scenario(tmp_path: Path) -> _PrimaryVenvScenario:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    uv_executable = shutil.which("uv")
    assert uv_executable is not None
    original_venv = tmp_path.parent / f"{tmp_path.name}-original-venv"
    subprocess.run(
        [uv_executable, "venv", "--python", "python3", str(original_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    original_interpreter = original_venv / "bin" / "python"
    assert original_interpreter.is_file()
    original_cfg = original_venv / "pyvenv.cfg"
    _set_uv_metadata_version(original_cfg, "0.11.1")
    source = tmp_path / "local-source.conf"
    source.write_text("[options]\ncustom_option = retained\n", encoding="utf-8")
    module_requirements = tmp_path / "addons" / "nested" / "module_a" / "requirements.txt"
    module_requirements.parent.mkdir(parents=True)
    module_requirements.write_text("requests==2.32.5\n", encoding="utf-8")
    explicit_requirements = tmp_path / "explicit.txt"
    explicit_requirements.write_text("urllib3==2.5.0\n", encoding="utf-8")
    args = [
        *_base_args(tmp_path, allow_partial=True),
        "--config",
        str(source),
        "--postgres",
        "compose",
        "--postgres-image",
        "pgvector/pgvector:pg16",
        "--postgres-port",
        "5468",
        "--addons-root",
        "addons",
        "--server-wide-module",
        "base",
        "--server-wide-module",
        "web",
        "--requirements",
        str(module_requirements),
        "--requirements",
        str(explicit_requirements),
    ]
    args[args.index("--python") + 1] = str(original_interpreter)
    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    prepared_venv = tmp_path / ".myl491-venv"
    subprocess.run(
        [uv_executable, "venv", "--python", str(original_interpreter), str(prepared_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    prepared_interpreter = prepared_venv / "bin" / "python"
    assert prepared_interpreter.is_file()
    return _PrimaryVenvScenario(
        args=args,
        source=source,
        original_cfg=original_cfg,
        original_interpreter=original_interpreter,
        prepared_interpreter=prepared_interpreter,
    )


def _prepare_primary_venv(
    tmp_path: Path,
    scenario: _PrimaryVenvScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.project_init import prepare_venv_command

    def _prepare_result(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        if "compile" in prepared.step_id:
            Path(prepared.argv[-1]).write_text("prepared-lock\n", encoding="utf-8")
        return ProcessResult(
            argv=prepared.argv,
            returncode=0,
            stdout=("postgres@sha256:" + "a" * 64) if "inspect" in prepared.argv else "",
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    executor = RecordingExecutor(result_factory=_prepare_result)
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)
    prepare_venv_command(
        tmp_path,
        python=str(scenario.original_interpreter),
        venv=".myl491-venv",
    ).run()


def _record_prepared_venv_origin(
    tmp_path: Path,
    prepared_interpreter: Path,
    requested_interpreter: str | Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish the same private provenance that a successful prepare emits."""
    from odoo_instance_sdk.project_init import prepare_venv_command

    project = ProjectConfig(repository_root=tmp_path, python=prepared_interpreter)
    write_manifest(tmp_path, project)

    def _result(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        return ProcessResult(
            argv=prepared.argv,
            returncode=0,
            stdout=("postgres@sha256:" + "a" * 64) if "inspect" in prepared.argv else "",
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    executor = RecordingExecutor(result_factory=_result)
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)
    prepare_venv_command(
        tmp_path, python=str(requested_interpreter), venv=prepared_interpreter.parent.parent
    ).run()


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_no_input_incomplete_fails_before_writes(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--yes",
        ],
    )
    assert result.exit_code != 0
    assert result.exception is not None
    assert "init_incomplete" in str(result.exception)
    assert not (tmp_path / ".odcli" / "project.toml").exists()


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_allow_partial_proceeds_with_warning(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path, json_output=True),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
            "--allow-partial",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["result"]["partial"] is True
    assert "test_url" in payload["result"]["missing"]
    manifest = (tmp_path / ".odcli" / "project.toml").read_text(encoding="utf-8")
    assert "default_source_database" not in manifest
    assert "managed_filestore" not in manifest


@pytest.mark.parametrize("database", ["", "   ", "bad/name"])
@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_rejects_invalid_database_before_manifest_or_cluster_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: str
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    spawn = MagicMock(side_effect=AssertionError("cluster spawn must not be reached"))
    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", spawn)
    result = CliRunner().invoke(
        cli,
        [
            *_base_args(tmp_path, json_output=True, allow_partial=True),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
            "--database",
            database,
        ],
    )
    assert result.exit_code != 0
    assert result.exception is not None
    assert "Invalid database name" in str(result.exception)
    assert not (tmp_path / ".odcli" / "project.toml").exists()
    spawn.assert_not_called()


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_full_self_contained_writes_data_dir_and_dotenv(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
            "--test-url",
            "http://127.0.0.1:18069",
            "--test-database",
            "remote_db",
            "--test-branch",
            "main",
            "--local-config",
            "--allow-partial",
        ],
    )
    assert result.exit_code == 0, result.output
    generated = parse_odoo_config(tmp_path / ".odcli" / "odoo.conf")
    assert generated["data_dir"] == str(project_owned_data_dir(tmp_path))
    dotenv = tmp_path / ".odcli" / ".env"
    assert dotenv.is_file()
    assert dotenv.stat().st_mode & 0o777 == 0o600
    dotenv_text = dotenv.read_text()
    assert "ODCLI_TEST_INSTANCE_ORIGIN_PINS" not in dotenv_text
    assert "ODCLI_TEST_MASTER_PASSWORD=" in dotenv_text


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_rerun_preserves_existing_test_instance(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    first = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--test-url",
            "http://127.0.0.1:18069",
            "--test-database",
            "remote_db",
            "--test-branch",
            "main",
            "--allow-partial",
        ],
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
        ],
    )
    assert second.exit_code == 0, second.output
    manifest = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "http://127.0.0.1:18069" in manifest
    assert 'database = "remote_db"' in manifest


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_after_prepare_venv_renders_e2e_inputs(tmp_path: Path) -> None:
    scenario = _build_primary_venv_scenario(tmp_path)
    generated = tmp_path / ".odcli" / "odoo.conf"
    assert generated.stat().st_mode & 0o777 == 0o600
    assert "custom_option = retained" in generated.read_text(encoding="utf-8")
    assert "--config" in scenario.args
    assert "--addons-root" in scenario.args
    assert scenario.original_interpreter.is_file()


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_after_prepare_venv_persists_real_uv_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _build_primary_venv_scenario(tmp_path)
    original_uv = _uv_metadata_version(scenario.original_cfg)
    prepared_cfg = scenario.prepared_interpreter.parent.parent / "pyvenv.cfg"
    prepared_uv = _uv_metadata_version(prepared_cfg)
    assert original_uv == "0.11.1"
    assert prepared_uv != original_uv
    assert "executable =" not in prepared_cfg.read_text(encoding="utf-8")
    _prepare_primary_venv(tmp_path, scenario, monkeypatch)
    assert ProjectConfig.load(tmp_path).python == scenario.prepared_interpreter
    origin = json.loads(origin_record_path(tmp_path).read_text(encoding="utf-8"))
    assert origin["venv_root"] == str(scenario.prepared_interpreter.parent.parent.resolve())
    assert origin["requested_path"] == str(scenario.original_interpreter.absolute())
    manifest_before = (tmp_path / ".odcli" / "project.toml").read_bytes()
    lock = tmp_path / ".odcli" / "requirements.lock"
    lock.write_bytes(b"prepared-lock\n")
    assert lock.read_bytes() == b"prepared-lock\n"
    assert manifest_before


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_after_prepare_venv_preserves_primary_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeating init with the original runtime inputs keeps the prepared venv."""
    scenario = _build_primary_venv_scenario(tmp_path)
    _prepare_primary_venv(tmp_path, scenario, monkeypatch)
    runner = CliRunner()
    manifest_before = (tmp_path / ".odcli" / "project.toml").read_bytes()
    lock = tmp_path / ".odcli" / "requirements.lock"
    lock.write_bytes(b"prepared-lock\n")
    lock_before = lock.read_bytes()
    source_before = scenario.source.read_bytes()

    repeated = runner.invoke(cli, [*scenario.args, "--yes"])
    assert repeated.exit_code == 0, repeated.output
    assert ProjectConfig.load(tmp_path).python == scenario.prepared_interpreter
    assert (tmp_path / ".odcli" / "project.toml").read_bytes() == manifest_before
    assert lock.read_bytes() == lock_before
    assert scenario.source.read_bytes() == source_before


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_rejects_tampered_prepared_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _build_primary_venv_scenario(tmp_path)
    _prepare_primary_venv(tmp_path, scenario, monkeypatch)
    candidate = scenario.prepared_interpreter
    candidate.unlink()
    candidate.write_text("attacker-controlled\n", encoding="utf-8")

    project = ProjectConfig.load(tmp_path)
    from odoo_instance_sdk.commands.cli_parts.init_registration import (
        _preserve_prepared_interpreter,
    )

    assert (
        _preserve_prepared_interpreter(tmp_path, scenario.original_interpreter, project)
        == scenario.original_interpreter
    )


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_rejects_stale_origin_after_project_recreation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _build_primary_venv_scenario(tmp_path)
    _prepare_primary_venv(tmp_path, scenario, monkeypatch)
    manifest = tmp_path / ".odcli" / "project.toml"
    manifest.unlink()
    replacement = ProjectConfig(
        repository_root=tmp_path,
        python=scenario.prepared_interpreter,
        requirements=("replacement.txt",),
    )
    write_manifest(tmp_path, replacement)
    from odoo_instance_sdk.commands.cli_parts.init_registration import (
        _preserve_prepared_interpreter,
    )

    assert (
        _preserve_prepared_interpreter(tmp_path, scenario.original_interpreter, replacement)
        == scenario.original_interpreter
    )


def test_prepared_uv_interpreter_allows_explicit_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absolute uv inputs match while explicit replacement inputs remain available."""
    uv_executable = shutil.which("uv")
    assert uv_executable is not None
    original_venv = tmp_path.parent / f"{tmp_path.name}-original-venv"
    subprocess.run(
        [uv_executable, "venv", "--python", "python3", str(original_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    original_interpreter = original_venv / "bin" / "python"
    original_cfg = original_venv / "pyvenv.cfg"
    _set_uv_metadata_version(original_cfg, "0.11.1")
    prepared_venv = tmp_path / ".myl491-venv"
    subprocess.run(
        [uv_executable, "venv", "--python", str(original_interpreter), str(prepared_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    prepared_interpreter = prepared_venv / "bin" / "python"
    original_uv = _uv_metadata_version(original_cfg)
    prepared_uv = _uv_metadata_version(prepared_venv / "pyvenv.cfg")
    assert original_uv == "0.11.1"
    assert prepared_uv != original_uv
    _record_prepared_venv_origin(tmp_path, prepared_interpreter, original_interpreter, monkeypatch)
    project = ProjectConfig(repository_root=tmp_path, python=prepared_interpreter)
    from odoo_instance_sdk.commands.cli_parts.init_registration import (
        _preserve_prepared_interpreter,
    )

    assert (
        _preserve_prepared_interpreter(tmp_path, original_interpreter, project)
        == prepared_interpreter
    )
    alternate_venv = tmp_path.parent / f"{tmp_path.name}-alternate-venv"
    subprocess.run(
        [uv_executable, "venv", "--python", "python3", str(alternate_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    alternate = alternate_venv / "bin" / "python"
    assert alternate.is_file()
    assert _preserve_prepared_interpreter(tmp_path, alternate, project) == alternate
    assert _preserve_prepared_interpreter(tmp_path, "python999", project) == "python999"


def test_escaping_venv_root_does_not_preserve_or_mutate_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uv_executable = shutil.which("uv")
    assert uv_executable is not None
    external_venv = tmp_path.parent / f"{tmp_path.name}-external-venv"
    subprocess.run(
        [uv_executable, "venv", "--python", "python3", str(external_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    project_venv = tmp_path / ".myl491-venv"
    project_venv.symlink_to(external_venv, target_is_directory=True)
    candidate = project_venv / "bin" / "python"
    venv_cfg = external_venv / "pyvenv.cfg"
    venv_cfg_before = venv_cfg.read_bytes()
    replacement = tmp_path / "replacement-python"
    replacement.write_text("replacement\n", encoding="utf-8")
    project = ProjectConfig(repository_root=tmp_path, python=candidate)
    manifest = tmp_path / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_bytes(b"before\n")
    process_calls: list[object] = []

    def _process_guard() -> None:
        process_calls.append(object())
        raise AssertionError("escaping venv must be rejected before process creation")

    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", _process_guard)
    from odoo_instance_sdk.commands.cli_parts.init_registration import (
        _preserve_prepared_interpreter,
    )

    assert _preserve_prepared_interpreter(tmp_path, replacement, project) == replacement
    assert manifest.read_bytes() == b"before\n"
    assert venv_cfg.read_bytes() == venv_cfg_before
    assert not process_calls
    assert not origin_record_path(tmp_path).exists()


def test_forged_venv_executable_metadata_does_not_preserve(tmp_path: Path) -> None:
    uv_executable = shutil.which("uv")
    assert uv_executable is not None
    requested_venv = tmp_path.parent / f"{tmp_path.name}-requested-venv"
    prepared_venv = tmp_path / ".myl491-venv"
    subprocess.run(
        [uv_executable, "venv", "--python", "python3", str(requested_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    requested = requested_venv / "bin" / "python"
    subprocess.run(
        [uv_executable, "venv", "--python", str(requested), str(prepared_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    cfg = prepared_venv / "pyvenv.cfg"
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + f"executable = {requested}\n", encoding="utf-8"
    )
    project = ProjectConfig(repository_root=tmp_path, python=prepared_venv / "bin" / "python")
    local_forged = tmp_path / ".odcli" / "primary-venv-origin.json"
    local_forged.parent.mkdir()
    local_forged.write_text(
        json.dumps(
            {
                "project_root": str(tmp_path.resolve()),
                "venv_root": str(prepared_venv.resolve()),
                "requested": str(requested),
                "requested_path": str(requested.absolute()),
            }
        ),
        encoding="utf-8",
    )
    from odoo_instance_sdk.commands.cli_parts.init_registration import (
        _preserve_prepared_interpreter,
    )

    assert _preserve_prepared_interpreter(tmp_path, requested, project) == requested
    cfg.write_text(
        "\n".join(
            line
            for line in cfg.read_text(encoding="utf-8").splitlines()
            if not line.startswith("uv =")
        )
        + "\n",
        encoding="utf-8",
    )
    assert _preserve_prepared_interpreter(tmp_path, requested, project) == requested


def test_malformed_prepared_venv_origin_fails_closed(tmp_path: Path) -> None:
    uv_executable = shutil.which("uv")
    assert uv_executable is not None
    requested_venv = tmp_path.parent / f"{tmp_path.name}-requested-venv"
    prepared_venv = tmp_path / ".myl491-venv"
    subprocess.run(
        [uv_executable, "venv", "--python", "python3", str(requested_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    requested = requested_venv / "bin" / "python"
    subprocess.run(
        [uv_executable, "venv", "--python", str(requested), str(prepared_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    project = ProjectConfig(repository_root=tmp_path, python=prepared_venv / "bin" / "python")
    origin = origin_record_path(tmp_path)
    origin.parent.mkdir(parents=True)
    from odoo_instance_sdk.commands.cli_parts.init_registration import (
        _preserve_prepared_interpreter,
    )

    origin.write_text("[]\n", encoding="utf-8")
    assert _preserve_prepared_interpreter(tmp_path, requested, project) == requested
    origin.write_text("{\n", encoding="utf-8")
    assert _preserve_prepared_interpreter(tmp_path, requested, project) == requested
    origin.write_text(json.dumps({"venv_root": str(tmp_path)}) + "\n", encoding="utf-8")
    assert _preserve_prepared_interpreter(tmp_path, requested, project) == requested


def test_origin_request_normalizes_tilde_before_project_relative_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    uv_executable = shutil.which("uv")
    assert uv_executable is not None
    original_venv = home / "original-venv"
    prepared_venv = tmp_path / ".myl491-venv"
    subprocess.run(
        [uv_executable, "venv", "--python", "python3", str(original_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    original = original_venv / "bin" / "python"
    subprocess.run(
        [uv_executable, "venv", "--python", str(original), str(prepared_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    prepared = prepared_venv / "bin" / "python"
    project = ProjectConfig(repository_root=tmp_path, python=prepared)
    write_manifest(tmp_path, project)
    _record_prepared_venv_origin(tmp_path, prepared, original, monkeypatch)
    from odoo_instance_sdk.commands.cli_parts.init_registration import (
        _preserve_prepared_interpreter,
    )

    requested = "~/original-venv/bin/python"
    assert _preserve_prepared_interpreter(tmp_path, requested, project) == prepared


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_origin_staging_ignores_project_symlink_victim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _build_primary_venv_scenario(tmp_path)
    victim = tmp_path.parent / f"{tmp_path.name}-origin-victim"
    victim.write_bytes(b"victim-before\n")
    staged = tmp_path / ".odcli" / "primary-venv-origin.json.prepare"
    staged.symlink_to(victim)
    _prepare_primary_venv(tmp_path, scenario, monkeypatch)
    assert victim.read_bytes() == b"victim-before\n"
    assert staged.is_symlink()


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_origin_publish_failure_restores_manifest_lock_and_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _build_primary_venv_scenario(tmp_path)
    _prepare_primary_venv(tmp_path, scenario, monkeypatch)
    manifest = tmp_path / ".odcli" / "project.toml"
    lock = tmp_path / ".odcli" / "requirements.lock"
    origin = origin_record_path(tmp_path)
    before = (manifest.read_bytes(), lock.read_bytes(), origin.read_bytes())

    def _fail_publish(_stage: object) -> None:
        raise OSError("injected origin publish failure")

    monkeypatch.setattr("odoo_instance_sdk.internal.primary_venv.publish_origin", _fail_publish)
    with pytest.raises(OSError, match="injected origin publish failure"):
        _prepare_primary_venv(tmp_path, scenario, monkeypatch)
    assert (manifest.read_bytes(), lock.read_bytes(), origin.read_bytes()) == before


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_with_distinct_valid_uv_venv_keeps_replacement_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _build_primary_venv_scenario(tmp_path)
    _prepare_primary_venv(tmp_path, scenario, monkeypatch)
    uv_executable = shutil.which("uv")
    assert uv_executable is not None
    alternate_venv = tmp_path.parent / f"{tmp_path.name}-distinct-venv"
    subprocess.run(
        [uv_executable, "venv", "--python", "python3", str(alternate_venv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    alternate = alternate_venv / "bin" / "python"
    args = [*scenario.args]
    args[args.index("--python") + 1] = str(alternate)
    result = CliRunner().invoke(cli, [*args, "--dry-run", "--yes"])
    assert result.exit_code == 0, result.output
    assert str(alternate) in result.output
    assert ProjectConfig.load(tmp_path).python == scenario.prepared_interpreter


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_failed_compose_init_resumes_without_manifest_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    def _fail(_context: object, _plan: object) -> BootstrapOutcome:
        raise BootstrapFailedError("tmp bootstrap failed")

    def _skip(context: object, plan: object) -> BootstrapOutcome:
        _skip_bootstrap_process_steps(context, plan)
        return BootstrapOutcome.ALREADY_READY

    actions = iter((_fail, _skip))

    def _fail_once_then_skip(context: object, plan: object) -> BootstrapOutcome:
        return next(actions)(context, plan)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _fail_once_then_skip,
    )
    runner = CliRunner()
    args = [
        *_base_args(tmp_path, allow_partial=True),
        "--postgres",
        "compose",
        "--postgres-image",
        "pgvector/pgvector:pg16",
        "--postgres-port",
        "5468",
        "--test-url",
        "http://127.0.0.1:18069",
        "--test-database",
        "remote_db",
        "--test-branch",
        "main",
        "--local-config",
    ]
    first = runner.invoke(cli, args)
    assert first.exit_code != 0
    manifest = tmp_path / ".odcli" / "project.toml"
    assert manifest.is_file()
    before = manifest.read_bytes()

    def _unexpected_manifest_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("identical Compose retry must not rewrite the manifest")

    monkeypatch.setattr("odoo_instance_sdk.project_init.write_manifest", _unexpected_manifest_write)
    second = runner.invoke(cli, args)
    assert second.exit_code == 0, second.output
    assert manifest.read_bytes() == before


def _compose_config_repair_case(tmp_path: Path) -> tuple[CliRunner, list[str], Path]:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    args = [
        *_base_args(tmp_path, allow_partial=True),
        "--postgres",
        "compose",
        "--postgres-image",
        "pgvector/pgvector:pg16",
        "--postgres-port",
        "5468",
        "--test-url",
        "http://127.0.0.1:18069",
        "--test-database",
        "remote_db",
        "--test-branch",
        "main",
        "--local-config",
        "--format",
        "json",
    ]
    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    generated = tmp_path / ".odcli" / "odoo.conf"
    generated.unlink()
    return runner, args, generated


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_compose_config_repair_dry_run_does_not_mutate(tmp_path: Path) -> None:
    runner, args, generated = _compose_config_repair_case(tmp_path)

    dry_run = runner.invoke(cli, [*args, "--dry-run"])
    assert dry_run.exit_code == 0, dry_run.output
    assert json.loads(dry_run.output)["dry_run"] is True
    assert not generated.exists()


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_compose_config_repair_resumes_lifecycle_without_premature_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, args, generated = _compose_config_repair_case(tmp_path)

    calls: list[str] = []

    def _track_bootstrap(
        context: object,
        plan: object,
    ) -> BootstrapOutcome:
        calls.append("bootstrap")
        _skip_bootstrap_process_steps(context, plan)
        return BootstrapOutcome.ALREADY_READY

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _track_bootstrap,
    )
    repaired = runner.invoke(cli, args)
    assert repaired.exit_code == 0, repaired.output
    payload = json.loads(repaired.output)
    assert payload["ok"] is True
    assert "generated_config" not in payload["result"]
    assert generated.is_file()
    assert calls == ["bootstrap"]


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_complete_compose_retry_skips_tmp_recreation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    args = [
        *_base_args(tmp_path, allow_partial=True),
        "--postgres",
        "compose",
        "--postgres-image",
        "pgvector/pgvector:pg16",
        "--postgres-port",
        "5468",
        "--format",
        "json",
    ]
    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output

    def _ready_sql_step(
        *,
        db_host: str,
        db_port: int,
        db_user: str,
        db_password: str,
        step_id: str,
        database: str = "tmp",
        stdin: bytes = b"",
        read_only: bool = True,
        mutating: bool = False,
    ) -> PreparedStep:
        del db_host, db_port, db_user, db_password, database, stdin, read_only, mutating
        return PreparedStep(
            step_id=step_id,
            argv=("/usr/bin/printf", "installed"),
            timeout=30.0,
            read_only=True,
        )

    from odoo_instance_sdk.internal.dbprep import bootstrap

    monkeypatch.setattr(bootstrap, "_bootstrap_sql_step", _ready_sql_step)

    def _probe_ready_without_spawn(
        context: object,
        plan: object,
    ) -> BootstrapOutcome:
        assert plan.create_step is None  # type: ignore[attr-defined]
        result = context.process_prepared(plan.probe_step)  # type: ignore[attr-defined]
        assert getattr(result, "stdout", "").strip() == "installed"
        context.skip(plan.spawn_step.step_id)  # type: ignore[attr-defined]
        context.skip(plan.ready_step.step_id)  # type: ignore[attr-defined]
        return BootstrapOutcome.ALREADY_READY

    monkeypatch.setattr(bootstrap, "run_bootstrap_tmp", _probe_ready_without_spawn)
    retry = runner.invoke(cli, args)
    assert retry.exit_code == 0, retry.output
    assert json.loads(retry.output)["ok"] is True


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_repeated_compose_resume_failure_preserves_boundary_error_and_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    args = [
        *_base_args(tmp_path, allow_partial=True),
        "--postgres",
        "compose",
        "--postgres-image",
        "pgvector/pgvector:pg16",
        "--postgres-port",
        "5468",
    ]
    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    manifest = tmp_path / ".odcli" / "project.toml"
    before = manifest.read_bytes()
    calls: list[tuple[str, ...]] = []

    def _fail_after_accounting(
        context: object,
        plan: object,
    ) -> BootstrapOutcome:
        calls.append(tuple(step.step_id for step in plan.execution_steps))  # type: ignore[attr-defined]
        _skip_bootstrap_process_steps(context, plan)
        raise BootstrapFailedError("tmp bootstrap failed: boundary unavailable")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _fail_after_accounting,
    )
    failed = runner.invoke(cli, args)
    assert failed.exit_code != 0
    assert isinstance(failed.exception, BootstrapFailedError)
    assert failed.exception.error_code == "init_bootstrap_failed"
    assert "boundary unavailable" in str(failed.exception)
    failed_again = runner.invoke(cli, args)
    assert failed_again.exit_code != 0
    assert isinstance(failed_again.exception, BootstrapFailedError)
    assert failed_again.exception.error_code == "init_bootstrap_failed"
    assert "boundary unavailable" in str(failed_again.exception)
    assert manifest.read_bytes() == before
    assert calls == [
        (
            "init.bootstrap.tmp.verify",
            "init.bootstrap.tmp.probe",
            "init.bootstrap.tmp",
            "init.bootstrap.tmp.ready",
            "init.bootstrap.databases",
            "catalog.bootstrap.tmp.record",
        ),
        (
            "init.bootstrap.tmp.verify",
            "init.bootstrap.tmp.probe",
            "init.bootstrap.tmp",
            "init.bootstrap.tmp.ready",
            "init.bootstrap.databases",
            "catalog.bootstrap.tmp.record",
        ),
    ]


def test_named_remote_init_is_complete_repeatable_and_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda **_kwargs: tmp_path / "catalog.sqlite3",
    )
    runner = CliRunner()
    common = [
        "init",
        "--no-input",
        "--yes",
        "--odoo-bin",
        "/opt/odoo/odoo-bin",
        "--python",
        "python3",
        "--config",
        "odoo.conf",
        "--project",
        str(tmp_path),
        "--format",
        "json",
    ]
    first = runner.invoke(
        cli,
        [
            *common,
            "--remote",
            "staging",
            "https://staging.example/",
            "staging",
            "main",
            "--remote",
            "PROD",
            "https://prod.example",
            "production",
            "release/19",
        ],
    )
    assert first.exit_code == 0, first.output
    payload = json.loads(first.output)["result"]
    assert payload["remote_password_keys"] == [
        "ODCLI_REMOTE_PROD_MASTER_PASSWORD",
        "ODCLI_REMOTE_STAGING_MASTER_PASSWORD",
    ]
    assert "remote-secret" not in first.output

    second = runner.invoke(cli, common)
    assert second.exit_code == 0, second.output
    config = ProjectConfig.load(tmp_path)
    assert [source.name for source in config.remote_instances] == ["prod", "staging"]
    assert config.remote_instances[1].database == "staging"
    assert config.remote_instances[1].git_branch == "main"
    assert config.test_instance is None
    manifest = (tmp_path / ".odcli" / "project.toml").read_text(encoding="utf-8")
    assert "ODCLI_REMOTE_" not in manifest
    assert "remote-secret" not in manifest

    third = runner.invoke(cli, [*common, "--remote", "lab", "https://lab.example", "dev"])
    assert third.exit_code == 0, third.output
    updated = ProjectConfig.load(tmp_path)
    assert [source.name for source in updated.remote_instances] == ["lab", "prod", "staging"]
    assert updated.remote_instances[1:] == config.remote_instances
    assert updated.remote_instances[0].database is None


def test_named_remote_init_is_complete_repeatable_and_secret_free_without_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda **_kwargs: tmp_path / "catalog.sqlite3",
    )
    args = [
        "init",
        "--no-input",
        "--yes",
        "--odoo-bin",
        "/opt/odoo/odoo-bin",
        "--python",
        "python3",
        "--config",
        "odoo.conf",
        "--project",
        str(tmp_path),
        "--format",
        "json",
        "--remote",
        "staging",
        "https://staging.example",
        "main",
    ]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    source = ProjectConfig.load(tmp_path).remote_instances[0]
    assert source.database is None
    assert source.git_branch == "main"
    assert "remote-secret" not in result.output


@pytest.mark.parametrize(
    ("database_arg", "expected_database"),
    [((), None), (("staging",), "staging")],
)
def test_named_remote_init_is_complete_repeatable_and_secret_free_inline_syntax(
    tmp_path: Path, database_arg: tuple[str, ...], expected_database: str | None
) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "init",
            "--project",
            str(tmp_path),
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--config",
            "odoo.conf",
            "--remote=lab",
            "https://lab.example",
            *database_arg,
            "dev",
            "--run-arg",
            "--remote",
            "--dry-run",
            "--format=json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["result"]
    assert "--remote" in payload["default_run_args"]
    assert payload["remote_instances"][0]["database"] == expected_database
    assert not (tmp_path / ".odcli/project.toml").exists()


@pytest.mark.parametrize("remote_first", [False, True])
def test_init_remote_shorthand_preserves_option_values_and_dry_run(
    tmp_path: Path, remote_first: bool
) -> None:
    remote = ["--remote", "lab", "https://lab.example", "dev"]
    options = ["--run-arg", "--remote", "--format=json"]
    result = CliRunner().invoke(
        cli,
        [
            "init",
            "--project",
            str(tmp_path),
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--config",
            "odoo.conf",
            "--no-input",
            "--dry-run",
            *(remote + options if remote_first else options + remote),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["result"]
    assert payload["default_run_args"] == ["--remote"]
    assert payload["remote_instances"] == [
        {
            "name": "lab",
            "base_url": "https://lab.example",
            "database": None,
            "git_branch": "dev",
            "password_key": "ODCLI_REMOTE_LAB_MASTER_PASSWORD",
        }
    ]
    assert not (tmp_path / ".odcli").exists()


def test_named_remote_init_dry_run_is_inert_without_input(tmp_path: Path) -> None:
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        source_config=Path("odoo.conf"),
        remote_instances=(
            RemoteSourceConfig(
                name="staging",
                base_url="https://staging.example",
                database="staging",
                git_branch="main",
            ),
        ),
    )
    missing, _details = evaluate_init_completeness(
        project_root=tmp_path,
        config=config,
        local_config=False,
        postgres_image=None,
        existing_test_instance=None,
        dry_run=True,
        remote_database_names=None,
    )
    assert missing == []
    result = init_project(
        tmp_path,
        config,
        postgres_allocated=False,
        no_input=True,
        dry_run=True,
    )
    assert result["remote_password_keys"] == ["ODCLI_REMOTE_STAGING_MASTER_PASSWORD"]
    assert not (tmp_path / ".odcli" / "project.toml").exists()


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_explicit_test_instance_options_replace_existing_atomically(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    first = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--test-url",
            "http://127.0.0.1:18069",
            "--test-database",
            "remote_db",
            "--test-branch",
            "main",
            "--allow-partial",
        ],
    )
    assert first.exit_code == 0, first.output
    replacement = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        source_config=tmp_path / ".odcli" / "odoo.conf",
        postgres=PostgresProjectConfig(
            mode="compose",
            image="pgvector/pgvector:pg16",
            port=5468,
            user="odoo",
        ),
        test_instance=_test_instance(
            base_url="http://replacement.test:9070",
            database="replacement_db",
            git_branch="develop",
        ),
    )
    init_project(
        tmp_path,
        replacement,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
    )
    manifest = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "http://replacement.test:9070" in manifest
    assert 'database = "replacement_db"' in manifest
    assert 'git_branch = "develop"' in manifest
    assert "http://127.0.0.1:18069" not in manifest
    assert 'database = "remote_db"' not in manifest


def test_execute_init_drops_test_database_when_remote_list_has_one_name(tmp_path: Path) -> None:
    config = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        test_instance=_test_instance(
            base_url="http://example.test:8069",
            git_branch="main",
        ),
    )
    missing, details = evaluate_init_completeness(
        project_root=tmp_path,
        config=config,
        local_config=True,
        postgres_image="pgvector/pgvector:pg16",
        existing_test_instance=None,
        dry_run=False,
        remote_database_names=["only_db"],
    )
    assert "test_database" not in missing
    assert "only_db" in details["test_database"]


def test_init_command_resolves_remote_database_via_action_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        test_instance=_test_instance(
            base_url="http://example.test:8069",
            git_branch="main",
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.project_init.fetch_remote_database_names_for_init",
        lambda _cfg: ["only_db"],
    )
    command = init_project_command(tmp_path, config, postgres_allocated=False, local_config=True)
    step_ids = [step.step_id for step in command.plan.steps]
    assert _INIT_REMOTE_NAMES_ACTION_ID in step_ids
    missing, details = evaluate_init_completeness(
        project_root=tmp_path,
        config=config,
        local_config=True,
        postgres_image=None,
        existing_test_instance=None,
        dry_run=False,
        remote_database_names=["only_db"],
    )
    assert "test_database" not in missing
    assert "only_db" in details["test_database"]


def test_init_command_uses_database_resource_names_in_action_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        test_instance=_test_instance(
            base_url="http://example.test:8069",
            git_branch="main",
        ),
    )
    observed: list[str] = []

    def fake_names(self: DatabaseResource) -> tuple[str, ...]:
        observed.append(self.base_url)
        return ("resolved_db",)

    monkeypatch.setattr(DatabaseResource, "names", fake_names)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        no_input=True,
        allow_partial=False,
    )
    assert _INIT_REMOTE_NAMES_ACTION_ID in [step.step_id for step in command.plan.steps]
    with pytest.raises(InstanceConfigurationError, match="init_incomplete"):
        command.run()
    assert observed == ["http://example.test:8069"]


def test_dry_run_lists_test_database_missing_without_names_call(tmp_path: Path) -> None:
    config = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        test_instance=_test_instance(
            base_url="http://example.test:8069",
            git_branch="main",
        ),
    )
    missing, _ = evaluate_init_completeness(
        project_root=tmp_path,
        config=config,
        local_config=True,
        postgres_image="pgvector/pgvector:pg16",
        existing_test_instance=None,
        dry_run=True,
        remote_database_names=None,
    )
    assert "test_database" in missing
    command = init_project_command(
        tmp_path, config, postgres_allocated=False, local_config=True, dry_run=True
    )
    assert _INIT_REMOTE_NAMES_ACTION_ID in [step.step_id for step in command.plan.steps]


def test_external_postgres_requires_allow_partial_without_test_instance(tmp_path: Path) -> None:
    config = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        source_config=tmp_path / "odoo.conf",
    )
    missing, _ = evaluate_init_completeness(
        project_root=tmp_path,
        config=config,
        local_config=False,
        postgres_image=None,
        existing_test_instance=None,
        dry_run=False,
        remote_database_names=None,
    )
    assert "test_url" in missing
    with pytest.raises(InstanceConfigurationError, match="init_incomplete"):
        init_project(
            tmp_path,
            config,
            postgres_allocated=False,
            no_input=True,
            allow_partial=False,
        )


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_dry_run_shows_bootstrap_step_without_spawn(tmp_path: Path) -> None:
    config = _compose_config(tmp_path)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        dry_run=True,
    )
    assert any(step.step_id == "init.bootstrap.tmp" for step in command.plan.steps)
    assert any(step.step_id == "catalog.bootstrap.tmp.record" for step in command.plan.steps)


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_rejects_symlinked_data_dir_without_bootstrap_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    config = _compose_config(tmp_path)
    source_config = config.source_config
    assert source_config is not None
    source_config.parent.mkdir(parents=True)
    source_config.write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
    external = tmp_path.parent / "external-init-filestore"
    external.mkdir()
    data_directory = tmp_path / ".odcli" / "filestore"
    data_directory.symlink_to(external, target_is_directory=True)
    catalog_path = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=catalog_path)
    catalog.close()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda **_kwargs: catalog_path,
    )

    with pytest.raises(InstanceConfigurationError, match="symlink"):
        init_project_command(tmp_path, config, postgres_allocated=False, local_config=True)

    with sqlite3.connect(catalog_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM database_events WHERE event_type='bootstrapped'"
            ).fetchone()[0]
            == 0
        )


def test_auxiliary_restore_uses_bootstrap_database_in_argv(tmp_path: Path) -> None:
    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n")
    start_config = StartConfig(config_path=str(config_path), http_port=8069)
    bootstrap_plan = bootstrap_tmp_plan(
        command_prefix=("/usr/bin/python", "/opt/odoo/odoo-bin"),
        start_config=start_config,
        db_host="127.0.0.1",
        db_port=5468,
        db_user="odoo",
        db_password="secret",
        default_cwd=tmp_path,
    )
    assert f"--database {BOOTSTRAP_DATABASE}" in " ".join(bootstrap_plan.spawn_step.argv)
    assert "--database=__odcli_restore__" not in bootstrap_plan.spawn_step.argv
    assert "--db-filter=^$" not in bootstrap_plan.spawn_step.argv


def test_verify_project_owned_data_dir_rejects_symlink(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    external = tmp_path / "external"
    external.mkdir()
    data_dir = project_root / ".odcli" / "filestore"
    data_dir.parent.mkdir(parents=True)
    data_dir.symlink_to(external, target_is_directory=True)
    with pytest.raises(InstanceConfigurationError, match="regular directory"):
        verify_project_owned_data_dir(project_root, data_dir)


def test_bootstrap_tmp_command_records_odoo_argv(tmp_path: Path) -> None:
    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
    start_config = StartConfig(config_path=str(config_path), http_port=8069)

    def success(step: object) -> ProcessResult:
        return ProcessResult(
            argv=step.argv,  # type: ignore[attr-defined]
            returncode=0,
            stdout="",
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd", None),
            environment=cast(
                "tuple[tuple[str, str], ...]",
                getattr(step, "environment", ()),
            ),
        )

    results = {
        "init.bootstrap.tmp.probe": _failed_probe_result,
        "init.bootstrap.tmp.ready": _ready_probe_result,
    }

    def bootstrap_results(step: object) -> ProcessResult:
        return results.get(step.step_id, success)(step)  # type: ignore[attr-defined]

    executor = RecordingExecutor(result_factory=bootstrap_results)
    command = tmp_bootstrap_command(
        command_prefix=("/usr/bin/python3", "/opt/odoo/odoo-bin"),
        start_config=start_config,
        db_host="127.0.0.1",
        db_port=5468,
        db_user="odoo",
        db_password="secret",
        default_cwd=tmp_path,
        executor=executor,
    )
    assert [step.step_id for step in command.plan.steps] == [
        "init.bootstrap.tmp.verify",
        "init.bootstrap.tmp.probe",
        "init.bootstrap.tmp",
        "init.bootstrap.tmp.ready",
    ]
    assert command.run() is True
    assert [step.step_id for step in executor.executed] == [
        "init.bootstrap.tmp.probe",
        "init.bootstrap.tmp",
        "init.bootstrap.tmp.ready",
    ]
    spawn = next(step for step in executor.executed if step.step_id == "init.bootstrap.tmp")
    argv = spawn.argv
    assert "--database" in argv and "tmp" in argv
    assert "--init" in argv and "base" in argv
    assert "--stop-after-init" in argv


def test_bootstrap_valid_tmp_skips_odoo_spawn(tmp_path: Path) -> None:
    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
    start_config = StartConfig(config_path=str(config_path), http_port=8069)
    executor = RecordingExecutor(result_factory=_ready_probe_result)
    command = tmp_bootstrap_command(
        command_prefix=("/usr/bin/python3", "/opt/odoo/odoo-bin"),
        start_config=start_config,
        db_host="127.0.0.1",
        db_port=5468,
        db_user="odoo",
        db_password="secret",
        default_cwd=tmp_path,
        executor=executor,
    )
    assert command.run() is True
    assert executor.spawned == []
    assert any(step.step_id == "init.bootstrap.tmp.probe" for step in executor.executed)


def test_bootstrap_invalid_tmp_raises_init_bootstrap_failed(tmp_path: Path) -> None:
    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
    start_config = StartConfig(config_path=str(config_path), http_port=8069)

    def invalid_bootstrap_results(step: object) -> ProcessResult:
        step_id = step.step_id  # type: ignore[attr-defined]
        if step_id == "init.bootstrap.tmp.probe":
            return _failed_probe_result(step)
        if step_id == "init.bootstrap.tmp.ready":
            return _failed_probe_result(step)
        return ProcessResult(
            argv=step.argv,  # type: ignore[attr-defined]
            returncode=0,
            stdout="",
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd", None),
            environment=cast(
                "tuple[tuple[str, str], ...]",
                getattr(step, "environment", ()),
            ),
        )

    executor = RecordingExecutor(result_factory=invalid_bootstrap_results)
    command = tmp_bootstrap_command(
        command_prefix=("/usr/bin/python3", "/opt/odoo/odoo-bin"),
        start_config=start_config,
        db_host="127.0.0.1",
        db_port=5468,
        db_user="odoo",
        db_password="secret",
        default_cwd=tmp_path,
        executor=executor,
    )
    with pytest.raises(BootstrapFailedError, match=r"init_bootstrap_failed|base module"):
        command.run()
    assert sum(1 for step in executor.executed if step.step_id == "init.bootstrap.tmp") == 1


def test_init_bootstrap_failure_prevents_successful_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    catalog_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_: catalog_path
    )
    project_id = f"project_{repo_key(tmp_path, tmp_path / '.git')}"
    catalog = BackupCatalog(db_path=catalog_path)
    catalog._register_project(project_id, tmp_path, tmp_path / ".git")
    assert catalog.forget_project(project_id)
    catalog.close()

    def _noop_ensure_running(
        self: PostgresCluster,
        timeout: float = 60.0,
        *,
        temporary_path: Path | None = None,
        step_ids: object = None,
    ) -> None:
        return None

    def _fail_bootstrap(
        context: object,
        plan: object,
    ) -> BootstrapOutcome:
        raise BootstrapFailedError("tmp bootstrap failed: invalid database")

    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", _noop_ensure_running)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _fail_bootstrap,
    )
    config = _compose_config(tmp_path)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
    )
    with pytest.raises(BootstrapFailedError, match=r"init_bootstrap_failed|tmp bootstrap failed"):
        command.run()
    catalog = BackupCatalog(db_path=catalog_path)
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_compose_failure_keeps_forgotten_project_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    catalog_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_: catalog_path
    )
    project_id = f"project_{repo_key(tmp_path, tmp_path / '.git')}"
    catalog = BackupCatalog(db_path=catalog_path)
    catalog._register_project(project_id, tmp_path, tmp_path / ".git")
    assert catalog.forget_project(project_id)
    catalog.close()

    def fail_compose(_context: object, _followup: object) -> None:
        raise InstanceConfigurationError("compose init failed")

    monkeypatch.setattr(
        "odoo_instance_sdk.project_init._execute_compose_followup_phase", fail_compose
    )
    command = init_project_command(
        tmp_path,
        _compose_config(tmp_path),
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
    )
    with pytest.raises(InstanceConfigurationError, match="compose init failed"):
        command.run()
    catalog = BackupCatalog(db_path=catalog_path)
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


def test_init_bootstrap_publishes_observed_project_database_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.dbprep import bootstrap

    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    data_directory = root / ".odcli" / "filestore"
    data_directory.mkdir(parents=True)
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog._register_project(project_id, root, root / ".git")
    claim = catalog._ensure_postgres_cluster_pending(project_id, "compose", "volume")
    catalog._activate_postgres_cluster(claim.cluster_id, project_id, "compose", "volume")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda: tmp_path / "catalog.sqlite3"
    )
    monkeypatch.setattr(
        bootstrap,
        "run_bootstrap_tmp",
        lambda _context, _plan: BootstrapOutcome.CREATED,
    )
    cluster = SimpleNamespace(
        _project_id=project_id, endpoint_host="127.0.0.1", endpoint_port=15432
    )
    plan = bootstrap_tmp_plan(
        command_prefix=("python3", "odoo-bin"),
        start_config=StartConfig(db_user="odoo"),
        db_host="127.0.0.1",
        db_port=15432,
        db_user="odoo",
        db_password="secret",
        include_create=False,
        record_action=bootstrap.bootstrap_record_action(),
        capture_inventory=True,
    )
    followup = _BootstrapFollowup(
        cluster=cluster,  # type: ignore[arg-type]
        plan=plan,
        project_root=root,
        data_directory=data_directory,
        database_names=("odoo",),
    )
    context = MagicMock()
    context.process_prepared.side_effect = lambda step: ProcessResult(
        argv=step.argv,
        returncode=0,
        stdout="tmp\nodoo\npostgres\n",
        stderr="",
        duration=0.0,
        cwd=step.cwd,
        environment=step.environment,
    )
    _execute_bootstrap_followup_phase(context, followup)
    assert catalog._project_bound_database_names(
        project_id, claim.cluster_id, "127.0.0.1", 15432
    ) == ("odoo",)
    catalog.close()


def test_init_bootstrap_retry_publishes_observed_binding_when_tmp_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.dbprep import bootstrap

    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    data_directory = root / ".odcli" / "filestore"
    data_directory.mkdir(parents=True)
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog._register_project(project_id, root, root / ".git")
    claim = catalog._ensure_postgres_cluster_pending(project_id, "compose", "volume")
    catalog._activate_postgres_cluster(claim.cluster_id, project_id, "compose", "volume")
    catalog._record_database_bootstrapped(
        "127.0.0.1", 15432, "tmp", cluster_id=claim.cluster_id, data_directory=data_directory
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda: tmp_path / "catalog.sqlite3"
    )
    monkeypatch.setattr(
        bootstrap,
        "run_bootstrap_tmp",
        lambda _context, _plan: BootstrapOutcome.ALREADY_READY,
    )
    cluster = SimpleNamespace(
        _project_id=project_id, endpoint_host="127.0.0.1", endpoint_port=15432
    )
    plan = bootstrap_tmp_plan(
        command_prefix=("python3", "odoo-bin"),
        start_config=StartConfig(db_user="odoo"),
        db_host="127.0.0.1",
        db_port=15432,
        db_user="odoo",
        db_password="secret",
        include_create=False,
        record_action=bootstrap.bootstrap_record_action(),
        capture_inventory=True,
    )
    followup = _BootstrapFollowup(
        cluster=cluster,  # type: ignore[arg-type]
        plan=plan,
        project_root=root,
        data_directory=data_directory,
        database_names=("odoo",),
    )
    context = MagicMock()
    context.process_prepared.side_effect = lambda step: ProcessResult(
        argv=step.argv,
        returncode=0,
        stdout="tmp\nodoo\npostgres\n",
        stderr="",
        duration=0.0,
        cwd=step.cwd,
        environment=step.environment,
    )
    _execute_bootstrap_followup_phase(context, followup)
    assert catalog._project_bound_database_names(
        project_id, claim.cluster_id, "127.0.0.1", 15432
    ) == ("odoo",)
    catalog.close()


def test_deferred_init_materializes_password_for_database_inventory_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    (root / ".odcli").mkdir(parents=True)
    postgres_dir = tmp_path / "postgres"
    postgres_dir.mkdir()
    (postgres_dir / "postgres-password").write_text("captured-secret\n", encoding="utf-8")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_project_postgres_dir",
        lambda _project_id: postgres_dir,
    )
    config = ProjectConfig(
        repository_root=root,
        default_source_database="odoo",
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=15432),
    )
    compose, followup = _compose_followup_steps(root, config, dry_run=False, defer_actions=True)
    assert followup.database_names == ("tmp", "odoo")
    inventory = followup.plan.inventory_step
    assert inventory is not None and inventory.runtime_environment_factory is not None
    compose.password_state[0] = "runtime-secret"
    environment, _cleanup, secrets = inventory.runtime_environment_factory()
    assert dict(environment)["PGPASSWORD"] == "runtime-secret"
    assert secrets == ("runtime-secret",)


def test_first_run_triggers_bootstrap_when_no_valid_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.internal.dbprep.bootstrap import ensure_project_bootstrap_tmp
    from odoo_instance_sdk.internal.proc import RunContext

    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
    client = MagicMock()
    client.config.http_timeout_seconds = 10.0
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(config_path=str(config_path), http_port=8069, db_user="odoo"),
            command_prefix=("/usr/bin/python3", "/opt/odoo/odoo-bin"),
            default_cwd=tmp_path,
        ),
        _client=client,
    )
    cluster = MagicMock()
    cluster.owned = True
    cluster._project_id = "project"
    cluster.endpoint_host = "127.0.0.1"
    cluster.endpoint_port = 5468
    instance._postgres_cluster = cluster
    calls: list[bool] = []

    def _track_bootstrap(
        context: object,
        plan: object,
    ) -> BootstrapOutcome:
        calls.append(True)
        _skip_bootstrap_process_steps(context, plan)
        return BootstrapOutcome.ALREADY_READY

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _track_bootstrap,
    )
    context = MagicMock(spec=RunContext)
    context.action = MagicMock()
    context.complete_action = MagicMock()
    context.skip = MagicMock()
    ensure_project_bootstrap_tmp(instance, context)
    assert calls == [True]
    context.action.assert_called_once_with("init.bootstrap.tmp.verify")


def test_first_foreground_run_captures_bootstrap_steps(tmp_path: Path) -> None:
    from odoo_instance_sdk.config import InstanceConfig

    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(config_path=str(config_path), http_port=8069, db_user="odoo"),
            command_prefix=("/usr/bin/python3", "/opt/odoo/odoo-bin"),
            default_cwd=tmp_path,
        ),
        _client=MagicMock(),
    )
    cluster = MagicMock()
    cluster.owned = True
    cluster.endpoint_host = "127.0.0.1"
    cluster.endpoint_port = 5468
    instance._postgres_cluster = cluster

    command = instance.run_foreground_command(args=("-u", "comerta_base", "--stop-after-init"))

    assert tuple(
        step.step_id for step in command.plan.steps if step.step_id.startswith("init.bootstrap.tmp")
    ) == (
        "init.bootstrap.tmp.verify",
        "init.bootstrap.tmp.probe",
        "init.bootstrap.tmp",
        "init.bootstrap.tmp.ready",
    )


@dataclass(slots=True)
class _SelfContainedRestoreSetup:
    project: ProjectConfig
    client: MagicMock
    backup: Backup
    remote_database: str
    auxiliary: OdooInstance
    session: AuxiliaryRestoreSession
    executor: RecordingExecutor
    bootstrap_spawned: list[bool]
    restore: MagicMock


def _prepare_self_contained_restore_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, odoo_version: str
) -> _SelfContainedRestoreSetup:
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
    from odoo_instance_sdk.internal.project_manifest import write_manifest
    from odoo_instance_sdk.models import BackupFormat
    from tests.fixtures import make_backup

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_name = source\n"
        "admin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo_bin = tmp_path / "odoo-bin"
    odoo_bin.write_text("#!/bin/sh\n")
    odoo_bin.chmod(0o755)
    remote_database = f"remote_test_{odoo_version.replace('.', '_')}"
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo_bin,
        source_config=source,
        default_source_database="old",
        managed_filestore=Path(".odcli/filestore"),
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5468, user="odoo"),
        test_instance=RemoteTestInstanceConfig(
            base_url="https://example.test",
            database=remote_database,
            git_branch=f"main-{odoo_version}",
        ),
    )
    write_manifest(tmp_path, project)
    backup_file = tmp_path / "remote.zip"
    backup_file.write_bytes(b"zip-backup")
    backup = make_backup(
        source_base_url="https://example.test",
        database_name=remote_database,
        path=str(backup_file),
        filename=backup_file.name,
        size_bytes=backup_file.stat().st_size,
        format=BackupFormat.ZIP,
        filestore_requested=True,
        source_git_branch=f"develop-{odoo_version}",
    )

    client = MagicMock()
    auxiliary = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(config_path=str(source), http_port=8069),
            command_prefix=(str(python), str(odoo_bin)),
            default_cwd=tmp_path,
        ),
        _client=client,
    )
    local = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(config_path=str(source), http_port=8069),
        ),
        _client=client,
    )
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    client.instance.from_project.return_value = auxiliary
    client.instance.from_config.return_value = local
    client.instance.return_value = remote
    client.unregister_process.return_value = (None, None)

    session = auxiliary_restore_session(auxiliary)
    handle = ProcessHandle(
        process=MagicMock(),
        argv=session.start_step.argv,
        process_group_id=123,
        session_id=123,
        inherited_stdio=False,
    )
    executor = RecordingExecutor(handles={session.start_step.step_id: handle})
    bootstrap_spawned: list[bool] = []

    def _track_bootstrap(
        context: object,
        plan: object,
    ) -> BootstrapOutcome:
        bootstrap_spawned.append(True)
        _skip_bootstrap_process_steps(context, plan)
        return BootstrapOutcome.ALREADY_READY

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _track_bootstrap,
    )
    PostgresCluster._from_config(project, repository_root=tmp_path, compose_runner=None)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        lambda _config: None,
    )
    monkeypatch.setattr(
        OdooInstance,
        "wait_ready",
        lambda _self, _proc, *, timeout, version_info=False, database_manager=False: MagicMock(
            ok=True
        ),
    )
    restore = MagicMock()
    monkeypatch.setattr(DatabaseResource, "restore", restore)
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.status_code = 303
    response.headers = {"location": "/web/database/manager"}
    response.json.return_value = {"result": ["source"]}

    @contextlib.contextmanager
    def fake_http(_self: DatabaseResource, timeout: float | None = None) -> Any:
        del timeout
        yield MagicMock(post=MagicMock(return_value=response))

    monkeypatch.setattr(DatabaseResource, "_http", fake_http)

    def command_factory(
        project_path: Path,
        *,
        options: DatabaseRefreshOptions,
        **kwargs: object,
    ) -> object:
        return DatabasePreparationCoordinator(client).refresh_database_command(
            project_path,
            options=options,
            executor=executor,
            admin_password=cast("str | None", kwargs.get("admin_password")),
        )

    client.environments.refresh_database_command.side_effect = command_factory
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", "https://example.test:443")
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", lambda *a, **k: None)
    return _SelfContainedRestoreSetup(
        project=project,
        client=client,
        backup=backup,
        remote_database=remote_database,
        auxiliary=auxiliary,
        session=session,
        executor=executor,
        bootstrap_spawned=bootstrap_spawned,
        restore=restore,
    )


def _run_self_contained_init(tmp_path: Path, setup: _SelfContainedRestoreSetup) -> None:
    init_command = init_project_command(
        tmp_path,
        setup.project,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
    )
    step_ids = [step.step_id for step in init_command.plan.steps]
    assert "init.bootstrap.tmp" in step_ids
    assert "init.bootstrap.tmp.probe" in step_ids
    assert "catalog.bootstrap.tmp.record" in step_ids
    init_command.run()


@pytest.mark.parametrize("odoo_version", ["13.0", "19.0"])
@pytest.mark.usefixtures("stub_psql_resolution")
def test_self_contained_init_bootstraps_before_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, odoo_version: str
) -> None:
    setup = _prepare_self_contained_restore_setup(tmp_path, monkeypatch, odoo_version)
    _run_self_contained_init(tmp_path, setup)
    assert setup.bootstrap_spawned == [True]


@pytest.mark.parametrize("odoo_version", ["13.0", "19.0"])
@pytest.mark.usefixtures("stub_psql_resolution")
def test_self_contained_restore_switches_default_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, odoo_version: str
) -> None:
    setup = _prepare_self_contained_restore_setup(tmp_path, monkeypatch, odoo_version)
    _run_self_contained_init(tmp_path, setup)
    result = CliRunner().invoke(cli, ["db", "refresh", "--restore", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result"]["default_switched"] is True
    assert setup.backup.database_name == setup.remote_database
    assert setup.backup.source_git_branch == f"develop-{odoo_version}"
    setup.restore.assert_called_once()
    assert [step.step_id for step in setup.executor.spawned] == [setup.session.start_step.step_id]
    setup.client.unregister_process.assert_called_once()


@pytest.mark.usefixtures("stub_compose_init_followup")
@pytest.mark.usefixtures("stub_psql_resolution")
def test_init_master_env_db_refresh_dry_run_flow(tmp_path: Path) -> None:
    odoo_bin = tmp_path / "odoo-bin"
    odoo_bin.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    odoo_bin.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    init_result = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            str(odoo_bin),
            "--python",
            "python3",
            "--project",
            str(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
            "--database",
            "source",
            "--test-url",
            "http://127.0.0.1:18069",
            "--test-database",
            "remote_db",
            "--test-branch",
            "main",
            "--local-config",
            "--allow-partial",
        ],
    )
    assert init_result.exit_code == 0, init_result.output
    env_path = tmp_path / ".odcli" / ".env"
    env_path.write_text(
        env_path.read_text().replace(
            "ODCLI_TEST_MASTER_PASSWORD=",
            "ODCLI_TEST_MASTER_PASSWORD=remote-secret",
        )
    )
    refresh_result = runner.invoke(
        cli,
        [
            "--project",
            str(tmp_path),
            "db",
            "refresh",
            "--restore",
            "--dry-run",
            "--format",
            "json",
        ],
    )
    assert refresh_result.exit_code == 0, refresh_result.output
    payload = json.loads(refresh_result.output)
    assert payload["dry_run"] is True
