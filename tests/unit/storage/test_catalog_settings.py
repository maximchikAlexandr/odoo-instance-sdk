from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.storage.catalog import BackupCatalog


def test_settings_round_trip_and_revision_safe_crud(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        created = catalog.create_setting("feature.flags", {"z": [True, None], "a": 2})
        stored = catalog._conn.execute(
            "SELECT value_json FROM settings WHERE key=?", (created.key,)
        ).fetchone()[0]
        assert stored == '{"a":2,"z":[true,null]}'
        assert created.value == {"a": 2, "z": [True, None]}
        assert created.revision == 1
        assert created.created_at == created.updated_at

        updated = catalog.update_setting(
            created.key, "enabled", expected_revision=1, new_key="feature.name"
        )
        assert (updated.key, updated.value, updated.revision) == ("feature.name", "enabled", 2)
        assert updated.created_at == created.created_at
        assert catalog.get_setting(created.key) is None
        with pytest.raises(BackupCatalogError, match="reload and retry"):
            catalog.delete_setting(updated.key, expected_revision=1)
        assert catalog.list_settings() == (updated,)
        catalog.delete_setting(updated.key, expected_revision=2)
        assert catalog.list_settings() == ()
    finally:
        catalog.close()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("auth.token", "do-not-store"),
        ("gitlab.mr.default_assignee:https://example.test/group/project", True),
        ("gitlab.mr.default_assignee:https://example.test/group/project", 0),
        ("gitlab.mr.default_assignee:https://EXAMPLE.test/group/project", "alice"),
        ("feature.nan", float("nan")),
    ],
)
def test_invalid_and_secret_settings_are_rejected(tmp_path: Path, key: str, value: object) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        with pytest.raises(BackupCatalogError):
            catalog.create_setting(key, value)  # type: ignore[arg-type]
        assert catalog.list_settings() == ()
    finally:
        catalog.close()
