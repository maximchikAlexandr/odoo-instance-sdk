from __future__ import annotations

import os
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.proc import RecordingExecutor
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project_init import init_project_command


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
