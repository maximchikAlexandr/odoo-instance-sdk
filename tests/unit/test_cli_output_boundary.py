from __future__ import annotations

import ast
from pathlib import Path

import pytest
from rich import box
from rich.text import Text

from odoo_instance_sdk.commands.output import (
    OutputMode,
    bordered_table,
    emit,
    postgres_state_cells,
    render_rich_text,
    success_document,
)
from odoo_instance_sdk.models import PostgresClusterState

_REPO_ROOT = Path(__file__).parents[2]
_COMMANDS_ROOT = _REPO_ROOT / "src" / "odoo_instance_sdk" / "commands"
_DOWNSTREAM_DIRECT_TABLE_SITES = frozenset(
    {
        ("src/odoo_instance_sdk/commands/backup.py", 195),
        ("src/odoo_instance_sdk/commands/backup.py", 223),
        ("src/odoo_instance_sdk/commands/backup.py", 251),
        ("src/odoo_instance_sdk/commands/backup.py", 265),
        ("src/odoo_instance_sdk/commands/backup.py", 279),
        ("src/odoo_instance_sdk/commands/backup.py", 323),
        ("src/odoo_instance_sdk/commands/backup.py", 373),
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 672),
        ("src/odoo_instance_sdk/commands/db.py", 756),
        ("src/odoo_instance_sdk/commands/db.py", 787),
        ("src/odoo_instance_sdk/commands/db.py", 809),
        ("src/odoo_instance_sdk/commands/db.py", 826),
        ("src/odoo_instance_sdk/commands/env/checkout.py", 881),
        ("src/odoo_instance_sdk/commands/git.py", 35),
        ("src/odoo_instance_sdk/commands/module.py", 105),
        ("src/odoo_instance_sdk/commands/module.py", 125),
        ("src/odoo_instance_sdk/commands/module.py", 142),
        ("src/odoo_instance_sdk/commands/module.py", 172),
        ("src/odoo_instance_sdk/commands/module.py", 188),
        ("src/odoo_instance_sdk/commands/pg.py", 69),
        ("src/odoo_instance_sdk/commands/pg.py", 172),
        ("src/odoo_instance_sdk/commands/pg.py", 227),
        ("src/odoo_instance_sdk/commands/pg.py", 521),
        ("src/odoo_instance_sdk/commands/ps.py", 176),
        ("src/odoo_instance_sdk/commands/ps.py", 204),
        ("src/odoo_instance_sdk/commands/ps.py", 224),
        ("src/odoo_instance_sdk/commands/ps.py", 258),
        ("src/odoo_instance_sdk/commands/resource.py", 491),
        ("src/odoo_instance_sdk/commands/resource.py", 535),
        ("src/odoo_instance_sdk/commands/test.py", 195),
        ("src/odoo_instance_sdk/commands/translations.py", 347),
        ("src/odoo_instance_sdk/commands/translations.py", 383),
    }
)


@pytest.mark.unit
def test_bordered_table_uses_shared_geometry_and_foldable_columns() -> None:
    table = bordered_table("Name", "Details", title="Inventory")

    assert table.box == box.SQUARE
    assert table.safe_box is True
    assert table.show_edge is True
    assert table.show_lines is True
    assert table.header_style == "bold"
    assert [column.overflow for column in table.columns] == ["fold", "fold"]

    table.add_row("worker", "long detail " * 20)
    rendered = render_rich_text(table, width=80)

    assert "Inventory" in rendered
    assert "Name" in rendered
    assert "Details" in rendered
    assert "worker" in rendered
    assert "┌" in rendered and "┼" in rendered and "└" in rendered
    assert all(len(line) <= 80 for line in rendered.splitlines())


@pytest.mark.unit
def test_render_rich_text_is_side_effect_free(capsys: pytest.CaptureFixture[str]) -> None:
    assert render_rich_text(Text("captured"), width=80) == "captured"

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.unit
def test_postgres_state_cells_preserve_state_and_deduplicate_reasons() -> None:
    assert postgres_state_cells(
        PostgresClusterState.HEALTHY,
        "stats_failed",
        None,
        "stats_failed",
        "query_failed",
    ) == ("healthy", "stats_failed, query_failed")
    assert postgres_state_cells(PostgresClusterState.STOPPED) == ("stopped", "")


@pytest.mark.unit
def test_emit_owns_one_bounded_rich_emission(capsys: pytest.CaptureFixture[str]) -> None:
    calls = 0

    def projection(_document: object) -> str:
        nonlocal calls
        calls += 1
        return "one result"

    assert (
        emit(
            success_document(command="boundary", result={"status": "ok"}),
            OutputMode.RICH,
            rich=projection,
        )
        == 0
    )
    assert calls == 1
    assert capsys.readouterr().out == "one result\n"


@pytest.mark.unit
def test_production_commands_inventory_direct_table_constructors() -> None:
    actual: set[tuple[str, int]] = set()
    approved: set[tuple[str, int]] = set()
    for source_path in sorted(_COMMANDS_ROOT.rglob("*.py")):
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        relative_path = source_path.relative_to(_REPO_ROOT).as_posix()
        constructors = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Table"
        ]
        actual.update((relative_path, node.lineno) for node in constructors)
        if relative_path != "src/odoo_instance_sdk/commands/output.py":
            continue
        helper = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "bordered_table"
        )
        approved.update(
            (relative_path, node.lineno) for node in ast.walk(helper) if node in constructors
        )

    downstream = actual - approved
    assert len(_DOWNSTREAM_DIRECT_TABLE_SITES) == 32
    assert len(actual) == 33
    assert len(approved) == 1
    assert downstream == _DOWNSTREAM_DIRECT_TABLE_SITES
