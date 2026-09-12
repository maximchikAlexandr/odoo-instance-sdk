from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import ConfigError, StalePlanError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.automation import ModuleUpdatePlan as AdapterPlan
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.models import CommandResult, ModuleUpdatePlan, StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.module import ModuleResource, classify_module_update_error


def _result(stdout: str, *, returncode: int = 0) -> CommandResult:
    return CommandResult(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr="",
        duration=0.0,
    )


def _instance(tmp_path: Path) -> OdooInstance:
    return cast(
        "OdooInstance",
        SimpleNamespace(
            config=InstanceConfig(
                base_url="http://127.0.0.1:8069",
                default_cwd=tmp_path,
                start_config=StartConfig(addons_path=["addons"]),
            )
        ),
    )


def test_update_uses_one_exclusive_command_and_returns_confirmed_modules(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    command = Command.create(
        ExecutionPlan(),
        lambda _context: _result(
            '__ODCLI_PAYLOAD__abc__{"result":{"updated":["sale"]}}__END_PAYLOAD__abc__'
        ),
    )
    resource = ModuleResource(instance)
    with (
        patch(
            "odoo_instance_sdk.internal.automation.update_modules_command",
            return_value=command,
        ) as update_command,
        patch(
            "odoo_instance_sdk.internal.automation.plan_module_update",
            return_value=AdapterPlan(modules=["sale"]),
        ),
    ):
        result = resource.update(("sale",))

    assert result.updated == ("sale",)
    update_command.assert_called_once_with(instance, ("sale",))


def test_update_rejects_uninstalled_modules_before_mutation(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    resource = ModuleResource(instance)
    with (
        patch(
            "odoo_instance_sdk.internal.automation.plan_module_update",
            return_value=AdapterPlan(modules=[], not_installed=["sale"]),
        ),
        patch("odoo_instance_sdk.internal.automation.update_modules_command") as update_command,
        pytest.raises(ConfigError, match="modules not installed: sale"),
    ):
        resource.update(("sale",))
    update_command.assert_not_called()


def test_nonzero_update_result_is_a_process_failure(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    command = Command.create(ExecutionPlan(), lambda _context: _result("", returncode=2))
    resource = ModuleResource(instance)
    with (
        patch(
            "odoo_instance_sdk.internal.automation.plan_module_update",
            return_value=AdapterPlan(modules=["sale"]),
        ),
        patch(
            "odoo_instance_sdk.internal.automation.update_modules_command",
            return_value=command,
        ),
        pytest.raises(RuntimeError, match=r"module update failed \(rc=2\)"),
    ):
        resource.update(("sale",))


def test_only_known_concurrent_user_error_is_classified() -> None:
    matching = "odoo.exceptions.UserError: another module operation is in progress"
    unrelated = "odoo.exceptions.UserError: invalid module manifest"

    assert classify_module_update_error(matching) is not None
    assert classify_module_update_error(unrelated) is None
    assert classify_module_update_error("process failed: another module is being processed") is None


def test_real_instance_update_revalidates_head_before_exclusive_mutation(tmp_path: Path) -> None:
    head = "abc123"
    config = StartConfig(config_path=str(tmp_path / "odoo.conf"), db_name="db")
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            default_cwd=tmp_path,
            start_config=config,
            command_prefix=("python3", "odoo-bin"),
        ),
        _client=OdooClient(config=OdooClientConfig(executable="python3")),
    )
    result = ProcessResult(
        argv=(),
        returncode=0,
        stdout="different-head\n",
        stderr="",
        duration=0.0,
        cwd=str(tmp_path),
        environment=(),
    )
    executor = RecordingExecutor(results={"module.update.provenance.git": result})
    selection = ModuleUpdatePlan(modules=("sale",), head=head)
    with patch(
        "odoo_instance_sdk.resources.instance.SubprocessExecutor",
        return_value=executor,
    ):
        command = instance.modules.update_command(("sale",), selection=selection)
        assert tuple(step.step_id for step in command.plan.process_steps)[:1] == (
            "module.update.provenance.git",
        )
        with pytest.raises(StalePlanError):
            command.run()

    assert [step.step_id for step in executor.executed] == ["module.update.provenance.git"]
