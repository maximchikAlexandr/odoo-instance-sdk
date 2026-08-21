from __future__ import annotations

import json
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import VscodeImportError
from odoo_instance_sdk.internal.vscode_import import import_vscode_launch

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "comerta-launch.json"


def test_selects_odoo_profile_not_first_node() -> None:
    result = import_vscode_launch(_FIXTURE, launch_name="Odoo comerta", no_input=True)
    assert result.config.odoo_bin is not None
    assert result.config.odoo_bin.name == "odoo-bin"
    assert result.report.source_profile == "Odoo comerta"


def test_imports_external_paths_and_dev_mode() -> None:
    result = import_vscode_launch(_FIXTURE, launch_name="Odoo comerta", no_input=True)
    assert result.config.python == Path("/opt/comerta/venv/bin/python")
    assert result.config.default_source_database == "CMRT-361_1"
    assert result.config.preferred_http_port == 8068
    assert result.config.default_run_args == ("--dev=qweb,xml",)


def test_drops_u_comerta_base() -> None:
    result = import_vscode_launch(_FIXTURE, launch_name="Odoo comerta", no_input=True)
    assert "-u comerta_base" in result.report.dropped_args
    assert result.config.default_run_args == ("--dev=qweb,xml",)


def test_ignored_prelaunch_and_envfile() -> None:
    result = import_vscode_launch(_FIXTURE, launch_name="Odoo comerta", no_input=True)
    assert result.report.ignored_pre_launch_task is True
    assert result.report.ignored_env_file is True


def test_unresolved_variable_error(tmp_path: Path) -> None:
    launch = tmp_path / "launch.json"
    launch.write_text(
        json.dumps(
            {
                "configurations": [
                    {
                        "name": "Bad",
                        "type": "debugpy",
                        "request": "launch",
                        "program": "${workspaceFolder}/odoo-bin",
                        "python": "${env:PYTHON_PATH}",
                        "args": ["-c", "./odoo.conf"],
                    }
                ]
            }
        )
    )
    with pytest.raises(VscodeImportError, match="Unsupported variable"):
        import_vscode_launch(launch, launch_name="Bad", no_input=True)


def test_multiple_candidates_require_launch_name_in_no_input(tmp_path: Path) -> None:
    launch = tmp_path / "launch.json"
    launch.write_text(
        json.dumps(
            {
                "configurations": [
                    {
                        "name": "Odoo one",
                        "type": "debugpy",
                        "request": "launch",
                        "program": "${workspaceFolder}/odoo-bin",
                        "args": ["-d", "db1"],
                    },
                    {
                        "name": "Odoo two",
                        "type": "debugpy",
                        "request": "launch",
                        "program": "${workspaceFolder}/odoo-bin",
                        "args": ["-d", "db2"],
                    },
                ]
            }
        )
    )
    with pytest.raises(VscodeImportError, match="Multiple"):
        import_vscode_launch(launch, no_input=True)


def test_workspace_folder_resolved(tmp_path: Path) -> None:
    vscode_dir = tmp_path / ".vscode"
    vscode_dir.mkdir()
    launch = vscode_dir / "launch.json"
    launch.write_text(
        json.dumps(
            {
                "configurations": [
                    {
                        "name": "Odoo",
                        "type": "debugpy",
                        "request": "launch",
                        "program": "${workspaceFolder}/odoo-bin",
                        "cwd": "${workspaceFolder}",
                        "args": ["-c", "./odoo.conf"],
                    }
                ]
            }
        )
    )
    result = import_vscode_launch(launch, launch_name="Odoo", no_input=True)
    assert result.config.odoo_bin == tmp_path / "odoo-bin"
    assert result.config.runtime_cwd == Path(".")
