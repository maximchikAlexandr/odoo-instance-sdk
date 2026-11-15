"""Scaffold configuration, planning, and fake native execution."""

from __future__ import annotations

import ast
import shutil
import stat
from collections.abc import Mapping
from pathlib import Path

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.exceptions import ScaffoldError
from odoo_instance_sdk.execution import ActionStep, JsonValue, ProcessStep
from odoo_instance_sdk.internal.paths import get_user_config_path
from odoo_instance_sdk.project import ProjectConfig

_FAKE_ODOO = """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
template = pathlib.Path(args[args.index("--template") + 1])
name = args[-2]
destination = pathlib.Path(args[-1]) / name
mode = pathlib.Path(str(sys.argv[0]) + ".mode").read_text(encoding="utf-8")
if mode == "fail":
    destination.mkdir(parents=True)
    (destination / "partial.txt").write_text("partial", encoding="utf-8")
    raise SystemExit(2)
for path in template.rglob("*"):
    if not path.is_file():
        continue
    relative = path.relative_to(template)
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8")
    target.write_text(
        text.replace("{{ name|pascal }}", "SaleExtension").replace("{{ name }}", name),
        encoding="utf-8",
    )
"""


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ODCLI_USER_ROOT", str(tmp_path / "user-root"))


def _binary(tmp_path: Path, *, mode: str = "ok") -> Path:
    path = tmp_path / "odoo-bin"
    path.write_text(_FAKE_ODOO, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    Path(str(path) + ".mode").write_text(mode, encoding="utf-8")
    return path


def _project(tmp_path: Path, binary: Path, body: str = "") -> ProjectConfig:
    root = tmp_path / "project"
    (root / ".odcli").mkdir(parents=True)
    text = f'[project]\nodoo_bin = "{binary}"\n'
    if body:
        text += "\n" + body
    (root / ".odcli" / "project.toml").write_text(text, encoding="utf-8")
    return ProjectConfig.load(root)


def _client(binary: Path) -> OdooClient:
    return OdooClient(config=OdooClientConfig(executable=str(binary)))


def _prepare_details(command: object) -> Mapping[str, JsonValue]:
    plan = getattr(command, "plan")
    step = next(
        item
        for item in plan.steps
        if isinstance(item, ActionStep) and item.step_id == "scaffold.template.prepare"
    )
    assert isinstance(step.details, dict)
    return step.details


@pytest.mark.parametrize(
    ("user_text", "project_body", "include", "exclude", "manifest", "expected_author", "wizard"),
    [
        (
            '[scaffold.manifest]\nauthor = "User"\n',
            '[scaffold]\nexclude = ["wizard"]\n',
            (),
            (),
            None,
            "User",
            False,
        ),
        (
            '[scaffold]\ninclude = ["wizard"]\n\n[scaffold.manifest]\nauthor = "User"\n',
            "",
            (),
            ("wizard",),
            None,
            "User",
            False,
        ),
        (
            "",
            "",
            ("wizard",),
            (),
            {"author": "CLI", "summary": "Sale workflow extensions"},
            "CLI",
            True,
        ),
    ],
)
def test_scaffold_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    user_text: str,
    project_body: str,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    manifest: dict[str, str] | None,
    expected_author: str,
    wizard: bool,
) -> None:
    _isolate(monkeypatch, tmp_path)
    if user_text:
        path = get_user_config_path()
        path.parent.mkdir(parents=True)
        path.write_text(user_text, encoding="utf-8")
    binary = _binary(tmp_path)
    project = _project(tmp_path, binary, project_body)
    command = _client(binary).scaffolds.create_command(
        project=project,
        module_name="SaleExtension",
        destination=tmp_path / "addons",
        include=include,
        exclude=exclude,
        manifest=manifest,
    )
    details = _prepare_details(command)
    files = details["files"]
    assert isinstance(files, dict)
    parsed = ast.literal_eval(str(files["__manifest__.py"]))
    assert command.plan.process_steps[0].argv[1:3] == ("scaffold", "--template")
    assert parsed["author"] == expected_author
    assert parsed["license"] == "LGPL-3"
    assert parsed["name"] == "Sale Extension"
    assert ("wizard/__init__.py" in files) is wizard
    assert not (tmp_path / "addons" / "sale_extension").exists()
    assert not Path(command.plan.process_steps[0].argv[3]).exists()


def test_dry_run_creates_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    binary = _binary(tmp_path)
    project = _project(tmp_path, binary, '[scaffold.manifest]\nauthor = "User"\n')
    command = _client(binary).scaffolds.create_command(
        project=project,
        module_name="sale_extension",
        destination=tmp_path / "addons",
    )
    template = Path(command.plan.process_steps[0].argv[3])
    assert template.name == "template"
    assert not template.exists()
    assert not template.parent.exists()
    assert not (tmp_path / "addons").exists()


@pytest.mark.parametrize(
    ("module_name", "exclude", "manifest", "code"),
    [
        ("../escape", (), {"author": "User"}, "scaffold_invalid_module"),
        ("sale_extension", ("models",), {"author": "User"}, "scaffold_baseline"),
        ("sale_extension", (), None, "scaffold_missing_author"),
        ("sale_extension", (), {"author": "User", "password": "secret"}, "scaffold_secret"),
        ("sale_extension", (), {"author": "User {{ injected }}"}, "scaffold_invalid_manifest"),
    ],
)
def test_scaffold_rejects_unsafe_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    exclude: tuple[str, ...],
    manifest: dict[str, str] | None,
    code: str,
) -> None:
    _isolate(monkeypatch, tmp_path)
    binary = _binary(tmp_path)
    project = _project(tmp_path, binary)
    with pytest.raises(ScaffoldError) as caught:
        _client(binary).scaffolds.create_command(
            project=project,
            module_name=module_name,
            destination=tmp_path / "addons",
            exclude=exclude,
            manifest=manifest,
        )
    assert caught.value.code == code
    assert not (tmp_path / "addons").exists()


