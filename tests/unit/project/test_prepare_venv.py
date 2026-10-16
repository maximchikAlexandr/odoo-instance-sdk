from __future__ import annotations

from pathlib import Path

from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project_init import prepare_venv_command


def test_prepare_existing_empty_venv_persists_interpreter_after_success(tmp_path: Path) -> None:
    (tmp_path / ".odcli").mkdir()
    interpreter = tmp_path / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder", encoding="utf-8")
    (tmp_path / ".odcli" / "project.toml").write_text(
        ProjectConfig(repository_root=tmp_path).to_manifest(), encoding="utf-8"
    )

    command = prepare_venv_command(tmp_path, python="3.12", venv=".venv")
    assert [step.step_id for step in command.plan.steps] == ["prepare-venv.manifest"]
    result = command.run()

    assert result["interpreter"] == str(interpreter.resolve())
    assert ProjectConfig.load(tmp_path).python == interpreter.resolve()
