"""Security and concurrency contracts for the direct encrypted secret store."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import (
    SecretStoreConflictError,
    SecretStoreCorruptError,
    SecretStoreRecordError,
)
from odoo_instance_sdk.internal.secret_store import SecretStore

_MEMBER = "8d5e0d5f-5a8a-4ce8-8f4b-8ff4c8e1b2f5"
pytestmark = pytest.mark.dashboard


@pytest.fixture
def store(tmp_path: Path) -> SecretStore:
    return SecretStore(user_root=tmp_path / "user")


def test_empty_store_is_absent_without_creating_files(store: SecretStore) -> None:
    assert store.lookup_remote_odoo("https://Odoo.Example/") is None
    assert not store.key_path.exists()
    assert not store.store_path.exists()


def test_first_write_is_encrypted_owner_only_and_write_only(store: SecretStore) -> None:
    record = store.create_remote_odoo("https://Odoo.Example/", "odoo-password")

    assert record.base_url == "https://odoo.example"
    assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.store_path.stat().st_mode) == 0o600
    assert b"odoo-password" not in store.store_path.read_bytes()
    assert "odoo-password" not in repr(store.lookup_remote_odoo("https://odoo.example"))
    with pytest.raises(sqlite3.DatabaseError), sqlite3.connect(store.store_path) as connection:
        connection.execute("SELECT * FROM remote_odoo_secret").fetchall()


def test_blank_edit_retains_secret_and_revision_is_cas(store: SecretStore) -> None:
    created = store.create_remote_odoo("https://odoo.example", "first")
    updated = store.update_remote_odoo(
        "https://odoo.example", revision=created.revision, master_password=""
    )
    assert updated.revision == 2
    assert store.lookup_remote_odoo("https://odoo.example").master_password == "first"  # type: ignore[union-attr]

    with pytest.raises(SecretStoreConflictError):
        store.update_remote_odoo(
            "https://odoo.example", revision=created.revision, master_password="stale"
        )
    assert store.lookup_remote_odoo("https://odoo.example").master_password == "first"  # type: ignore[union-attr]


def test_gitlab_identity_generates_stable_key_and_is_host_scoped(store: SecretStore) -> None:
    first = store.create_gitlab_member(_MEMBER, "https://GitLab.Example/", "operator", "token-a")
    second = store.create_gitlab_member(_MEMBER, "https://other.example", "operator", "token-b")

    assert first.token_key.startswith("ODCLI_GITLAB_TOKEN_")
    assert first.token_key != second.token_key
    assert "token-a" not in repr(store.lookup_gitlab_member(_MEMBER, "gitlab.example"))
    assert store.lookup_gitlab_member(_MEMBER, "gitlab.example").token_key == first.token_key  # type: ignore[union-attr]
    updated = store.update_gitlab_member(
        _MEMBER,
        "gitlab.example",
        revision=first.revision,
        login="new-operator",
        token="",
    )
    assert updated.token_key == first.token_key
    assert store.lookup_gitlab_member(_MEMBER, "gitlab.example").login == "new-operator"  # type: ignore[union-attr]
    assert store.lookup_gitlab_member(_MEMBER, "gitlab.example") is not None
    assert store.lookup_gitlab_member(_MEMBER, "gitlab.example").token_key == first.token_key  # type: ignore[union-attr]
    assert store.lookup_gitlab_member(_MEMBER, "other.example").token_key == second.token_key  # type: ignore[union-attr]


@pytest.mark.parametrize("state", ["key_only", "store_only", "wrong_key", "corrupt_schema"])
def test_partial_wrong_and_corrupt_store_fail_closed(store: SecretStore, state: str) -> None:
    store.create_remote_odoo("https://odoo.example", "secret")
    if state == "key_only":
        store.store_path.unlink()
    elif state == "store_only":
        store.key_path.unlink()
    elif state == "wrong_key":
        store.key_path.write_bytes(os.urandom(32))
    elif state == "corrupt_schema":
        driver = __import__("sqlcipher3.dbapi2", fromlist=["connect"])
        key = store.key_path.read_bytes().hex()
        with driver.connect(str(store.store_path)) as connection:
            connection.execute(f"PRAGMA key = \"x'{key}'\"")
            connection.execute("DROP TABLE remote_odoo_secret")
    with pytest.raises(SecretStoreCorruptError):
        store.lookup_remote_odoo("https://odoo.example")


def test_invalid_present_record_does_not_look_absent(store: SecretStore) -> None:
    store.create_remote_odoo("https://odoo.example", "secret")
    driver = __import__("sqlcipher3.dbapi2", fromlist=["connect"])
    connection = driver.connect(str(store.store_path))
    connection.execute(f"PRAGMA key = \"x'{store.key_path.read_bytes().hex()}'\"")
    connection.execute("UPDATE remote_odoo_secret SET master_password = ''")
    connection.commit()
    connection.close()

    with pytest.raises(SecretStoreRecordError):
        store.lookup_remote_odoo("https://odoo.example")


def test_delete_uses_revision_and_preserves_newer_value(store: SecretStore) -> None:
    created = store.create_remote_odoo("https://odoo.example", "secret")
    store.update_remote_odoo(
        "https://odoo.example", revision=created.revision, master_password="new"
    )
    with pytest.raises(SecretStoreConflictError):
        store.delete_remote_odoo("https://odoo.example", revision=created.revision)
    assert store.lookup_remote_odoo("https://odoo.example").master_password == "new"  # type: ignore[union-attr]
