from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.internal.automation import verify_deps_command
from odoo_instance_sdk.internal.database_preparation import _remote_password
from odoo_instance_sdk.internal.pg.builder import build_psql_specification
from odoo_instance_sdk.internal.proc import PreparedCommand, PreparedStep, prepared_step
from odoo_instance_sdk.internal.process_env import captured_child_environment
from odoo_instance_sdk.internal.project_env import (
    ProjectEnvironmentError,
    effective_project_environment,
    load_project_environment,
)
from odoo_instance_sdk.internal.vscode_generate import build_launch_profile, launch_json
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance import _build_shell_script_step
from odoo_instance_sdk.resources.postgres import PostgresCluster

if TYPE_CHECKING:
    from odoo_instance_sdk.commands.context import RuntimeView


def _prepared_steps(command: object) -> tuple[PreparedStep, ...]:
    import odoo_instance_sdk.execution as execution_module

    prepared = cast("PreparedCommand[object]", execution_module._COMMANDS[id(command)])
    return tuple(step for step in prepared.steps if isinstance(step, PreparedStep))


def _write_env(root: Path, content: bytes | str, *, mode: int = 0o600) -> Path:
    directory = root / ".odcli"
    directory.mkdir()
    path = directory / ".env"
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    os.chmod(path, mode)
    return path


def test_project_env_accepts_exact_grammar_and_bom(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "\ufeff\n"
        "  # comment\n"
        "PLAIN =  internal  whitespace  # is data  \n"
        "SINGLE='literal value'\n"
        'DOUBLE="line\\nreturn\\rtab\\t slash\\\\ quote\\""\n'
        "EMPTY=\n",
    )

    assert dict(load_project_environment(tmp_path)) == {
        "PLAIN": "internal  whitespace  # is data",
        "SINGLE": "literal value",
        "DOUBLE": 'line\nreturn\rtab\t slash\\ quote"',
        "EMPTY": "",
    }


@pytest.mark.parametrize(
    ("content", "line"),
    [
        ("export KEY=value\n", 1),
        ("KEY=one\nKEY=two\n", 2),
        ("KEY='unterminated\n", 1),
        ('KEY="unterminated\n', 1),
        ('KEY="bad\\q"\n', 1),
        ('KEY="ok" trailing\n', 1),
        ("KEY='a\\b'\n", 1),
        ("KEY=$(command)\n", 1),
        ("KEY=`command`\n", 1),
        ("KEY=a\rb\n", 1),
        ("1KEY=value\n", 1),
    ],
)
def test_project_env_rejects_unsupported_forms(tmp_path: Path, content: str, line: int) -> None:
    path = _write_env(tmp_path, content)

    with pytest.raises(ProjectEnvironmentError, match=rf"{path}:{line}:") as error:
        load_project_environment(tmp_path)
    assert "value" not in str(error.value)
    assert "command" not in str(error.value)


