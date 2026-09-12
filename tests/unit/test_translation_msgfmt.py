from __future__ import annotations

import base64
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from toon import encode

from odoo_instance_sdk.commands.output import JsonObject, build_envelope, success_document
from odoo_instance_sdk.commands.translations import (
    _bounded_validation_output,
    _rich_translation_export,
    export_translations_command,
)
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance


def _instance(worktree: Path) -> OdooInstance:
    return OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(
                http_port=8069,
                addons_path=[str(worktree)],
            ),
            default_cwd=worktree,
            command_prefix=(sys.executable, "odoo-bin"),
        ),
        _client=MagicMock(),
    )


def _odoo_result(step: object, payload: dict[str, object]) -> ProcessResult:
    nonce = getattr(step, "wrapper_nonce")
    stdout = f"__ODCLI_PAYLOAD__{nonce}__ {json.dumps(payload)} __END_PAYLOAD__{nonce}__"
    return ProcessResult(
        argv=getattr(step, "argv"),
        returncode=0,
        stdout=stdout,
        stderr="",
        duration=0.0,
        cwd=getattr(step, "cwd"),
        environment=getattr(step, "environment"),
    )


def _payload(po: bytes) -> dict[str, object]:
    return {
        "result": [
            {
                "iso": "fr",
                "filename": "fr.po",
                "data": base64.b64encode(po).decode("ascii"),
                "module": "sale",
                "installed": True,
                "lang": "fr_FR",
            }
        ]
    }


def _module(worktree: Path) -> None:
    module = worktree / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}", encoding="utf-8")


def test_available_msgfmt_is_captured_with_sanitized_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "msgfmt"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(tool) if name == "msgfmt" else None)

    command = export_translations_command(
        _instance(tmp_path), ("sale",), ("fr_FR",), worktree_root=tmp_path
    )

    validation = next(step for step in command.plan.process_steps if "msgfmt" in step.step_id)
    assert validation.argv == (
        str(tool.resolve()),
        "--check",
        "--statistics",
        "-o",
        "/dev/null",
        "-",
    )
    assert validation.environment_overrides == (("LC_ALL", "C"),)
    assert validation.input_preview == "<generated PO>"


def test_missing_msgfmt_omits_optional_validation_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    command = export_translations_command(
        _instance(tmp_path), ("sale",), ("fr_FR",), worktree_root=tmp_path
    )

    assert all("msgfmt" not in step.step_id for step in command.plan.process_steps)


def test_successful_validation_runs_before_atomic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "msgfmt"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(tool) if name == "msgfmt" else None)
    _module(tmp_path)
    po = b'msgid ""\nmsgstr ""\n'
    executor = RecordingExecutor()

    def result_factory(step: object) -> ProcessResult:
        if getattr(step, "step_id") == "instance.shell_script":
            return _odoo_result(step, _payload(po))
        assert getattr(step, "stdin") == po
        assert getattr(step, "argv")[1:] == ("--check", "--statistics", "-o", "/dev/null", "-")
        return ProcessResult(
            argv=getattr(step, "argv"),
            returncode=0,
            stdout="msgfmt: 1 translated message\n",
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd"),
            environment=getattr(step, "environment"),
        )

    executor.result_factory = result_factory
    monkeypatch.setattr("odoo_instance_sdk.resources.instance.SubprocessExecutor", lambda: executor)

    command = export_translations_command(
        _instance(tmp_path), ("sale",), ("fr_FR",), worktree_root=tmp_path
    )
    assert [step.step_id for step in command.plan.process_steps] == [
        "instance.shell_script",
        "translations.msgfmt.0",
    ]

    result = command.run()[0]

    assert result.path.read_bytes() == po
    assert result.validation is not None
    assert result.validation.status == "passed"
    assert "translated message" in result.validation.output
    assert [step.step_id for step in executor.executed] == [
        "instance.shell_script",
        "translations.msgfmt.0",
    ]


def test_failed_validation_preserves_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "msgfmt"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(tool) if name == "msgfmt" else None)
    _module(tmp_path)
    target = tmp_path / "sale" / "i18n" / "fr.po"
    target.parent.mkdir()
    target.write_bytes(b"old")
    po = b'msgid "broken"\n'
    executor = RecordingExecutor()

    def result_factory(step: object) -> ProcessResult:
        if getattr(step, "step_id") == "instance.shell_script":
            return _odoo_result(step, _payload(po))
        return ProcessResult(
            argv=getattr(step, "argv"),
            returncode=1,
            stdout="",
            stderr="msgfmt: malformed PO file\n",
            duration=0.0,
            cwd=getattr(step, "cwd"),
            environment=getattr(step, "environment"),
        )

    executor.result_factory = result_factory
    monkeypatch.setattr("odoo_instance_sdk.resources.instance.SubprocessExecutor", lambda: executor)

    with pytest.raises(ConfigError, match="msgfmt validation failed"):
        export_translations_command(
            _instance(tmp_path), ("sale",), ("fr_FR",), worktree_root=tmp_path
        ).run()

    assert target.read_bytes() == b"old"


def test_available_tool_dry_run_does_not_export_validate_or_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "msgfmt"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(tool) if name == "msgfmt" else None)
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.resources.instance.SubprocessExecutor", lambda: executor)

    command = export_translations_command(
        _instance(tmp_path), ("sale",), ("fr_FR",), worktree_root=tmp_path
    )

    assert executor.executed == []
    assert not (tmp_path / "sale" / "i18n" / "fr.po").exists()
    assert [step.step_id for step in command.plan.process_steps] == [
        "instance.shell_script",
        "translations.msgfmt.0",
    ]


def test_warning_statistics_are_visible_in_rich_and_machine_meaning() -> None:
    result: JsonObject = {
        "exports": [
            {
                "module": "sale",
                "requested_lang": "fr_FR",
                "actual_filename": "fr.po",
                "bytes_written": 16,
                "validation": {
                    "tool": "msgfmt",
                    "status": "passed",
                    "output": "warning: fuzzy translation\n1 translated message",
                    "returncode": 0,
                },
            }
        ]
    }

    rich = _rich_translation_export(success_document(command="translations.export", result=result))
    machine = build_envelope(command="translations.export", ok=True, result=result)

    assert "warning: fuzzy translation" in rich
    assert "1 translated message" in rich
    machine_result = machine["result"]
    assert isinstance(machine_result, dict)
    exports = machine_result["exports"]
    assert isinstance(exports, list)
    first_export = exports[0]
    assert isinstance(first_export, dict)
    validation = first_export["validation"]
    assert isinstance(validation, dict)
    assert validation["tool"] == "msgfmt"
    assert validation["status"] == "passed"
    validation_output = validation["output"]
    assert isinstance(validation_output, str)
    assert "warning: fuzzy translation" in validation_output
    assert "1 translated message" in validation_output
    assert machine["data"] == machine["result"]
    toon = encode(machine)
    assert isinstance(toon, str)
    assert "warning: fuzzy translation" in toon
    assert "1 translated message" in toon


def test_validation_output_is_bounded() -> None:
    output = _bounded_validation_output("x" * 100, limit=32)

    assert len(output) <= 32
    assert output.endswith("[output truncated]")
