from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.context import ResolvedContext
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.execution import Command, ExecutionPlan, ProcessStep
from odoo_instance_sdk.internal.automation import ModuleUpdatePlan as AdapterPlan
from odoo_instance_sdk.models import CommandResult, DevelopmentEnvironment, StartConfig
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.module import ModuleResource


def _module_instance(config: InstanceConfig) -> OdooInstance:
    return cast("OdooInstance", SimpleNamespace(config=config))


def test_module_info_and_install_order_are_machine_readable(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    (addons / "base").mkdir(parents=True)
    (addons / "sale").mkdir()
    (addons / "base" / "__manifest__.py").write_text("{}\n")
    (addons / "sale" / "__manifest__.py").write_text("{'depends': ['base']}\n")
    instance = _module_instance(
        InstanceConfig(
            base_url="http://127.0.0.1:8069",
            default_cwd=tmp_path,
            start_config=StartConfig(addons_path=["addons"]),
        )
    )
    instance.modules = ModuleResource(instance)
    context = ResolvedContext(
        client=None,  # type: ignore[arg-type]
        source=None,  # type: ignore[arg-type]
        instance=instance,
        provenance="cwd",
    )

    with patch(
        "odoo_instance_sdk.commands.module.cli_context.ready_instance", return_value=context
    ):
        result = CliRunner().invoke(cli, ["module", "install-order", "sale", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result"]["modules"] == ["base", "sale"]


def test_module_where_and_deps_have_rich_projections_and_no_fields_option(
    tmp_path: Path,
) -> None:
    addons = tmp_path / "addons"
    (addons / "base").mkdir(parents=True)
    (addons / "sale").mkdir()
    (addons / "base" / "__manifest__.py").write_text("{}\n")
    (addons / "sale" / "__manifest__.py").write_text("{'depends': ['base', 'missing']}\n")
    instance = _module_instance(
        InstanceConfig(
            base_url="http://127.0.0.1:8069",
            default_cwd=tmp_path,
            start_config=StartConfig(addons_path=["addons"]),
        )
    )
    instance.modules = ModuleResource(instance)
    context = ResolvedContext(
        client=None,  # type: ignore[arg-type]
        source=None,  # type: ignore[arg-type]
        instance=instance,
        provenance="cwd",
    )
    with patch(
        "odoo_instance_sdk.commands.module.cli_context.ready_instance", return_value=context
    ):
        where = CliRunner().invoke(cli, ["module", "where", "sale"])
        deps = CliRunner().invoke(cli, ["module", "deps", "sale"])
        fields = CliRunner().invoke(
            cli, ["module", "where", "sale", "--format", "json", "--fields", "path"]
        )

    assert where.exit_code == 0, where.output
    assert "Odoo module location" in where.output
    assert str(addons / "sale") in where.output
    assert deps.exit_code == 0, deps.output
    assert "missing" in deps.output
    assert fields.exit_code == 2
    assert "fields" in fields.output.lower()


@pytest.mark.parametrize(
    ("stderr", "stdout", "expected_code"),
    [
        (
            "",
            "odoo.exceptions.UserError: another module is being processed",
            "module_operation_in_progress",
        ),
        ("", "odoo.exceptions.UserError: invalid module manifest", "module_update_failed"),
        ("worker failed", "", "module_update_failed"),
    ],
)
def test_module_update_cli_classifies_failures_without_retry(
    stderr: str, stdout: str, expected_code: str, tmp_path: Path
) -> None:
    project = ProjectConfig(
        repository_root=tmp_path,
        python=sys.executable,
        odoo_bin=Path(sys.executable),
    )
    instance = SimpleNamespace(
        config=SimpleNamespace(
            start_config=StartConfig(db_name="db"),
            command_prefix=(sys.executable, str(tmp_path / "odoo-bin")),
        )
    )
    context = ResolvedContext(
        client=object(),  # type: ignore[arg-type]
        source=project,
        instance=instance,  # type: ignore[arg-type]
        provenance="cwd",
    )
    calls = 0

    def run(_context: object) -> CommandResult:
        nonlocal calls
        calls += 1
        return CommandResult(args=[], returncode=2, stdout=stdout, stderr=stderr, duration=0.0)

    command = Command.create(ExecutionPlan(), run)
    with (
        patch("odoo_instance_sdk.commands.module.cli_context.ready_instance", return_value=context),
        patch("odoo_instance_sdk.cli.update_modules_command", return_value=command),
    ):
        result = CliRunner().invoke(cli, ["module", "update", "sale", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert calls == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == expected_code


def _update_context(tmp_path: Path, owner: str) -> tuple[ResolvedContext, OdooInstance]:
    source: ProjectConfig | DevelopmentEnvironment
    if owner == "project":
        source = ProjectConfig(
            repository_root=tmp_path,
            python=sys.executable,
            odoo_bin=Path(sys.executable),
            default_base_ref="HEAD~1",
        )
    else:
        source = cast(
            "DevelopmentEnvironment",
            SimpleNamespace(
                id="env-1",
                name="dev",
                worktree_path=str(tmp_path),
                repository_root=str(tmp_path),
                python_environment_path=sys.executable,
                base_ref="HEAD~1",
            ),
        )
    instance = _module_instance(
        InstanceConfig(
            base_url="http://127.0.0.1:8069",
            default_cwd=tmp_path,
            start_config=StartConfig(addons_path=["addons"], db_name="db"),
            command_prefix=(sys.executable, "odoo-bin"),
        )
    )
    instance.modules = ModuleResource(instance)
    return (
        ResolvedContext(
            client=None,  # type: ignore[arg-type]
            source=source,
            instance=instance,
            provenance="cwd",
        ),
        instance,
    )


@pytest.mark.parametrize("owner", ["project", "environment"])
@pytest.mark.parametrize("mode", ["rich", "json", "toon"])
@pytest.mark.parametrize("changed", [True, False], ids=["changed", "explicit"])
def test_module_update_dry_run_preserves_selection_and_defers_update_plan(
    owner: str, mode: str, changed: bool, tmp_path: Path
) -> None:
    context, _instance = _update_context(tmp_path, owner)
    selected = SimpleNamespace(
        base_source="explicit",
        requested_base="HEAD~1",
        resolved_base="merge-base",
        merge_base="merge-base",
        head="captured-head",
        changed_files=("addons/sale/models.py",),
        modules=("sale", "stock"),
        ignored_paths=(),
        unmapped_paths=(),
    )
    executed = 0

    def run(_context: object) -> CommandResult:
        nonlocal executed
        executed += 1
        return CommandResult(args=[], returncode=0, stdout="", stderr="", duration=0.0)

    command = Command.create(
        ExecutionPlan(
            steps=(
                ProcessStep(
                    step_id="module.probe",
                    argv=("git", "status"),
                    display="git status",
                    executable="git",
                ),
                ProcessStep(
                    step_id="module.update",
                    argv=("odoo-bin", "--upgrade"),
                    display="odoo-bin --upgrade",
                    executable="odoo-bin",
                    mutating=True,
                ),
            )
        ),
        run,
    )
    patches = [
        patch(
            "odoo_instance_sdk.internal.automation.plan_module_update",
            return_value=AdapterPlan(modules=["sale"], not_installed=["stock"]),
        ),
        patch(
            "odoo_instance_sdk.internal.automation.update_modules_command",
            return_value=command,
        ),
    ]
    if changed:
        patches.append(
            patch(
                "odoo_instance_sdk.resources.module.resolve_changed_selection",
                return_value=selected,
            )
        )
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "odoo_instance_sdk.commands.module.cli_context.ready_instance",
                return_value=context,
            )
        )
        for item in patches:
            stack.enter_context(item)
        args = ["module", "update"]
        if changed:
            args.append("--changed")
        else:
            args.append("sale")
        args.extend(["--dry-run", "--format", mode])
        result = CliRunner().invoke(cli, args)

    assert result.exit_code == 0, result.output
    assert executed == 0
    if mode == "rich":
        assert "git status" in result.output
        assert "odoo-bin --upgrade" in result.output
    else:
        if mode == "json":
            payload = json.loads(result.stdout)
        else:
            from toon import DecodeOptions, decode

            payload = decode(result.stdout, DecodeOptions(indent=2, strict=True))
        assert payload["result"] == payload["data"]
        assert payload["result"]["modules"] == ["sale"]
        assert payload["result"]["not_installed"] == ["stock"]
        if changed:
            assert payload["result"]["head"] == "captured-head"
            assert payload["result"]["changed_files"] == ["addons/sale/models.py"]
        assert [step["step_id"] for step in payload["result"]["plan"]["steps"]] == [
            "module.probe",
            "module.update",
        ]
