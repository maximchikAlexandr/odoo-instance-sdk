from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RecordingExecutor
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.project_init import init_project_command

# This is deliberately kept next to the focused matrix rather than in the
# private E2E repository.  It makes the 27 delta scenarios auditable even when
# that repository is unavailable to the implementation workspace.
SCENARIO_EVIDENCE: dict[str, str] = {
    "project-init/omitted-venv": "test_integrated_init_omission_preserves_legacy_plan",
    "project-init/fresh-ordered-preparation": "test_integrated_init_captures_manifest_before_shared_preparation",
    "project-init/missing-python": "test_integrated_init_requires_python_before_manifest_mutation",
    "project-init/prepared-interpreter-bootstrap": "test_integrated_init_captures_concrete_bootstrap_interpreter",
    "project-init/empty-requirements": "test_integrated_init_empty_inputs_skip_dependency_steps",
    "project-init/create-or-validation-failure": "test_integrated_init_process_failures_stop_before_persistence",
    "project-init/compile-or-sync-failure": "test_integrated_init_process_failures_stop_before_persistence",
    "project-init/publication-failure": "test_integrated_init_publication_failure_restores_manifest",
    "project-init/bootstrap-failure": "tests/unit/test_init_self_contained.py::test_init_bootstrap_failure_prevents_successful_result",
    "project-init/identical-retry": "tests/unit/test_init_self_contained.py::test_complete_compose_retry_skips_tmp_recreation",
    "project-init/damaged-venv": "tests/unit/project/test_prepare_venv.py::test_prepare_existing_empty_venv_persists_interpreter_after_success",
    "project-init/missing-venv-changed-selector": "test_integrated_init_missing_venv_captures_selector_and_dependencies",
    "project-init/existing-venv-changed-selector": "test_integrated_init_existing_venv_is_not_recreated",
    "project-init/headless-replacement-confirmation": "test_integrated_init_headless_changed_selector_requires_yes",
    "development-environment/missing-venv-created": "test_integrated_init_missing_venv_captures_selector_and_dependencies",
    "development-environment/existing-venv-refreshed": "test_integrated_init_existing_venv_is_not_recreated",
    "development-environment/empty-inputs": "test_integrated_init_empty_inputs_skip_dependency_steps",
    "development-environment/process-failure-state": "test_integrated_init_process_failures_stop_before_persistence",
    "development-environment/persistence-rollback": "test_integrated_init_publication_failure_restores_manifest",
    "development-environment/shared-owner": "test_integrated_init_shares_standalone_preparation_steps",
    "development-environment/dry-run": "test_integrated_init_dry_run_is_inert_in_all_output_modes",
    "cli-odcli/omission-compatibility": "test_integrated_init_omission_preserves_legacy_plan",
    "cli-odcli/delegation": "test_init_help_exposes_opt_in_venv",
    "cli-odcli/dry-run-output": "test_integrated_init_dry_run_is_inert_in_all_output_modes",
    "cli-odcli/existing-refresh": "test_integrated_init_existing_manifest_refreshes",
    "cli-odcli/failure-transports": "test_integrated_init_failure_uses_all_output_modes",
    "cli-odcli/standalone-recovery": "tests/unit/project/test_prepare_venv.py::test_prepare_validates_existing_venv_before_refresh",
}


def _project(tmp_path: Path) -> ProjectConfig:
    (tmp_path / ".odcli").mkdir()
    venv = tmp_path / ".venv"
    interpreter = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text(
        "home = /runtime\nimplementation = CPython\nversion_info = 3.12\nuv = 0.13.0\n",
        encoding="utf-8",
    )
    return ProjectConfig(repository_root=tmp_path, python="3.12")


