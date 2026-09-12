from __future__ import annotations

from collections.abc import Callable

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
    ids=lambda item: getattr(item, "__name__", "approval") if callable(item) else str(item),
)
def test_bounded_rich_tables_treat_dynamic_cells_as_inert_text(
    render: Callable[[OutputDocument], str], result: dict[str, JsonValue]
) -> None:
    output = render(_document(result))

    assert "[/]" in output
    assert "markup-like" in output
    assert "\\x1b[31m" in output
    assert "\x1b" not in output


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
