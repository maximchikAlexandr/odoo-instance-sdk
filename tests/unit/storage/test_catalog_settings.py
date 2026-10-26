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


def test_rename_validates_the_proposed_key_and_value_atomically(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        original = catalog.create_setting("feature.name", True)
        with pytest.raises(BackupCatalogError):
            catalog.update_setting(
                original.key,
                True,
                expected_revision=original.revision,
                new_key="gitlab.mr.default_assignee:https://example.test/group/project",
            )
        assert catalog.get_setting(original.key) == original
        assert catalog.list_settings() == (original,)
    finally:
        catalog.close()


def test_rename_collision_is_a_safe_catalog_error_and_keeps_both_rows(
    tmp_path: Path,
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        original = catalog.create_setting("feature.name", "one")
        existing = catalog.create_setting("feature.other", "two")
        with pytest.raises(BackupCatalogError) as error:
            catalog.update_setting(
                original.key,
                "renamed",
                expected_revision=original.revision,
                new_key=existing.key,
            )
        assert "one" not in str(error.value)
        assert catalog.get_setting(original.key) == original
        assert catalog.get_setting(existing.key) == existing
    finally:
        catalog.close()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("auth.token", "submitted-secret-auth-token"),
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


@pytest.mark.parametrize(
    "key",
    [
        "database_url",
        "access_key",
        "authorization",
        "auth_header",
        "client_secret",
        "refresh_token",
        "private_key_path",
        "docker_auth_config",
    ],
)
def test_redaction_classified_keys_are_rejected_without_echoing_values(
    tmp_path: Path, key: str
) -> None:
    submitted = f"submitted-secret-{key}"
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        with pytest.raises(BackupCatalogError) as error:
            catalog.create_setting(key, submitted)
        assert submitted not in str(error.value)
        assert catalog.list_settings() == ()
    finally:
        catalog.close()
