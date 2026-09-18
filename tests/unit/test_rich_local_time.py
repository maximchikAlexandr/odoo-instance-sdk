from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.backup import _rich_detail, _rich_table
from odoo_instance_sdk.commands.output import OutputDocument
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.cli_format import rich_local_time

_LOCAL_OFFSET = datetime.now().astimezone().utcoffset()
_LOCAL_OFFSET_SECONDS = int(_LOCAL_OFFSET.total_seconds()) if _LOCAL_OFFSET is not None else 0


def _expected_local(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _document(result: dict[str, JsonValue]) -> OutputDocument:
    return OutputDocument(
        schema_version=1,
        ok=True,
        command="backup.list",
        context={},
        provenance={},
        dry_run=False,
        warnings=(),
        result=result,
    )


@pytest.mark.parametrize(
    ("value", "label"),
    [
        pytest.param(
            datetime(2026, 9, 16, 22, 30, tzinfo=UTC),
            "aware-utc",
            id="aware-utc",
        ),
        pytest.param(
            datetime(2026, 9, 16, 22, 30),
            "naive-sqlite-utc",
            id="naive-sqlite-utc",
        ),
        pytest.param(
            datetime(2026, 9, 16, 22, 30, tzinfo=timezone(timedelta(hours=5))),
            "non-zero-offset",
            id="non-zero-offset",
        ),
    ],
)
def test_rich_local_time_formats_without_seconds_or_offsets(value: datetime, label: str) -> None:
    rendered = rich_local_time(value if label != "naive-sqlite-utc" else value.isoformat())

    assert rendered == _expected_local(value)
    assert "T" not in rendered
    assert "Z" not in rendered
    assert rendered[10:] == f" {rendered[11:]}"
    assert rendered.count(":") == 1


def test_rich_local_time_crosses_day_boundary() -> None:
    midnight_utc = datetime(2026, 9, 16, 0, 5, tzinfo=UTC)

    rendered = rich_local_time(midnight_utc)

    assert rendered == _expected_local(midnight_utc)
    if _LOCAL_OFFSET_SECONDS < 0:
        assert rendered.startswith("2026-09-15")
    elif _LOCAL_OFFSET_SECONDS > 0:
        assert rendered.startswith("2026-09-16")


def test_rich_local_time_accepts_iso_string_with_z() -> None:
    rendered = rich_local_time("2026-09-16T22:30:00Z")

    assert rendered == _expected_local(datetime(2026, 9, 16, 22, 30, tzinfo=UTC))


def test_backup_ls_rich_shows_local_catalogue_time() -> None:
    iso = "2026-09-16T22:30:00Z"
    document = _document({"backups": [{"id": "b1", "catalogue_time": iso}]})

    rendered = _rich_table(document)

    assert _expected_local(datetime(2026, 9, 16, 22, 30, tzinfo=UTC)) in rendered
    assert iso not in rendered


def test_backup_inspect_rich_formats_all_time_fields() -> None:
    catalogue = "2026-09-16T22:30:00Z"
    occurred = "2026-09-15T01:10:00Z"
    restored = "2026-09-15T03:20:00Z"
    document = _document(
        {
            "id": "b1",
            "catalogue_time": catalogue,
            "history": [{"sequence": 1, "event_type": "download_started", "occurred_at": occurred}],
            "restore_links": [
                {
                    "db_host": "h",
                    "db_port": 5432,
                    "database_name": "demo",
                    "restored_at": restored,
                }
            ],
        }
    )

    rendered = _rich_detail(document)

    assert _expected_local(datetime(2026, 9, 16, 22, 30, tzinfo=UTC)) in rendered
    assert _expected_local(datetime(2026, 9, 15, 1, 10, tzinfo=UTC)) in rendered
    assert _expected_local(datetime(2026, 9, 15, 3, 20, tzinfo=UTC)) in rendered
    assert catalogue not in rendered
    assert occurred not in rendered
    assert restored not in rendered


def _seed_catalog(tmp_path: Path) -> Path:
    db_path = tmp_path / "catalog.sqlite3"
    backup_path = tmp_path / "backup.zip"
    with ZipFile(backup_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"db_name": "demo"}))
        archive.writestr("dump.sql", "-- test")

    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    backup_id = "00000000-0000-0000-0000-000000000007"
    catalog = BackupCatalog(db_path=db_path)
    catalog.start_download(backup_id, "http://localhost:8069", "demo", "zip", True, backup_path)
    catalog.success_download(backup_id, backup_path.name, backup_path.stat().st_size, "")
    catalog.close()
    return db_path


def test_json_output_keeps_iso_precision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _seed_catalog(tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.cli.get_catalog_path", lambda **_kwargs: db_path)

    json_result = CliRunner().invoke(cli, ["backup", "list", "--all-projects", "--format", "json"])
    assert json_result.exit_code == 0
    raw = json.loads(json_result.stdout)["result"]["backups"][0]["catalogue_time"]
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    assert "T" in str(raw)
    assert str(raw).count(":") >= 2
    assert parsed.second == 0 or parsed.second >= 0

    toon_result = CliRunner().invoke(cli, ["backup", "list", "--all-projects", "--format", "toon"])
    assert toon_result.exit_code == 0
    from toon import DecodeOptions, decode

    toon_payload = decode(toon_result.stdout, DecodeOptions(indent=2, strict=True))
    raw_toon = toon_payload["result"]["backups"][0]["catalogue_time"]
    assert str(raw_toon) == str(raw)


def test_durations_are_not_reformatted() -> None:
    from odoo_instance_sdk.commands.pg import _cluster_rich

    document = OutputDocument(
        schema_version=1,
        ok=True,
        command="pg.cluster",
        context={},
        provenance={},
        dry_run=False,
        warnings=(),
        result={
            "mode": "managed",
            "owned": True,
            "state": "healthy",
            "endpoint": "localhost:5432",
            "server": {"version": "17.0", "uptime_seconds": 90123},
        },
    )

    rendered = _cluster_rich(document)
    assert "90123s" in rendered
    assert "-" not in rendered.split("Uptime", 1)[1].split("\n", 1)[0]
