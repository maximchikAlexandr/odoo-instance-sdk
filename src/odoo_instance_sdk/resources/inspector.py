"""Bounded read-only Odoo registry inspection."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, TypeVar, cast

import msgspec

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.server import parse_payload
from odoo_instance_sdk.models.command import CommandResult
from odoo_instance_sdk.models.inspection import OdooFieldInfo, OdooModelInfo, OdooRefInfo

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.resources.instance import OdooInstance

_Result = TypeVar("_Result", OdooModelInfo, OdooFieldInfo, OdooRefInfo)


_TECHNICAL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*$")
_FIELD_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_XMLID = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*$")


class OdooInspector:
    """Small public facade for the three supported registry identity reads."""

    def __init__(self, instance: OdooInstance) -> None:
        self._instance = instance

    def model(self, name: str) -> OdooModelInfo:
        return self.model_command(name).run()

    def model_command(self, name: str) -> Command[OdooModelInfo]:
        _validate_identifier(name, _TECHNICAL_NAME, "model")
        source = (
            "import json as _odcli_json\n"
            f"_name = _odcli_json.loads({json.dumps(json.dumps(name))})\n"
            "_model = env.registry.models.get(_name)\n"
            "_bounded = lambda _value: (_value[:256] if isinstance(_value, str) else None)\n"
            "result = {'name': _name, 'exists': _model is not None, "
            "'description': _bounded(getattr(_model, '_description', None)) if _model else None, "
            "'table': _bounded(getattr(_model, '_table', None)) if _model else None, "
            "'abstract': bool(getattr(_model, '_abstract', False)) if _model else None, "
            "'transient': bool(getattr(_model, '_transient', False)) if _model else None, "
            "'auto': bool(getattr(_model, '_auto', False)) if _model else None, "
            "'field_count': len(getattr(_model, '_fields', {})) if _model else None}\n"
        )
        return cast(
            "Command[OdooModelInfo]",
            self._instance._shell_script_command(
                source,
                commit=False,
                exclusive=False,
                result_converter=lambda result: _decode(result, OdooModelInfo),
            ),
        )

    def field(self, model: str, name: str) -> OdooFieldInfo:
        return self.field_command(model, name).run()

    def field_command(self, model: str, name: str) -> Command[OdooFieldInfo]:
        _validate_identifier(model, _TECHNICAL_NAME, "model")
        _validate_identifier(name, _FIELD_NAME, "field")
        source = (
            "import json as _odcli_json\n"
            f"_model_name = _odcli_json.loads({json.dumps(json.dumps(model))})\n"
            f"_field_name = _odcli_json.loads({json.dumps(json.dumps(name))})\n"
            "_model = env.registry.models.get(_model_name)\n"
            "_field = getattr(_model, '_fields', {}).get(_field_name) if _model else None\n"
            "_bounded = lambda _value: (_value[:256] if isinstance(_value, str) else None)\n"
            "result = {'model': _model_name, 'name': _field_name, 'exists': _field is not None, "
            "'label': _bounded(getattr(_field, 'string', None)) if _field else None, "
            "'type': _bounded(getattr(_field, 'type', None)) if _field else None, "
            "'required': bool(getattr(_field, 'required', False)) if _field else None, "
            "'readonly': bool(getattr(_field, 'readonly', False)) if _field else None, "
            "'store': bool(getattr(_field, 'store', False)) if _field else None, "
            "'index': bool(getattr(_field, 'index', False)) if _field else None, "
            "'relation': _bounded(getattr(_field, 'relation', None)) if _field else None, "
            "'related': bool(getattr(_field, 'related', None)) if _field else None, "
            "'computed': bool(getattr(_field, 'compute', None)) if _field else None}\n"
        )
        return cast(
            "Command[OdooFieldInfo]",
            self._instance._shell_script_command(
                source,
                commit=False,
                exclusive=False,
                result_converter=lambda result: _decode(result, OdooFieldInfo),
            ),
        )

    def ref(self, xmlid: str) -> OdooRefInfo:
        return self.ref_command(xmlid).run()

    def ref_command(self, xmlid: str) -> Command[OdooRefInfo]:
        _validate_identifier(xmlid, _XMLID, "xmlid")
        source = (
            "import json as _odcli_json\n"
            f"_xmlid = _odcli_json.loads({json.dumps(json.dumps(xmlid))})\n"
            "_record = env.ref(_xmlid, raise_if_not_found=False)\n"
            "_exists = bool(_record)\n"
            "_display_name = _record.display_name if _exists else None\n"
            "result = {'xmlid': _xmlid, 'exists': _exists, "
            "'model': _record._name[:256] if _exists and isinstance(_record._name, str) else None, "
            "'record_id': int(_record.id) if _exists else None, "
            "'display_name': _display_name[:256] if isinstance(_display_name, str) else None}\n"
        )
        return cast(
            "Command[OdooRefInfo]",
            self._instance._shell_script_command(
                source,
                commit=False,
                exclusive=False,
                result_converter=lambda result: _decode(result, OdooRefInfo),
            ),
        )


def _validate_identifier(value: str, pattern: re.Pattern[str], label: str) -> None:
    if not isinstance(value, str) or len(value) > 256 or not pattern.fullmatch(value):
        raise ValueError(f"invalid {label} identifier")


def _decode(result: CommandResult, model: type[_Result]) -> _Result:
    payload = parse_payload(result.stdout)
    raw = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise ConfigError("Odoo inspection returned an invalid result")
    try:
        return msgspec.convert(raw, type=model)
    except (msgspec.ValidationError, TypeError, ValueError) as exc:
        raise ConfigError("Odoo inspection returned invalid bounded metadata") from exc


__all__ = ["OdooInspector"]
