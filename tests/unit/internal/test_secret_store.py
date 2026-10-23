"""Security and concurrency contracts for the direct encrypted secret store."""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from odoo_instance_sdk.exceptions import (
    SecretStoreBusyError,
    SecretStoreConflictError,
    SecretStoreCorruptError,
    SecretStoreRecordError,
    SecretStoreUnavailableError,
)
from odoo_instance_sdk.internal.secret_store import SecretStore

_MEMBER = "8d5e0d5f-5a8a-4ce8-8f4b-8ff4c8e1b2f5"


def _sqlcipher_connection(store: SecretStore, *, timeout: float = 5) -> Any:
    driver = __import__("sqlcipher3.dbapi2", fromlist=["connect"])
    connection = driver.connect(str(store.store_path), timeout=timeout)
    connection.execute(f"PRAGMA key = \"x'{store.key_path.read_bytes().hex()}'\"")
    connection.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    return connection


@pytest.fixture
def store(tmp_path: Path) -> SecretStore:
    return SecretStore(user_root=tmp_path / "user")


def test_empty_store_is_absent_without_creating_files(store: SecretStore) -> None:
    assert store.lookup_remote_odoo("https://Odoo.Example/") is None
    assert not store.key_path.exists()
    assert not store.store_path.exists()


def test_write_fails_closed_when_sqlcipher_is_unavailable(
    store: SecretStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "sqlcipher3.dbapi2", None)

    with pytest.raises(SecretStoreUnavailableError, match="install the dashboard extra"):
        store.create_remote_odoo("https://odoo.example", "secret")

    assert not store.key_path.exists()
    assert not store.store_path.exists()


@pytest.mark.dashboard
def test_first_write_is_encrypted_owner_only_and_write_only(store: SecretStore) -> None:
    record = store.create_remote_odoo("https://Odoo.Example/", "odoo-password")

    assert record.base_url == "https://odoo.example"
    assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.store_path.stat().st_mode) == 0o600
    assert b"odoo-password" not in store.store_path.read_bytes()
    assert "odoo-password" not in repr(store.lookup_remote_odoo("https://odoo.example"))
    with pytest.raises(sqlite3.DatabaseError), sqlite3.connect(store.store_path) as connection:
        connection.execute("SELECT * FROM remote_odoo_secret").fetchall()


@pytest.mark.dashboard
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


@pytest.mark.dashboard
def test_gitlab_identity_generates_stable_key_and_is_host_scoped(store: SecretStore) -> None:
    first = store.create_gitlab_member(_MEMBER, "https://GitLab.Example/", "operator", "token-a")
    _second = store.create_gitlab_member(_MEMBER, "https://other.example", "operator", "token-b")

    first_private = store.lookup_gitlab_member(_MEMBER, "gitlab.example")
    second_private = store.lookup_gitlab_member(_MEMBER, "other.example")
    assert first_private is not None
    assert second_private is not None
    assert first_private.token_key.startswith("ODCLI_GITLAB_TOKEN_")
    assert first_private.token_key != second_private.token_key
    assert not hasattr(first, "token_key")
    assert first_private.token_key not in repr(first)
    assert "token-a" not in repr(first)

    loaded = store.get_gitlab_member(_MEMBER, "gitlab.example")
    assert loaded is not None
    assert not hasattr(loaded, "token_key")
    assert first_private.token_key not in repr(loaded)
    assert "token-a" not in repr(loaded)

    updated = store.update_gitlab_member(
        _MEMBER,
        "gitlab.example",
        revision=first.revision,
        login="new-operator",
        token="",
    )
    assert not hasattr(updated, "token_key")
    assert first_private.token_key not in repr(updated)
    assert "token-a" not in repr(updated)
    updated_private = store.lookup_gitlab_member(_MEMBER, "gitlab.example")
    assert updated_private is not None
    assert updated_private.login == "new-operator"
    assert updated_private.token_key == first_private.token_key
    assert updated_private.token == "token-a"


@pytest.mark.dashboard
def test_concurrent_sessions_same_identity_have_one_cas_winner(store: SecretStore) -> None:
    store.create_remote_odoo("https://odoo.example", "initial")
    loaded = threading.Barrier(2, timeout=5)
    first_done = threading.Event()
    outcomes: dict[str, str] = {}

    def update_first() -> None:
        connection = _sqlcipher_connection(store)
        try:
            revision = connection.execute(
                "SELECT revision FROM remote_odoo_secret WHERE base_url = ?",
                ("https://odoo.example",),
            ).fetchone()[0]
            loaded.wait()
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE remote_odoo_secret SET master_password = ?, revision = revision + 1 "
                "WHERE base_url = ? AND revision = ?",
                ("from-a", "https://odoo.example", revision),
            ).rowcount
            assert changed == 1
            connection.commit()
            outcomes["a"] = "updated"
            first_done.set()
        except Exception as exc:
            outcomes["a"] = f"unexpected: {exc}"
        finally:
            connection.close()

    def update_stale() -> None:
        connection = _sqlcipher_connection(store)
        try:
            revision = connection.execute(
                "SELECT revision FROM remote_odoo_secret WHERE base_url = ?",
                ("https://odoo.example",),
            ).fetchone()[0]
            loaded.wait()
            assert first_done.wait(5)
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE remote_odoo_secret SET master_password = ?, revision = revision + 1 "
                "WHERE base_url = ? AND revision = ?",
                ("from-b", "https://odoo.example", revision),
            ).rowcount
            assert changed == 0
            connection.rollback()
            outcomes["b"] = "conflict"
        except Exception as exc:
            outcomes["b"] = f"unexpected: {exc}"
        finally:
            connection.close()

    thread_a = threading.Thread(target=update_first)
    thread_b = threading.Thread(target=update_stale)
    thread_a.start()
    thread_b.start()
    thread_a.join(10)
    thread_b.join(10)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert sorted(outcomes.values()) == ["conflict", "updated"]
    assert store.lookup_remote_odoo("https://odoo.example").master_password == "from-a"  # type: ignore[union-attr]


