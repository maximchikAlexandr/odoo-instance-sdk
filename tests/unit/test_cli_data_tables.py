from __future__ import annotations

from collections.abc import Callable

import pytest

from odoo_instance_sdk.commands import backup, db, pg
from odoo_instance_sdk.commands.output import OutputDocument
from odoo_instance_sdk.execution import JsonValue


def _document(result: dict[str, JsonValue], *, command: str = "test") -> OutputDocument:
    return OutputDocument(
        schema_version=1,
        ok=True,
        command=command,
        context={},
        provenance={},
        dry_run=False,
        warnings=(),
        result=result,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("render", "result"),
    [
        (backup._rich_table, {"backups": []}),
        (db._list_rich, {"cluster": "127.0.0.1:5432", "databases": []}),
        (pg._locks_rich, {"rows": []}),
        (pg._monitoring_rich, {"installed": [], "already_present": [], "skipped": []}),
    ],
)
def test_data_command_empty_results_keep_one_bordered_table(
    render: Callable[[OutputDocument], str], result: dict[str, JsonValue]
) -> None:
    output = render(_document(result))

    assert "┌" in output
    assert "├" in output
    assert (
        "No backups" in output or "No databases" in output or "(none)" in output or "none" in output
    )


@pytest.mark.unit
@pytest.mark.parametrize("width", [80, 120, 180])
def test_backup_long_values_fold_at_supported_widths(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    monkeypatch.setenv("COLUMNS", str(width))
    output = backup._rich_table(
        _document(
            {
                "backups": [
                    {
                        "id": "backup-1",
                        "source_base_url": "https://odoo.example/" + "source" * 30,
                        "database_name": "database-" + "name" * 20,
                        "state": "available",
                        "file_present": True,
                        "recorded_bytes": None,
                        "catalogue_time": None,
                    }
                ]
            }
        )
    )

    assert output.strip()
    assert all(len(line) <= width for line in output.splitlines())
    assert "backup-1" in output


@pytest.mark.unit
def test_postgres_human_state_keeps_lifecycle_and_availability_separate() -> None:
    output = pg._cluster_rich(
        _document(
            {
                "mode": "compose",
                "owned": True,
                "state": "healthy",
                "unavailability_reason": "stats_failed",
                "server_unavailability_reason": "query_failed",
            },
            command="postgres.status",
        )
    )

    assert "│State" in output
    assert "healthy" in output
    assert "Availability" in output
    assert "stats_failed" in output
    assert "query_failed" in output