def test_project_env_rejects_nul_invalid_utf8_and_insecure_permissions(tmp_path: Path) -> None:
    path = _write_env(tmp_path, b"KEY=secret\x00\n")
    with pytest.raises(ProjectEnvironmentError, match=rf"{path}:1:"):
        load_project_environment(tmp_path)

    path.write_bytes(b"KEY=\xff\n")
    with pytest.raises(ProjectEnvironmentError, match="invalid UTF-8"):
        load_project_environment(tmp_path)

    path.write_text("KEY=secret\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWGRP)
    with pytest.raises(ProjectEnvironmentError, match="owner-only") as error:
        load_project_environment(tmp_path)
    assert "secret" not in str(error.value)


def test_project_env_missing_and_parent_files_are_not_discovered(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    project = parent / "project"
    project.mkdir(parents=True)
    _write_env(parent, "PARENT=must-not-load\n")

    assert dict(load_project_environment(project)) == {}


def test_project_env_rejects_a_dotenv_symlink_outside_the_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.env"
    outside.write_text("ESCAPED=must-not-load\n", encoding="utf-8")
    (project / ".odcli").mkdir()
    (project / ".odcli" / ".env").symlink_to(outside)

    with pytest.raises(ProjectEnvironmentError, match="inside the resolved project") as error:
        load_project_environment(project)
    assert "must-not-load" not in str(error.value)


def test_project_env_process_precedence_is_immutable_and_does_not_mutate_process() -> None:
    file_values = {"FROM_FILE": "file", "OVERRIDE": "file"}
    process = {"OVERRIDE": "process", "FROM_PROCESS": "process"}
    result = effective_project_environment(file_values, process)

    assert dict(result) == {
        "FROM_FILE": "file",
        "OVERRIDE": "process",
        "FROM_PROCESS": "process",
    }
    with pytest.raises(TypeError):
        result["NEW"] = "value"  # type: ignore[index]
    assert process == {"OVERRIDE": "process", "FROM_PROCESS": "process"}

    config = InstanceConfig(base_url="http://127.0.0.1:8069", project_environment=file_values)
    with pytest.raises(TypeError):
        config.project_environment["NEW"] = "value"  # type: ignore[index]


def test_restore_consumes_process_master_before_file_master() -> None:
    file_values = {"ODCLI_TEST_MASTER_PASSWORD": "file-secret"}

    assert _remote_password(effective_project_environment(file_values, {})) == "file-secret"
    assert (
        _remote_password(
            effective_project_environment(file_values, {"ODCLI_TEST_MASTER_PASSWORD": "process"})
        )
        == "process"
    )


def test_project_env_values_are_only_merged_at_explicit_odoo_child_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OVERRIDE", "process")
    file_values = {"ORDINARY": "file", "OVERRIDE": "file", "ODCLI_TEST_MASTER_PASSWORD": "secret"}

    snapshot, overrides = captured_child_environment(project_environment=file_values)
    captured = dict(snapshot)
    public = dict(overrides)
    assert captured["ORDINARY"] == "file"
    assert captured["OVERRIDE"] == "process"
    assert "ODCLI_TEST_MASTER_PASSWORD" not in captured
    assert "ODCLI_TEST_MASTER_PASSWORD" not in public

    explicit_snapshot, explicit_public = captured_child_environment(
        {"ODCLI_TEST_MASTER_PASSWORD": "explicit-secret"}
    )
    assert "ODCLI_TEST_MASTER_PASSWORD" not in dict(explicit_snapshot)
    assert "ODCLI_TEST_MASTER_PASSWORD" not in dict(explicit_public)

    denied = prepared_step("git", ["status"])
    assert "ORDINARY" not in dict(denied.environment_snapshot)
    assert "ODCLI_TEST_MASTER_PASSWORD" not in dict(denied.environment_snapshot)


def test_odoo_child_gets_file_values_but_psql_keeps_its_purpose_built_environment() -> None:
    file_values = {"ORDINARY": "ordinary", "ODCLI_TEST_MASTER_PASSWORD": "secret"}
    odoo_step, _, _, _ = _build_shell_script_step(
        StartConfig(),
        executable_prefix=("python", "odoo-bin"),
        default_cwd=None,
        source="result = 1\n",
        project_environment=file_values,
    )
    assert dict(odoo_step.environment_snapshot)["ORDINARY"] == "ordinary"
    assert "ODCLI_TEST_MASTER_PASSWORD" not in dict(odoo_step.environment_snapshot)
    assert "secret" not in repr(odoo_step.public_projection())

    psql_step = build_psql_specification(
        host="127.0.0.1",
        port=5432,
        user="odoo",
        database="postgres",
        _executable="psql",
        _environment_values=(("PGAPPNAME", "odcli"),),
    ).prepared_step
    assert "ORDINARY" not in dict(psql_step.environment_snapshot)
    assert dict(psql_step.environment_snapshot)["PGAPPNAME"] == "odcli"


@pytest.mark.parametrize("builder", ["git", "postgresql", "docker", "package", "child"])
def test_production_builders_deny_project_dotenv_from_non_odoo_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, builder: str
) -> None:
    sentinel = "dotenv-boundary-sentinel"
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "process-master-sentinel")
    _write_env(
        tmp_path,
        f"DOTENV_SENTINEL={sentinel}\nODCLI_TEST_MASTER_PASSWORD=file-master-sentinel\n",
    )
    project_values = load_project_environment(tmp_path)
    if builder == "git":
        client = OdooClient(config=OdooClientConfig(executable="odoo"))
        git_command = client.environments.list_command(project=tmp_path)
        steps = _prepared_steps(git_command)
    elif builder == "postgresql":
        steps = (
            build_psql_specification(
                host="127.0.0.1",
                port=5432,
                user="odoo",
                database="postgres",
                _executable="psql",
            ).prepared_step,
        )
    elif builder == "docker":
        cluster = PostgresCluster(
            _repository_root=tmp_path,
            _project_id="dotenv-test",
            _mode="compose",
            _endpoint_host="127.0.0.1",
            _endpoint_port=5432,
            _image="postgres:16",
            _user="odoo",
        )
        steps = tuple(
            step for step in cluster._ensure_running_steps(5.0) if isinstance(step, PreparedStep)
        )
    elif builder == "package":
        package_command = verify_deps_command(
            recorded_python=sys.executable, worktree_root=tmp_path
        )
        steps = _prepared_steps(package_command)
    else:
        steps = (prepared_step("representative-child", ["--version"]),)

    assert steps
    for step in steps:
        environment = dict(step.environment_snapshot)
        for key, value in project_values.items():
            assert environment.get(key) != value
        assert "ODCLI_TEST_MASTER_PASSWORD" not in environment


def test_editor_and_browser_builders_do_not_embed_project_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sentinel = "dotenv-ui-sentinel"
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "ui-master-sentinel")
    _write_env(
        tmp_path,
        f"DOTENV_SENTINEL={sentinel}\nODCLI_TEST_MASTER_PASSWORD=ui-master-sentinel\n",
    )
    project_values = load_project_environment(tmp_path)
    runtime = cast(
        "object",
        SimpleNamespace(
            command_prefix=("python", "/repo/odoo-bin"),
            start_config=StartConfig(),
            database="demo",
            environment_name="demo",
            project_id="project_demo",
            python_path=tmp_path / "venv" / "bin" / "python",
            root=tmp_path,
        ),
    )
    profile = build_launch_profile(cast("RuntimeView", runtime))
    editor_payload = repr(profile)
    browser_payload = launch_json(profile)
    for value in project_values.values():
        assert value not in editor_payload
        assert value not in browser_payload
