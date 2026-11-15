from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import msgspec
import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan, ProcessStep
from odoo_instance_sdk.models import CommandResult, OdooFieldInfo, OdooModelInfo, OdooRefInfo
from odoo_instance_sdk.resources.inspector import OdooInspector, _decode

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance


def _shell_instance(payload: dict[str, Any], calls: list[tuple[str, bool, bool]]) -> OdooInstance:
    def shell(source: str, *, commit: bool, exclusive: bool, result_converter: Any) -> Command[Any]:
        calls.append((source, commit, exclusive))
        raw = CommandResult(
            args=["odoo"],
            returncode=0,
            stdout=(
                "__ODCLI_PAYLOAD__deadbeefdeadbeef__ "
                + json.dumps({"result": payload})
                + " __END_PAYLOAD__deadbeefdeadbeef__"
            ),
            stderr="",
            duration=0.0,
        )
        return Command.create(ExecutionPlan(), lambda _context: result_converter(raw))

    return cast("OdooInstance", SimpleNamespace(_shell_script_command=shell))


def test_model_inspection_uses_one_rollback_only_fixed_registry_command() -> None:
    calls: list[tuple[str, bool, bool]] = []
    inspector = OdooInspector(
        _shell_instance(
            {
                "name": "res.partner",
                "exists": True,
                "description": "Contact",
                "table": "res_partner",
                "abstract": False,
                "transient": False,
                "auto": True,
                "field_count": 7,
            },
            calls,
        )
    )

    result = inspector.model("res.partner")

    assert result == OdooModelInfo(
        name="res.partner",
        exists=True,
        description="Contact",
        table="res_partner",
        abstract=False,
        transient=False,
        auto=True,
        field_count=7,
    )
    assert len(calls) == 1
    source, commit, exclusive = calls[0]
    assert (commit, exclusive) == (False, False)
    assert "env.registry.models.get(_name)" in source
    assert "_fields" in source
    assert "_record" not in source


def test_inspection_command_plan_has_rollback_action_without_http_server(tmp_path: Path) -> None:
    config_path = tmp_path / "odoo.conf"
    config_path.write_text(
        "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\n"
        f"addons_path = {tmp_path / 'addons'}\nadmin_passwd = x\n",
        encoding="utf-8",
    )
    instance = OdooClient(config=OdooClientConfig(executable="odoo")).instance.from_config(
        config_path
    )

    command = instance.inspector.model_command("res.partner")
    actions = [step for step in command.plan.steps if isinstance(step, ActionStep)]
    processes = [step for step in command.plan.steps if isinstance(step, ProcessStep)]

    assert [(action.action, action.read_only, action.mutating) for action in actions] == [
        ("rollback", True, False)
    ]
    assert len(processes) == 1
    assert processes[0].argv[1] == "shell"


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (
            OdooModelInfo,
            {
                "name": "x_missing.model",
                "exists": False,
                "description": None,
                "table": None,
                "abstract": None,
                "transient": None,
                "auto": None,
                "field_count": None,
            },
        ),
        (
            OdooFieldInfo,
            {
                "model": "res.partner",
                "name": "missing_field",
                "exists": False,
                "label": None,
                "type": None,
                "required": None,
                "readonly": None,
                "store": None,
                "index": None,
                "relation": None,
                "related": None,
                "computed": None,
            },
        ),
        (
            OdooRefInfo,
            {
                "xmlid": "base.missing",
                "exists": False,
                "model": None,
                "record_id": None,
                "display_name": None,
            },
        ),
    ],
)
def test_missing_inspection_metadata_is_deterministic(
    model: type[msgspec.Struct], fields: dict[str, Any]
) -> None:
    assert msgspec.to_builtins(model(**fields)) == fields


def test_inspection_models_reject_unbounded_or_fabricated_metadata() -> None:
    with pytest.raises(ValueError, match="bounded"):
        OdooModelInfo(
            name="res.partner",
            exists=True,
            description="x" * 257,
            table="res_partner",
            abstract=False,
            transient=False,
            auto=True,
            field_count=1,
        )
    with pytest.raises(ValueError, match="missing objects"):
        OdooModelInfo(
            name="x_missing.model",
            exists=False,
            description=None,
            table="res_x",
            abstract=None,
            transient=None,
            auto=None,
            field_count=None,
        )


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("model_command", ("res.partner; import os",)),
        ("field_command", ("res.partner", "email value")),
        ("ref_command", ("base.user_admin.extra",)),
    ],
)
def test_invalid_identifiers_fail_before_shell_construction(
    method: str, args: tuple[str, ...]
) -> None:
    calls: list[tuple[str, bool, bool]] = []
    inspector = OdooInspector(_shell_instance({}, calls))

    with pytest.raises(ValueError, match="invalid"):
        getattr(inspector, method)(*args)

    assert calls == []


def test_field_and_ref_sources_project_only_approved_values() -> None:
    field_calls: list[tuple[str, bool, bool]] = []
    field = OdooInspector(
        _shell_instance(
            {
                "model": "res.partner",
                "name": "email",
                "exists": True,
                "label": "Email",
                "type": "char",
                "required": False,
                "readonly": False,
                "store": True,
                "index": False,
                "relation": None,
                "related": False,
                "computed": False,
            },
            field_calls,
        )
    ).field("res.partner", "email")
    assert field.exists is True
    source = field_calls[0][0]
    assert "getattr(_field, 'compute', None)" in source
    assert "getattr(_field, 'related', None)" in source
    assert "'default'" not in source and "'domain'" not in source

    ref_calls: list[tuple[str, bool, bool]] = []
    ref = OdooInspector(
        _shell_instance(
            {
                "xmlid": "base.user_admin",
                "exists": True,
                "model": "res.users",
                "record_id": 1,
                "display_name": "Administrator",
            },
            ref_calls,
        )
    ).ref("base.user_admin")
    assert ref.record_id == 1
    ref_source = ref_calls[0][0]
    assert "env.ref(_xmlid, raise_if_not_found=False)" in ref_source
    assert "login" not in ref_source and "password" not in ref_source


def test_decoder_rejects_invalid_shell_payload_with_sanitized_error() -> None:
    result = CommandResult(args=["odoo"], returncode=0, stdout="{}", stderr="", duration=0.0)
    with pytest.raises(Exception, match="invalid result"):
        _decode(result, OdooModelInfo)
