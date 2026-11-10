from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.resources.instance import OdooInstance


def _instance(tmp_path: Path) -> OdooInstance:
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\n")
    client = OdooClient(config=OdooClientConfig(executable="python3"))
    return client.instance.from_config(config_path)


def test_bounded_user_query_returns_typed_safe_projection(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    payload = json.dumps([{"id": 14, "login": "fixture", "name": "Fixture User"}])
    executor = RecordingExecutor(
        results={
            "instance.shell_script": ProcessResult(
                argv=("python3", "shell"),
                returncode=0,
                stdout=payload,
                stderr="",
                duration=0.01,
                cwd=None,
                environment=(),
            )
        }
    )
    with patch(
        "odoo_instance_sdk.resources.instance.identity.SubprocessExecutor",
        return_value=executor,
    ):
        command = instance.list_browser_login_users_command("fixture")
        users = command.run()

    assert [(user.id, user.login, user.name) for user in users] == [(14, "fixture", "Fixture User")]
    assert len(command.plan.process_steps) == 1
    assert command.plan.process_steps[0].input_preview == "<redacted>"
    assert "SUPERUSER_ID" not in repr(users)


def test_browser_user_search_rejects_unbounded_input_before_planning(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    with pytest.raises(ValueError, match="too long"):
        instance.list_browser_login_users_command("x" * 81)


def test_session_command_plan_never_contains_a_sid(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    command = instance.create_browser_session_command(14)
    assert command.plan.steps
    assert "fixture-sid" not in repr(command)
    assert "session_token" not in repr(command.plan)
    assert command.plan.process_steps[0].input_preview == "<redacted>"
