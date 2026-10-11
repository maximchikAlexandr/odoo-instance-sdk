from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts/odcli_agent_tools/numbered_tool.py"
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


def test_service_dispatches_inspect_with_exact_identifier(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    service = _module()
    from odoo_instance_sdk.internal import agent_checkout

    monkeypatch.setattr(
        agent_checkout,
        "inspect_numbered_installation",
        lambda identifier: {"kind": "numbered", "identifier": identifier, "healthy": True},
    )

    assert service._dispatch(["inspect", "7"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "kind": "numbered",
        "identifier": "7",
        "healthy": True,
    }


def test_service_dispatches_remove_with_validated_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    service = _module()
    from odoo_instance_sdk import client as client_module, config as config_module
    from odoo_instance_sdk.internal import agent_checkout

    calls: list[tuple[str, Path, str]] = []

    class FakeClient:
        def __init__(self, *, config: object) -> None:
            del config

    monkeypatch.setattr(client_module, "OdooClient", FakeClient)
    monkeypatch.setattr(
        config_module,
        "OdooClientConfig",
        lambda *, executable: SimpleNamespace(executable=executable),
    )
    monkeypatch.setattr(
        agent_checkout,
        "remove_numbered_installation",
        lambda identifier, *, client, checkout_parent: calls.append(
            (identifier, checkout_parent, type(client).__name__)
        ),
    )

    parent = tmp_path / "checkouts"
    assert (
        service._dispatch(
            ["remove", "7", "--checkout-parent", str(parent), "--odoo-executable", "/bin/odoo"]
        )
        == 0
    )
    assert calls == [("7", parent, "FakeClient")]
    assert json.loads(capsys.readouterr().out) == {
        "kind": "numbered",
        "identifier": "7",
        "removed": True,
    }


def test_service_dispatches_create_with_narrow_environment_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    service = _module()
    from odoo_instance_sdk import (
        client as client_module,
        config as config_module,
        project as project_module,
    )
    from odoo_instance_sdk.internal import agent_checkout
    from odoo_instance_sdk.internal.agent_checkout import NumberedCheckoutOptions

    class FakeClient:
        def __init__(self, *, config: object) -> None:
            del config

    monkeypatch.setattr(client_module, "OdooClient", FakeClient)
    monkeypatch.setattr(
        config_module,
        "OdooClientConfig",
        lambda *, executable: SimpleNamespace(executable=executable),
    )
    project = SimpleNamespace(name="project")
    monkeypatch.setattr(project_module.ProjectConfig, "load", lambda path: (project, path)[0])
    captured: list[NumberedCheckoutOptions] = []

    def create(options: NumberedCheckoutOptions) -> SimpleNamespace:
        captured.append(options)
        return SimpleNamespace(to_json=lambda: {"ok": True})

    monkeypatch.setattr(agent_checkout, "create_numbered_installation", create)
    environment_json = tmp_path / "environment.json"
    environment_json.write_text(
        json.dumps(
            {
                "base_ref": "main",
                "db_mode": "copy",
                "python": "/usr/bin/python3",
                "create_venv": True,
                "odoo_bin": "/srv/odoo/odoo-bin",
                "hash_lock": str(tmp_path / "requirements.lock"),
                "hash_lock_sha256": "a" * 64,
                "remote_name": "staging",
            }
        ),
        encoding="utf-8",
    )

    assert (
        service._dispatch(
            [
                "create",
                "--project",
                str(tmp_path / "project.toml"),
                "--reference-checkout",
                str(tmp_path / "reference"),
                "--checkout-parent",
                str(tmp_path / "checkouts"),
                "--reference-sha",
                "b" * 40,
                "--sdk-sha",
                "c" * 40,
                "--environment-json",
                str(environment_json),
                "--odoo-executable",
                "/bin/odoo",
            ]
        )
        == 0
    )
    options = captured[0]
    assert options.environment_options.db_mode.value == "copy"
    assert options.environment_options.odoo_bin == Path("/srv/odoo/odoo-bin")
    assert options.reference_sha == "b" * 40
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_service_rejects_untyped_environment_option_values(tmp_path: Path) -> None:
    service = _module()
    payload = tmp_path / "invalid.json"
    payload.write_text(json.dumps({"http_port": "8069"}), encoding="utf-8")

    with pytest.raises(TypeError, match="http_port"):
        service._load_environment_options(payload)
