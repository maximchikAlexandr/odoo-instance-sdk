from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from odoo_instance_sdk.cli import _rich_vscode_generate
from odoo_instance_sdk.commands import backup, db, pg, resource
from odoo_instance_sdk.commands.module import _rich_module_list, _rich_module_update
from odoo_instance_sdk.commands.output import OutputDocument
from odoo_instance_sdk.commands.test import rich_test_result
from odoo_instance_sdk.commands.translations import _rich_translation_export
from odoo_instance_sdk.execution import JsonValue

_DYNAMIC = "[/] markup-like \x1b[31mdata"
_POSTGRES_DYNAMIC = "[/] \x00\x01\x1b[31m\x7f\x80\x9f postgres"
_RICH_TABLE_NODE = "tests/unit/test_rich_markup_safety.py::test_bounded_rich_tables_treat_dynamic_cells_as_inert_text"
_RICH_TABLE_IDS = (
    "module-list",
    "module-update",
    "translation-export",
    "vscode-generate",
    "backup-table",
    "backup-detail",
    "backup-delete",
    "db-refresh",
    "db-admin-reset",
    "db-restore",
    "db-list",
    "resource-list",
    "resource-doctor",
    "postgres-cluster",
    "postgres-approval",
    "rich-test-result",
)


def _document(result: dict[str, JsonValue]) -> OutputDocument:
    return OutputDocument(
        schema_version=1,
        ok=True,
        command="test",
        context={},
        provenance={},
        dry_run=False,
        warnings=(),
        result=result,
    )


def _rich_test(document: OutputDocument) -> str:
    result = document.result
    assert isinstance(result, dict)
    return rich_test_result(result)


@pytest.mark.parametrize(
    ("render", "result"),
    [
        (_rich_module_list, {"modules": [{"name": _DYNAMIC, "state": _DYNAMIC}]}),
        (_rich_module_update, {"modules": [_DYNAMIC]}),
        (_rich_translation_export, {"exports": [{"module": _DYNAMIC}]}),
        (_rich_vscode_generate, {"written": _DYNAMIC}),
        (backup._rich_table, {"backups": [{"id": _DYNAMIC}]}),
        (backup._rich_detail, {"id": _DYNAMIC}),
        (backup._rich_delete, {"plan": {"path": _DYNAMIC}}),
        (db._rich_refresh, {"mode": _DYNAMIC}),
        (db._rich_admin_reset, {"database": _DYNAMIC}),
        (db._restore_rich, {"restored_database": _DYNAMIC}),
        (db._list_rich, {"cluster": _DYNAMIC, "databases": [{"name": _DYNAMIC}]}),
        (resource._rich_list, {"resources": [{"name": _DYNAMIC}]}),
        (resource._rich_doctor, {"findings": [{"message": _DYNAMIC}]}),
        (pg._cluster_rich, {"endpoint": _DYNAMIC}),
        (lambda document: pg._approval_rich(document, _DYNAMIC), {"image": _DYNAMIC}),
        (_rich_test, {"owner_kind": _DYNAMIC, "project_id": _DYNAMIC, "exit_code": 0}),
    ],
    ids=_RICH_TABLE_IDS,
)
def test_bounded_rich_tables_treat_dynamic_cells_as_inert_text(
    render: Callable[[OutputDocument], str], result: dict[str, JsonValue]
) -> None:
    output = render(_document(result))

    assert "[/]" in output
    assert "markup-like" in output
    assert "\\x1b[31m" in output
    assert "\x1b" not in output


def test_bounded_rich_table_node_ids_are_stable_for_mutmut() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            _RICH_TABLE_NODE,
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    collected = [line.strip() for line in result.stdout.splitlines() if _RICH_TABLE_NODE in line]
    assert collected == [f"{_RICH_TABLE_NODE}[{case_id}]" for case_id in _RICH_TABLE_IDS]


@pytest.mark.parametrize(
    ("render", "result"),
    [
        (
            pg._locks_rich,
            {"rows": [{"pid": _POSTGRES_DYNAMIC, "relation": _POSTGRES_DYNAMIC}]},
        ),
        (
            pg._monitoring_rich,
            {
                "installed": [_POSTGRES_DYNAMIC],
                "already_present": [_POSTGRES_DYNAMIC],
                "skipped": [_POSTGRES_DYNAMIC],
            },
        ),
    ],
    ids=["postgres-locks-rows", "postgres-monitoring-outcome"],
)
def test_postgres_rich_projection_escapes_hostile_controls_deterministically(
    render: Callable[[OutputDocument], str], result: dict[str, JsonValue]
) -> None:
    document = _document(result)

    first = render(document)
    second = render(document)

    assert first == second
    assert "[/]" in first
    assert "postgres" in first
    assert "\\x00" in first
    assert "\\x01" in first
    assert "\\x1b[31m" in first
    assert "\\x7f" in first
    assert "\\x80" in first
    assert "\\x9f" in first
    assert "\x1b" not in first
