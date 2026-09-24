from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from odoo_instance_sdk.commands import resource
from odoo_instance_sdk.commands.cli_parts.callbacks import (
    _print_doctor,
    _rich_deps_projection,
    _rich_detached_status,
    _rich_vscode_generate,
)
from odoo_instance_sdk.commands.git import _rich as rich_git
from odoo_instance_sdk.commands.module import (
    _rich_module_deps,
    _rich_module_info,
    _rich_module_list,
    _rich_module_order,
    _rich_module_where,
)
from odoo_instance_sdk.commands.multi_target import _rich_multi_target
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    failure_document,
    success_document,
)
from odoo_instance_sdk.commands.test import rich_test_result
from odoo_instance_sdk.commands.translations import _rich_translation_export
from odoo_instance_sdk.internal.doctor import CheckResult, DoctorRemediation, DoctorReport


def _document(result: dict[str, object], *, command: str = "workflow") -> OutputDocument:
    return success_document(command=command, result=cast("JsonObject", result))


def _assert_bordered(rendered: str, *labels: str) -> None:
    assert "┌" in rendered and "┼" in rendered and "└" in rendered
    assert all(label in rendered for label in labels)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("renderer", "result", "labels"),
    [
        (_rich_module_list, {"modules": []}, ("NAME", "STATE", "VERSION", "(none)")),
        (
            _rich_module_info,
            {"module": {"name": "sale", "path": "/addons/sale"}},
            ("Field", "Value", "sale"),
        ),
        (
            _rich_module_where,
            {
                "name": "sale",
                "path": "/addons/sale",
                "manifest_path": "/addons/sale/__manifest__.py",
            },
            ("Field", "Value", "manifest_path"),
        ),
        (
            _rich_module_deps,
            {"module": {"module": {"name": "sale"}}, "dependencies": []},
            ("Dependency", "Status", "(none)"),
        ),
        (
            resource._rich_doctor,
            {"findings": []},
            ("Severity", "Message", "No resource findings."),
        ),
        (
            _rich_translation_export,
            {"exports": []},
            ("Module", "Language", "(none)"),
        ),
        (
            _rich_vscode_generate,
            {"written": "/tmp/launch.json"},
            ("Field", "Value", "launch.json"),
        ),
    ],
)
def test_workflow_renderers_use_bordered_tables(
    renderer: Callable[[OutputDocument], str],
    result: dict[str, object],
    labels: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rendered = renderer(_document(result))

    _assert_bordered(rendered, *labels)
    assert capsys.readouterr().out == ""


@pytest.mark.unit
def test_module_install_order_is_row_oriented() -> None:
    rendered = _rich_module_order(_document({"modules": ["base", "sale"]}))

    _assert_bordered(rendered, "Order", "Module", "1", "base", "2", "sale")
    assert "Install order: base, sale" not in rendered


@pytest.mark.unit
def test_git_and_test_renderers_use_bordered_tables() -> None:
    git = rich_git(_document({"branch": "main", "ahead": 1}, command="git.check"))
    test = rich_test_result(
        {
            "owner_kind": "project",
            "project_id": "demo",
            "environment_id": None,
            "environment_name": None,
            "selection": {"kind": "module"},
            "modules": ["sale"],
            "exit_code": 0,
        }
    )

    _assert_bordered(git, "Field", "Value", "branch", "main")
    _assert_bordered(test, "Field", "Value", "Odoo test result", "sale")


@pytest.mark.unit
def test_detached_and_dependency_results_are_row_oriented() -> None:
    detached = _rich_detached_status(
        _document(
            {
                "pid": 42,
                "owner_kind": "environment",
                "owner_id": "env-1",
                "http_endpoint": "http://127.0.0.1:8069",
                "log_path": "/tmp/odoo.log",
            },
            command="run",
        )
    )
    deps = _rich_deps_projection(
        failure_document(
            command="deps.verify",
            dry_run=False,
            error_code="deps_verify_failed",
            error_message="Dependency verification failed",
            error_details={"missing_imports": [{"module": "odoo", "import": "odoo"}]},
        )
    )

    _assert_bordered(detached, "Field", "Value", "PID", "42", "Owner ID", "env-1")
    _assert_bordered(deps, "Check", "Status", "missing import", "odoo")


@pytest.mark.unit
def test_doctor_groups_scopes_into_tables(capsys: pytest.CaptureFixture[str]) -> None:
    report = DoctorReport(
        checks=[
            CheckResult("uv", "ok", "available"),
            CheckResult(
                "runtime",
                "warn",
                "unavailable",
                environment_id="env-1",
                environment_name="Demo",
                facts={"available": False},
                remediations=(
                    DoctorRemediation(
                        description="repair runtime",
                        argv=("odcli", "env", "sync"),
                        mutating=True,
                        dry_run_supported=True,
                    ),
                ),
            ),
        ]
    )

    _print_doctor(report)
    output = capsys.readouterr().out

    assert output.count("Check") == 2
    assert output.count("┌") == 2
    assert "Project checks" in output
    assert "Environment: Demo" in output
    assert "repair runtime" in output


@pytest.mark.unit
def test_multi_target_results_have_one_target_table() -> None:
    rendered = _rich_multi_target(
        _document(
            {
                "targets": [
                    {"target": "one", "ok": True, "result": {"status": "deleted"}},
                    {"target": "two", "ok": False, "error": "locked"},
                ]
            },
            command="backup.delete",
        )
    )

    _assert_bordered(rendered, "Target", "Outcome", "Details", "one", "success", "two", "failed")
