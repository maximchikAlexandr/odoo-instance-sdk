from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RecordingExecutor
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project_init import prepare_venv_command


def _result(step: PreparedStep, returncode: int = 0, stderr: str = "") -> ProcessResult:
    return ProcessResult(
        argv=step.argv,
        returncode=returncode,
        stdout="",
        stderr=stderr,
        duration=0.0,
        cwd=step.cwd,
        environment=step.environment,
    )


def _project(tmp_path: Path, *, requirements: bool = False) -> Path:
    (tmp_path / ".odcli").mkdir()
    if requirements:
        (tmp_path / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    config = ProjectConfig(
        repository_root=tmp_path,
        requirements=("requirements.txt",) if requirements else (),
    )
    (tmp_path / ".odcli" / "project.toml").write_text(config.to_manifest(), encoding="utf-8")
    return tmp_path


def test_prepare_existing_empty_venv_persists_interpreter_after_success(tmp_path: Path) -> None:
    (tmp_path / ".odcli").mkdir()
    interpreter = tmp_path / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder", encoding="utf-8")
    (tmp_path / ".odcli" / "project.toml").write_text(
        ProjectConfig(repository_root=tmp_path).to_manifest(), encoding="utf-8"
    )

    command = prepare_venv_command(tmp_path, python="3.12", venv=".venv")
    assert "prepare-venv.validate" in [step.step_id for step in command.plan.steps]
    with pytest.raises(ConfigError, match="valid virtual environment"):
        command.run()

    assert ProjectConfig.load(tmp_path).python is None


def test_prepare_validates_existing_venv_before_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    interpreter = project / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder", encoding="utf-8")
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    result = prepare_venv_command(project, python="3.12", venv=".venv").run()

    assert result["interpreter"] == str(interpreter.resolve())
    assert [step.step_id for step in executor.executed] == ["prepare-venv.validate"]
    assert ProjectConfig.load(project).python == interpreter.resolve()


@pytest.mark.parametrize("failed_step", ["compile", "install"])
def test_prepare_failure_keeps_manifest_and_existing_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_step: str
) -> None:
    project = _project(tmp_path, requirements=True)
    interpreter = project / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder", encoding="utf-8")
    lock = project / ".odcli" / "requirements.lock"
    lock.write_text("old lock\n", encoding="utf-8")
    lock.with_name("requirements.lock.prepare").write_text("stale lock\n", encoding="utf-8")
    manifest_before = (project / ".odcli" / "project.toml").read_bytes()

    def fail_compile(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        if "compile" in prepared.argv:
            Path(prepared.argv[-1]).write_text("new lock\n", encoding="utf-8")
        return _result(
            prepared,
            1 if failed_step in prepared.step_id else 0,
            f"{failed_step} failed",
        )

    executor = RecordingExecutor(result_factory=fail_compile)
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    with pytest.raises(ConfigError, match=failed_step):
        prepare_venv_command(project, python="3.12", venv=".venv").run()

    assert (project / ".odcli" / "project.toml").read_bytes() == manifest_before
    assert lock.read_text(encoding="utf-8") == "old lock\n"
    assert not (project / ".odcli" / "requirements.lock.prepare").exists()


def test_prepare_publishes_lock_only_after_install_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, requirements=True)
    interpreter = project / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder", encoding="utf-8")

    def write_compile_output(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        if "compile" in prepared.argv:
            Path(prepared.argv[-1]).write_text("new lock\n", encoding="utf-8")
        return _result(prepared)

    executor = RecordingExecutor(result_factory=write_compile_output)
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)
    prepare_venv_command(project, python="3.12", venv=".venv").run()

    assert (project / ".odcli" / "requirements.lock").read_text(encoding="utf-8") == "new lock\n"
    assert not (project / ".odcli" / "requirements.lock.prepare").exists()


def test_prepare_plan_projects_windows_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    from odoo_instance_sdk.resources.environment import checkout_artifacts

    venv = project / ".venv"
    artifacts = cast("Any", checkout_artifacts)
    monkeypatch.setattr(artifacts.os, "name", "nt")
    assert checkout_artifacts._owned_python_executable(venv).endswith(".venv/Scripts/python.exe")


def test_prepare_rejects_stale_manifest_before_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path)
    command = prepare_venv_command(project, python="3.12", venv=".venv")
    (project / ".odcli" / "project.toml").write_text(
        ProjectConfig(repository_root=project, python="other").to_manifest(), encoding="utf-8"
    )
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    with pytest.raises(ConfigError, match="manifest changed"):
        command.run()

    assert not executor.executed
    assert ProjectConfig.load(project).python == "other"
