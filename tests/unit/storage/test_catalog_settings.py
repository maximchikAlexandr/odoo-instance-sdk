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


def test_setting_route_id_and_json_properties_are_stable(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        setting = catalog.create_setting("feature.flags", {"β": True, "a": [1, None]})

        assert setting.route_id
        assert setting.value_json == '{"a":[1,null],"β":true}'
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


def test_update_rejects_stale_revision_without_changing_value(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        original = catalog.create_setting("feature.name", "one")
        updated = catalog.update_setting(original.key, "two", expected_revision=1)

        with pytest.raises(BackupCatalogError, match="reload and retry"):
            catalog.update_setting(updated.key, "stale", expected_revision=1)
        assert catalog.get_setting(updated.key) == updated
    finally:
        catalog.close()


@pytest.mark.parametrize(
    "key",
    [
        "",
        " feature.name",
        "feature.name ",
        "k" * 513,
        "gitlab.mr.default_assignee:https://[",
        "gitlab.mr.default_assignee:https://user@example.test/group/project",
        "gitlab.mr.default_assignee:https://example.test/group/project?ref=main",
        "gitlab.mr.default_assignee:https://example.test/group/../project",
    ],
)
def test_invalid_keys_are_rejected(tmp_path: Path, key: str) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        with pytest.raises(BackupCatalogError):
            catalog.create_setting(key, "alice")
        assert catalog.list_settings() == ()
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


def test_values_must_be_bounded_json(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    deep: object = "leaf"
    for _ in range(66):
        deep = [deep]
    invalid_values: tuple[object, ...] = (
        {"not-json"},
        deep,
        "v" * (64 * 1024 + 1),
        float("inf"),
    )
    try:
        for index, value in enumerate(invalid_values):
            with pytest.raises(BackupCatalogError):
                catalog.create_setting(f"feature.invalid.{index}", value)  # type: ignore[arg-type]
        assert catalog.list_settings() == ()
    finally:
        catalog.close()


def test_corrupt_persisted_json_is_rejected_without_echoing_value(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        setting = catalog.create_setting("feature.value", "safe")
        catalog._conn.execute(
            "UPDATE settings SET value_json=? WHERE key=?",
            ('{"duplicate":1,"duplicate":2}', setting.key),
        )

        with pytest.raises(BackupCatalogError, match="JSON is invalid"):
            catalog.get_setting(setting.key)
    finally:
        catalog.close()
