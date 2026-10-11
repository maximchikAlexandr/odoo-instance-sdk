from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import pytest


_SCRIPT = (
    Path(__file__).parents[2] / ".agents/skills/odcli-autonomous-work/scripts/numbered_tool.py"
)
_SCRIPTS = _SCRIPT.parent


def _module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("numbered_tool_test", _SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_service_bootstrap_revalidates_canonical_interpreter_and_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    service = _module()
    canonical = tmp_path / "canonical-python"
    calls: list[list[str]] = []

    monkeypatch.setattr(service, "_canonical_interpreter", lambda: canonical)

    def record_source(value: Path) -> None:
        calls.append([str(value)])

    monkeypatch.setattr(service, "_canonical_source", record_source)

    def run(argv: list[str], **_kwargs: object) -> str:
        calls.append(argv)
        return '{"ok": true}\n'

    monkeypatch.setattr(service, "_run", run)

    assert service.main(["list"]) == 0
    assert calls[0] == [str(canonical)]
    assert calls[1][0] == str(canonical)
    assert calls[1][2] == service._TRUSTED_ARG
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_service_dispatches_bounded_list_evidence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    service = _module()
    from odoo_instance_sdk.internal import agent_checkout

    monkeypatch.setattr(
        agent_checkout,
        "list_numbered_installations",
        lambda *, limit: ({"kind": "numbered", "identifier": "1", "healthy": True},),
    )

    assert service._dispatch(["list", "--limit", "1"]) == 0
    result = cast("list[dict[str, object]]", json.loads(capsys.readouterr().out))
    assert result == [{"kind": "numbered", "identifier": "1", "healthy": True}]