def _bare_project(tmp_path: Path, *, requirements: bool = False) -> ProjectConfig:
    (tmp_path / ".odcli").mkdir()
    if requirements:
        (tmp_path / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    return ProjectConfig(
        repository_root=tmp_path,
        python="3.12",
        requirements=("requirements.txt",) if requirements else (),
    )


def _materialize_venv(step: PreparedStep) -> None:
    venv = Path(step.argv[2])
    interpreter = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.write_text("placeholder\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text(
        "home = /runtime\nimplementation = CPython\nversion_info = 3.12\nuv = 0.13.0\n",
        encoding="utf-8",
    )


def _process_result(step: PreparedStep, *, returncode: int = 0, stderr: str = "") -> ProcessResult:
    return ProcessResult(
        argv=step.argv,
        returncode=returncode,
        stdout="",
        stderr=stderr,
        duration=0.0,
        cwd=step.cwd,
        environment=step.environment,
    )


def _fake_preparation_result(
    failed_step: str | None = None,
) -> Callable[[PreparedStep], ProcessResult]:
    def result(step: PreparedStep) -> ProcessResult:
        if step.step_id == "prepare-venv.venv":
            _materialize_venv(step)
        if "compile" in step.step_id:
            Path(step.argv[-1]).write_text("prepared-lock\n", encoding="utf-8")
        return _process_result(
            step,
            returncode=1 if step.step_id == failed_step else 0,
            stderr=f"{failed_step} failed" if step.step_id == failed_step else "",
        )

    return result


def test_scenario_evidence_covers_every_delta_case() -> None:
    assert len(SCENARIO_EVIDENCE) == 27
    assert all(selector for selector in SCENARIO_EVIDENCE.values())


def test_integrated_init_captures_manifest_before_shared_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )

    assert [step.step_id for step in command.plan.steps] == [
        "init",
        "prepare-venv.validate",
        "prepare-venv.origin",
        "prepare-venv.manifest",
    ]
    result = command.run()

    interpreter = str((tmp_path / ".venv" / "bin" / "python").resolve())
    assert result["interpreter"] == interpreter
    assert result["requested_python"] == "3.12"
    assert ProjectConfig.load(tmp_path).python == Path(interpreter)
    assert [step.step_id for step in executor.executed] == ["prepare-venv.validate"]


def test_integrated_init_omission_preserves_legacy_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path, config, postgres_allocated=False, allow_partial=True, no_input=True
    )

    assert [step.step_id for step in command.plan.steps] == ["init"]
    command.run()
    assert not executor.executed


def test_integrated_init_missing_venv_captures_selector_and_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _bare_project(tmp_path, requirements=True)
    executor = RecordingExecutor(result_factory=_fake_preparation_result())
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )

    assert [step.step_id for step in command.plan.steps] == [
        "init",
        "prepare-venv.venv",
        "prepare-venv.validate",
        "prepare-venv.dependencies.compile",
        "prepare-venv.dependencies.install",
        "prepare-venv.origin",
        "prepare-venv.manifest",
        "prepare-venv.dependencies.lock",
    ]
    create = next(
        step for step in command.plan.process_steps if step.step_id == "prepare-venv.venv"
    )
    assert create.argv[-2:] == ("--python", "3.12")
    result = command.run()

    assert result["requested_python"] == "3.12"
    assert ProjectConfig.load(tmp_path).python == (tmp_path / ".venv" / "bin" / "python").resolve()
    assert (tmp_path / ".odcli" / "requirements.lock").read_text() == "prepared-lock\n"


def test_integrated_init_existing_venv_is_not_recreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    changed = ProjectConfig(repository_root=tmp_path, python="3.13")
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path,
        changed,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )

    assert "prepare-venv.venv" not in [step.step_id for step in command.plan.steps]
    result = command.run()
    assert result["requested_python"] == "3.13"
    assert result["interpreter"] == str((tmp_path / ".venv" / "bin" / "python").resolve())
    assert [step.step_id for step in executor.executed] == ["prepare-venv.validate"]


def test_integrated_init_empty_inputs_skip_dependency_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )
    assert not any("dependencies" in step.step_id for step in command.plan.steps)
    command.run()
    assert not (tmp_path / ".odcli" / "requirements.lock").exists()


@pytest.mark.parametrize(
    "failed_step",
    [
        "prepare-venv.venv",
        "prepare-venv.validate",
        "prepare-venv.dependencies.compile",
        "prepare-venv.dependencies.install",
    ],
)
def test_integrated_init_process_failures_stop_before_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_step: str
) -> None:
    config = _bare_project(tmp_path, requirements="dependencies" in failed_step)
    executor = RecordingExecutor(result_factory=_fake_preparation_result(failed_step))
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )
    with pytest.raises(ConfigError):
        command.run()

    assert executor.executed[-1].step_id == failed_step
    assert not (tmp_path / ".odcli" / "primary-venv-origin.json").exists()
    assert ProjectConfig.load(tmp_path).python == "3.12"


def test_integrated_init_publication_failure_restores_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    def fail_publish(_stage: object) -> None:
        raise OSError("injected integrated origin publication failure")

    monkeypatch.setattr("odoo_instance_sdk.project_init.publish_origin", fail_publish)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )
    with pytest.raises(OSError, match="integrated origin publication"):
        command.run()

    assert ProjectConfig.load(tmp_path).python == "3.12"
    assert not (tmp_path / ".odcli" / "primary-venv-origin.json").exists()