def test_existing_target_and_layer_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    binary = _binary(tmp_path)
    project = _project(tmp_path, binary, '[scaffold.manifest]\nauthor = "User"\n')
    target = tmp_path / "addons" / "sale_extension"
    target.mkdir(parents=True)
    with pytest.raises(ScaffoldError) as existing:
        _client(binary).scaffolds.create_command(
            project=project,
            module_name="sale_extension",
            destination=tmp_path / "addons",
        )
    assert existing.value.code == "scaffold_destination_exists"
    assert target.is_dir()
    with pytest.raises(ScaffoldError) as conflict:
        _client(binary).scaffolds.create_command(
            project=project,
            module_name="sale_extension",
            destination=tmp_path / "fresh",
            include=("tests",),
            exclude=("tests",),
        )
    assert conflict.value.code == "scaffold_component_conflict"


def test_execution_cleans_template_and_rolls_back_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    binary = _binary(tmp_path, mode="fail")
    project = _project(tmp_path, binary, '[scaffold.manifest]\nauthor = "User"\n')
    command = _client(binary).scaffolds.create_command(
        project=project,
        module_name="sale_extension",
        destination=tmp_path / "addons",
    )
    template = Path(
        next(step.argv[3] for step in command.plan.steps if isinstance(step, ProcessStep))
    )
    with pytest.raises(ScaffoldError) as caught:
        command.run()
    assert caught.value.code == "scaffold_native_failed"
    assert not template.exists()
    assert not (tmp_path / "addons" / "sale_extension").exists()


def test_execution_returns_serialized_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    binary = _binary(tmp_path)
    project = _project(
        tmp_path,
        binary,
        '[scaffold]\ninclude = ["wizard"]\n\n[scaffold.manifest]\nauthor = "User"\n',
    )
    command = _client(binary).scaffolds.create_command(
        project=project,
        module_name="sale_extension",
        destination=tmp_path / "addons",
        manifest={"depends": ["base", "sale"]},
    )
    template = Path(command.plan.process_steps[0].argv[3])
    result = command.run()
    payload = msgspec.to_builtins(result)
    assert payload["module_name"] == "sale_extension"
    assert payload["template_source"] == "odoo.cli.scaffold"
    assert "wizard" in payload["components"]
    assert (tmp_path / "addons" / "sale_extension" / "__manifest__.py").is_file()
    parsed = ast.literal_eval(
        (tmp_path / "addons" / "sale_extension" / "__manifest__.py").read_text(encoding="utf-8")
    )
    assert parsed["depends"] == ["base", "sale"]
    assert "wizard/wizard_views.xml" in parsed["data"]
    assert "demo" not in parsed
    assert payload["created_files"]
    assert not template.parent.exists()
    shutil.rmtree(tmp_path / "addons" / "sale_extension")
    rerun = _client(binary).scaffolds.create_command(
        project=project,
        module_name="sale_extension",
        destination=tmp_path / "addons",
        manifest={"depends": ["base", "sale"]},
    )
    Path(rerun.plan.process_steps[0].argv[3]).parent.mkdir()
    rerun.run()
    assert (tmp_path / "addons" / "sale_extension" / "__manifest__.py").is_file()


def test_omit_reserved_manifest_and_missing_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    user = get_user_config_path()
    user.parent.mkdir(parents=True)
    user.write_text(
        '[scaffold]\nomit_manifest = ["website"]\n\n[scaffold.manifest]\nauthor = "User"\n',
        encoding="utf-8",
    )
    binary = _binary(tmp_path)
    project = _project(
        tmp_path,
        binary,
        '[scaffold.manifest]\nwebsite = "https://project.test"\n',
    )
    command = _client(binary).scaffolds.create_command(
        project=project,
        module_name="sale_extension",
        destination=tmp_path / "addons",
    )
    files = _prepare_details(command)["files"]
    assert isinstance(files, dict)
    parsed = ast.literal_eval(str(files["__manifest__.py"]))
    assert parsed["website"] == "https://project.test"
    with pytest.raises(ScaffoldError) as reserved:
        _client(binary).scaffolds.create_command(
            project=project,
            module_name="sale_extension",
            destination=tmp_path / "reserved",
            manifest={"demo": ["demo/demo.xml"]},
        )
    assert reserved.value.code == "scaffold_reserved_manifest"
    absent = _project(tmp_path / "broken", tmp_path / "absent-odoo")
    with pytest.raises(ScaffoldError) as missing:
        _client(binary).scaffolds.create_command(
            project=absent,
            module_name="sale_extension",
            destination=tmp_path / "missing",
        )
    assert missing.value.code == "scaffold_executable"


def test_project_scaffold_round_trip(tmp_path: Path) -> None:
    binary = _binary(tmp_path)
    project = _project(
        tmp_path,
        binary,
        '[scaffold]\ninclude = ["tests"]\n\n[scaffold.manifest]\nauthor = "User"\nlicense = "LGPL-3"\n',
    )
    assert project.scaffold is not None
    assert project.scaffold.include == ("tests",)
    restored = ProjectConfig.load(project.repository_root)
    (project.repository_root / ".odcli" / "project.toml").write_text(
        project.to_manifest(),
        encoding="utf-8",
    )
    written = ProjectConfig.load(project.repository_root)
    assert written.scaffold == restored.scaffold


def test_missing_user_config_and_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    path = get_user_config_path()
    result = CliRunner().invoke(cli, ["config", "path", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert result.stdout
    assert Path(path).name == "config.toml"
    assert not path.exists()