@pytest.mark.dashboard
def test_concurrent_sessions_different_identities_commit_independently(store: SecretStore) -> None:
    first = store.create_remote_odoo("https://first.example", "first")
    second = store.create_remote_odoo("https://second.example", "second")
    loaded = threading.Barrier(2, timeout=5)
    outcomes: dict[str, str] = {}

    def update(label: str, base_url: str, revision: int, password: str) -> None:
        connection = _sqlcipher_connection(store)
        try:
            loaded.wait()
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE remote_odoo_secret SET master_password = ?, revision = revision + 1 "
                "WHERE base_url = ? AND revision = ?",
                (password, base_url, revision),
            ).rowcount
            assert changed == 1
            connection.commit()
            outcomes[label] = "updated"
        except Exception as exc:
            outcomes[label] = f"unexpected: {exc}"
        finally:
            connection.close()

    thread_a = threading.Thread(
        target=update, args=("a", "https://first.example", first.revision, "first-new")
    )
    thread_b = threading.Thread(
        target=update, args=("b", "https://second.example", second.revision, "second-new")
    )
    thread_a.start()
    thread_b.start()
    thread_a.join(10)
    thread_b.join(10)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert outcomes == {"a": "updated", "b": "updated"}
    assert store.lookup_remote_odoo("https://first.example").master_password == "first-new"  # type: ignore[union-attr]
    assert store.lookup_remote_odoo("https://second.example").master_password == "second-new"  # type: ignore[union-attr]


@pytest.mark.dashboard
def test_locked_database_fails_with_bounded_busy_error(store: SecretStore) -> None:
    created = store.create_remote_odoo("https://odoo.example", "initial")
    locker = _sqlcipher_connection(store)
    locker.execute("BEGIN IMMEDIATE")
    try:
        busy_store = SecretStore(
            key_path=store.key_path, store_path=store.store_path, busy_timeout_ms=25
        )
        with pytest.raises(SecretStoreBusyError):
            busy_store.update_remote_odoo(
                "https://odoo.example", revision=created.revision, master_password="new"
            )
    finally:
        locker.rollback()
        locker.close()


def _leave_key_only(store: SecretStore) -> None:
    store.store_path.unlink()


def _leave_store_only(store: SecretStore) -> None:
    store.key_path.unlink()


def _use_wrong_key(store: SecretStore) -> None:
    store.key_path.write_bytes(os.urandom(32))


def _corrupt_schema(store: SecretStore) -> None:
    driver = __import__("sqlcipher3.dbapi2", fromlist=["connect"])
    key = store.key_path.read_bytes().hex()
    with driver.connect(str(store.store_path)) as connection:
        connection.execute(f"PRAGMA key = \"x'{key}'\"")
        connection.execute("DROP TABLE remote_odoo_secret")


@pytest.mark.parametrize(
    "prepare",
    [_leave_key_only, _leave_store_only, _use_wrong_key, _corrupt_schema],
    ids=["key-only", "store-only", "wrong-key", "corrupt-schema"],
)
@pytest.mark.dashboard
def test_partial_wrong_and_corrupt_store_fail_closed(
    store: SecretStore, prepare: Callable[[SecretStore], None]
) -> None:
    store.create_remote_odoo("https://odoo.example", "secret")
    prepare(store)
    with pytest.raises(SecretStoreCorruptError):
        store.lookup_remote_odoo("https://odoo.example")


@pytest.mark.dashboard
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


@pytest.mark.dashboard
def test_delete_uses_revision_and_preserves_newer_value(store: SecretStore) -> None:
    created = store.create_remote_odoo("https://odoo.example", "secret")
    store.update_remote_odoo(
        "https://odoo.example", revision=created.revision, master_password="new"
    )
    with pytest.raises(SecretStoreConflictError):
        store.delete_remote_odoo("https://odoo.example", revision=created.revision)
    assert store.lookup_remote_odoo("https://odoo.example").master_password == "new"  # type: ignore[union-attr]