def test_integrated_init_captures_concrete_bootstrap_interpreter(tmp_path: Path) -> None:
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="3.12",
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=55432),
    )
    (tmp_path / ".odcli").mkdir()
    venv = tmp_path / ".venv"
    interpreter = venv / "bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("version_info = 3.12\n", encoding="utf-8")

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        local_config=True,
        no_input=True,
        venv=".venv",
    )
    bootstrap = next(
        step for step in command.plan.process_steps if step.step_id == "init.bootstrap.tmp"
    )
    assert bootstrap.argv[0] == str(interpreter.resolve())
    assert bootstrap.argv[0] != "3.12"


def test_integrated_init_shares_standalone_preparation_steps(tmp_path: Path) -> None:
    config = _project(tmp_path)
    integrated = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )
    from odoo_instance_sdk.project_init import prepare_venv_command

    standalone = prepare_venv_command(config, python="3.12", venv=".venv")
    assert integrated.plan.steps[1:] == standalone.plan.steps


@pytest.mark.parametrize("output_mode", ["rich", "json", "toon"])
def test_integrated_init_dry_run_is_inert_in_all_output_modes(
    tmp_path: Path, output_mode: str
) -> None:
    result = CliRunner().invoke(
        __import__("odoo_instance_sdk.cli", fromlist=["cli"]).cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "3.12",
            "--venv",
            ".venv",
            "--dry-run",
            "--format",
            output_mode,
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "dry_run" in result.output.lower() or "dry run" in result.output.lower()
    assert not (tmp_path / ".odcli" / "project.toml").exists()
    assert not (tmp_path / ".venv").exists()
    if output_mode == "json":
        assert json.loads(result.output)["dry_run"] is True
    elif output_mode == "toon":
        from toon import DecodeOptions, decode

        assert decode(result.output, DecodeOptions(indent=2, strict=True))["dry_run"] is True


def test_integrated_init_existing_manifest_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".odcli").mkdir()
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="3.12",
    )
    write_manifest(tmp_path, config)
    venv = tmp_path / ".venv"
    interpreter = venv / "bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("version_info = 3.12\n", encoding="utf-8")
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    result = CliRunner().invoke(
        __import__("odoo_instance_sdk.cli", fromlist=["cli"]).cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "3.12",
            "--venv",
            ".venv",
            "--format",
            "json",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True
    assert [step.step_id for step in executor.executed] == ["prepare-venv.validate"]


def test_integrated_init_headless_changed_selector_requires_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".odcli").mkdir()
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="3.12",
    )
    write_manifest(tmp_path, config)
    result = CliRunner().invoke(
        __import__("odoo_instance_sdk.cli", fromlist=["cli"]).cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "3.13",
            "--venv",
            ".venv",
            "--format",
            "json",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "manifest exists and differs" in result.output
    assert ProjectConfig.load(tmp_path).python == "3.12"


@pytest.mark.parametrize("output_mode", ["rich", "json", "toon"])
def test_integrated_init_failure_uses_all_output_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_mode: str
) -> None:
    executor = RecordingExecutor(result_factory=_fake_preparation_result("prepare-venv.venv"))
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)
    result = CliRunner().invoke(
        __import__("odoo_instance_sdk.cli", fromlist=["cli"]).cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "3.12",
            "--venv",
            ".venv",
            "--format",
            output_mode,
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "prepare-venv.venv" in result.output
    assert "ok: true" not in result.output.lower()


def test_integrated_init_requires_python_before_manifest_mutation(tmp_path: Path) -> None:
    (tmp_path / ".odcli").mkdir()
    config = ProjectConfig(repository_root=tmp_path)

    with pytest.raises(ConfigError, match="effective Python selector"):
        init_project_command(
            tmp_path,
            config,
            postgres_allocated=False,
            allow_partial=True,
            no_input=True,
            venv=".venv",
        )

    assert not (tmp_path / ".odcli" / "project.toml").exists()


def test_init_help_exposes_opt_in_venv() -> None:
    from click.testing import CliRunner

    from odoo_instance_sdk.cli import cli

    result = CliRunner().invoke(cli, ["init", "--help"])
    assert result.exit_code == 0, result.output
    assert "--venv" in result.output


def test_integrated_init_dry_run_is_inert_and_contains_preparation_plan(tmp_path: Path) -> None:
    import json

    from click.testing import CliRunner

    from odoo_instance_sdk.cli import cli

    result = CliRunner().invoke(
        cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "3.12",
            "--venv",
            ".venv",
            "--dry-run",
            "--format",
            "json",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    plan_ids = [step["step_id"] for step in document["result"]["plan"]["steps"]]
    assert plan_ids[:2] == ["init", "prepare-venv.venv"]
    assert not (tmp_path / ".odcli" / "project.toml").exists()
    assert not (tmp_path / ".venv").exists()
