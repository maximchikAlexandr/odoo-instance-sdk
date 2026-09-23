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
def test_shared_output_module_contains_the_only_direct_table_constructor() -> None:
    source_path = Path(__file__).parents[2] / "src/odoo_instance_sdk/commands/output.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    constructors = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Table"
    ]

    assert len(constructors) == 1
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "bordered_table"
    )
    assert helper.end_lineno is not None
    assert constructors[0].lineno >= helper.lineno
    assert constructors[0].lineno <= helper.end_lineno
