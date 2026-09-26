from __future__ import annotations

import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from odoo_instance_sdk.exceptions import (
    BackupNotAvailableError,
    BackupNotFoundError,
    ConfigError,
    LockConflictError,
)
from odoo_instance_sdk.internal.locks import backup_lock_path, exclusive_lock
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import BackupDeletionResult
from odoo_instance_sdk.resources.backup import BackupResource
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.fixtures import make_backup

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


@pytest.fixture
def sample_backup_entry(
    client: OdooClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[dict[str, object], None, None]:
    db_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: db_path
    )
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_backups_dir", lambda **_kwargs: backups
    )
    backup_file = backups / "real_backup.zip"
    backup_file.write_bytes(b"x")
    bid = str(uuid.uuid4())
    catalog = BackupCatalog(db_path=db_path)
    catalog.start_download(
        bid,
        "http://localhost:8069",
        "mydb",
        "zip",
        True,
        backup_file,
    )
    catalog.success_download(bid, "real_backup.zip", 1024, "abc123")
    catalog.close()
    client._catalog = None
    yield {"path": backup_file, "db_path": db_path, "client": client, "tmp": tmp_path, "id": bid}


def test_list_empty(client: OdooClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: db_path
    )
    client._catalog = None
    res = BackupResource(_client=client)
    backups = res.list()
    assert backups == ()


def test_public_retention_read_update_preview_and_permissions(
    client: OdooClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir(mode=0o700)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_config_root",
        lambda **_kwargs: config_root,
    )
    settings = config_root / "user.toml"
    settings.write_text('[other]\nkeep = "yes"\n\n[backup]\nmax_uncompressed_bytes = 42\n')

    current = client.backups.retention()
    assert current.retention_days == 14
    assert current.auto_prune is False

    before_preview = settings.read_text(encoding="utf-8")
    preview_command = client.backups.set_retention_command(
        retention_days=7, auto_prune=True, dry_run=True
    )
    assert preview_command.plan.steps[0].read_only is True
    preview = preview_command.run()
    assert preview.changed is False
    assert settings.read_text(encoding="utf-8") == before_preview

    updated = client.backups.set_retention(retention_days=7, auto_prune=True)
    assert updated.changed is True
    assert client.backups.retention().retention_days == 7
    content = settings.read_text(encoding="utf-8")
    assert 'keep = "yes"' in content
    assert "max_uncompressed_bytes = 42" in content
    assert settings.stat().st_mode & 0o777 == 0o600


def test_public_retention_rejects_invalid_and_unreadable_settings(
    client: OdooClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_config_root",
        lambda **_kwargs: config_root,
    )
    with pytest.raises(ConfigError, match="positive integer"):
        client.backups.set_retention(retention_days=0)
    with pytest.raises(ConfigError, match="positive integer"):
        client.backups.set_retention(retention_days=True)
    with pytest.raises(ConfigError, match="boolean"):
        client.backups.set_retention(auto_prune=1)  # type: ignore[arg-type]

    settings = config_root / "user.toml"
    settings.mkdir()
    with pytest.raises(ConfigError, match="unable to read"):
        client.backups.retention()


def test_list_with_entries(client: OdooClient, sample_backup_entry: dict[str, object]) -> None:
    res = BackupResource(_client=client)
    backups = res.list(source_base_url="http://localhost:8069")
    assert len(backups) == 1
    assert backups[0].database_name == "mydb"
    assert backups[0].id == uuid.UUID(cast("str", sample_backup_entry["id"]))


def test_latest_backup(client: OdooClient, sample_backup_entry: dict[str, object]) -> None:
    res = BackupResource(_client=client)
    latest = res.latest("http://localhost:8069", "mydb")
    assert latest is not None
    assert latest.database_name == "mydb"
    assert latest.id == uuid.UUID(cast("str", sample_backup_entry["id"]))


def test_latest_backup_none(
    client: OdooClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: db_path
    )
    client._catalog = None
    res = BackupResource(_client=client)
    assert res.latest("http://localhost:8069", "nonexistent") is None


def test_history_with_started_event(
    client: OdooClient, sample_backup_entry: dict[str, object]
) -> None:
    res = BackupResource(_client=client)
    events = res.history(backup_id=cast("str", sample_backup_entry["id"]))
    assert len(events) >= 1
    assert any(e.event_type.value == "download_started" for e in events)


def test_history_empty(client: OdooClient, sample_backup_entry: dict[str, object]) -> None:
    res = BackupResource(_client=client)
    events = res.history()
    sample_events = [
        e for e in events if e.event_type.value not in ("download_started", "download_succeeded")
    ]
    assert len(sample_events) == 0


def _make_direct_delete_row_non_newest(
    client: OdooClient, sample_backup_entry: dict[str, object], tmp_path: Path
) -> None:
    """Give legacy direct-delete cases owned history without weakening protections."""
    catalog = client.get_catalog()
    project_root = tmp_path / "direct-project"
    (project_root / ".git").mkdir(parents=True)
    project_id = f"project_{repo_key(project_root, project_root / '.git')}"
    catalog._register_project(project_id, project_root, project_root / ".git")
    catalog._conn.execute(
        "UPDATE backups SET project_id = ? WHERE id = ?",
        (project_id, sample_backup_entry["id"]),
    )
    newer_id = str(uuid.uuid4())
    newer_path = tmp_path / "backups" / "newer.zip"
    newer_path.write_bytes(b"newer")
    catalog.start_download(
        newer_id,
        "http://localhost:8069",
        "mydb",
        "zip",
        True,
        newer_path,
        project_id=project_id,
    )
    catalog.success_download(
        newer_id,
        newer_path.name,
        newer_path.stat().st_size,
        "newer-sha",
        downloaded_at=datetime.now(UTC) + timedelta(days=1),
    )


def test_delete_idempotent(client: OdooClient, sample_backup_entry: dict[str, object]) -> None:
    _make_direct_delete_row_non_newest(
        client, sample_backup_entry, Path(cast("Path", sample_backup_entry["tmp"]))
    )
    res = BackupResource(_client=client)
    backup = make_backup(
        id=uuid.UUID(cast("str", sample_backup_entry["id"])),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(cast("Path", sample_backup_entry["path"])),
        filename=cast("Path", sample_backup_entry["path"]).name,
        size_bytes=1024,
        sha256="abc123",
    )
    result = res.delete(backup)
    assert isinstance(result, BackupDeletionResult)
    assert result.already_deleted is False
    assert result.file_existed is True
    assert result.deleted_at is not None

    second = res.delete(backup)
    assert second.already_deleted is True
    assert second.deleted_at is not None


def test_list_skips_missing_file(
    client: OdooClient, sample_backup_entry: dict[str, object]
) -> None:
    cast("Path", sample_backup_entry["path"]).unlink()
    res = BackupResource(_client=client)
    backups = res.list(source_base_url="http://localhost:8069")
    assert backups == ()


def test_full_audit_visibility(client: OdooClient, sample_backup_entry: dict[str, object]) -> None:
    _make_direct_delete_row_non_newest(
        client, sample_backup_entry, Path(cast("Path", sample_backup_entry["tmp"]))
    )
    res = BackupResource(_client=client)
    backup = make_backup(
        id=uuid.UUID(cast("str", sample_backup_entry["id"])),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(cast("Path", sample_backup_entry["path"])),
        filename=cast("Path", sample_backup_entry["path"]).name,
        size_bytes=1024,
        sha256="abc123",
    )
    res.delete(backup)
    events = res.history(backup_id=cast("str", sample_backup_entry["id"]))
    kinds = [e.event_type.value for e in events]
    assert "download_started" in kinds
    assert "download_succeeded" in kinds
    assert "deleted" in kinds


def test_cross_process_rehydration(
    client: OdooClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: db_path
    )
    client._catalog = None
    backup_file = tmp_path / "across.zip"
    backup_file.write_bytes(b"x")
    bid = str(uuid.uuid4())

    catalog1 = BackupCatalog(db_path=db_path)
    catalog1.start_download(
        bid,
        "http://localhost:8069",
        "mydb",
        "zip",
        True,
        backup_file,
    )
    catalog1.success_download(bid, "across.zip", 2048, "def456")
    catalog1.close()

    res = BackupResource(_client=client)
    backups = res.list(source_base_url="http://localhost:8069", database_name="mydb")
    assert len(backups) == 1
    assert backups[0].id == uuid.UUID(bid)
    assert backups[0].size_bytes == 2048


def test_backup_resource_repr(
    client: OdooClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: db_path
    )
    client._catalog = None
    res = BackupResource(_client=client)
    r = repr(res)
    assert "BackupResource" in r


def test_delete_unknown_id_raises(
    client: OdooClient, sample_backup_entry: dict[str, object]
) -> None:
    res = BackupResource(_client=client)
    backup = make_backup(
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(cast("Path", sample_backup_entry["path"])),
        filename=cast("Path", sample_backup_entry["path"]).name,
    )
    with pytest.raises(BackupNotFoundError):
        res.delete(backup)


def test_delete_missing_file_records_explicit_idempotent_outcome(
    client: OdooClient, sample_backup_entry: dict[str, object]
) -> None:
    _make_direct_delete_row_non_newest(
        client, sample_backup_entry, Path(cast("Path", sample_backup_entry["tmp"]))
    )
    path = cast("Path", sample_backup_entry["path"])
    path.unlink()
    res = BackupResource(_client=client)
    backup = make_backup(
        id=uuid.UUID(cast("str", sample_backup_entry["id"])),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(path),
        filename=path.name,
        size_bytes=1024,
        sha256="abc123",
    )

    result = res.delete(backup)

    assert result.file_existed is False
    assert result.already_deleted is False
    row = client.get_catalog().get_by_id(str(backup.id))
    assert row is not None
    assert row["state"] == "deleted"


def test_delete_refuses_downloading_backup_without_mutation(
    client: OdooClient, sample_backup_entry: dict[str, object]
) -> None:
    catalog = client.get_catalog()
    path = cast("Path", sample_backup_entry["path"])
    downloading_id = str(uuid.uuid4())
    catalog.start_download(
        downloading_id,
        "http://localhost:8069",
        "mydb",
        "zip",
        True,
        path,
    )
    backup = make_backup(
        id=uuid.UUID(downloading_id),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(path),
        filename=path.name,
        size_bytes=1024,
        sha256="abc123",
    )

    with pytest.raises(BackupNotAvailableError, match="downloading"):
        BackupResource(_client=client).delete(backup)

    row = catalog.get_by_id(downloading_id)
    assert row is not None
    assert row["state"] == "downloading"
    assert path.is_file()


def test_delete_lock_is_shared_and_rejects_busy_backup(
    client: OdooClient, sample_backup_entry: dict[str, object]
) -> None:
    _make_direct_delete_row_non_newest(
        client, sample_backup_entry, Path(cast("Path", sample_backup_entry["tmp"]))
    )
    path = cast("Path", sample_backup_entry["path"])
    backup = make_backup(
        id=uuid.UUID(cast("str", sample_backup_entry["id"])),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(path),
        filename=path.name,
        size_bytes=1024,
        sha256="abc123",
    )

    with exclusive_lock(backup_lock_path(str(backup.id))), pytest.raises(LockConflictError):
        BackupResource(_client=client).delete(backup)

    assert path.is_file()
    row = client.get_catalog().get_by_id(str(backup.id))
    assert row is not None
    assert row["state"] == "available"


def test_validate_uses_the_same_backup_lock(
    client: OdooClient, sample_backup_entry: dict[str, object]
) -> None:
    _make_direct_delete_row_non_newest(
        client, sample_backup_entry, Path(cast("Path", sample_backup_entry["tmp"]))
    )
    path = cast("Path", sample_backup_entry["path"])
    backup = make_backup(
        id=uuid.UUID(cast("str", sample_backup_entry["id"])),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(path),
        filename=path.name,
        size_bytes=1024,
        sha256="abc123",
    )

    with (
        exclusive_lock(backup_lock_path(str(backup.id))),
        pytest.raises(LockConflictError),
    ):
        BackupResource(_client=client).validate(backup)


def test_delete_rejects_symlink_without_audit_mutation(
    client: OdooClient, sample_backup_entry: dict[str, object], tmp_path: Path
) -> None:
    _make_direct_delete_row_non_newest(
        client, sample_backup_entry, Path(cast("Path", sample_backup_entry["tmp"]))
    )
    path = cast("Path", sample_backup_entry["path"])
    target = tmp_path / "outside.zip"
    target.write_bytes(b"outside")
    path.unlink()
    path.symlink_to(target)
    backup = make_backup(
        id=uuid.UUID(cast("str", sample_backup_entry["id"])),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(path),
        filename=path.name,
        size_bytes=1024,
        sha256="abc123",
    )

    with pytest.raises(BackupNotAvailableError, match="symlink"):
        BackupResource(_client=client).delete(backup)

    row = client.get_catalog().get_by_id(str(backup.id))
    assert row is not None
    assert row["state"] == "available"
    assert target.is_file()


def test_delete_rejects_replaced_file_without_audit_mutation(
    client: OdooClient,
    sample_backup_entry: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_direct_delete_row_non_newest(
        client, sample_backup_entry, Path(cast("Path", sample_backup_entry["tmp"]))
    )
    path = cast("Path", sample_backup_entry["path"])
    backup = make_backup(
        id=uuid.UUID(cast("str", sample_backup_entry["id"])),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(path),
        filename=path.name,
        size_bytes=1024,
        sha256="abc123",
    )

    original_lock = exclusive_lock

    @contextmanager
    def replace_before_lock(lock_path: Path) -> Iterator[None]:
        replacement = path.with_name("replacement.zip")
        replacement.write_bytes(b"replacement")
        path.unlink()
        replacement.rename(path)
        with original_lock(lock_path):
            yield

    monkeypatch.setattr("odoo_instance_sdk.resources.backup.exclusive_lock", replace_before_lock)

    with pytest.raises(BackupNotAvailableError, match="identity"):
        BackupResource(_client=client).delete(backup)

    row = client.get_catalog().get_by_id(str(backup.id))
    assert row is not None
    assert row["state"] == "available"
    assert path.is_file()
    assert not any(
        event.event_type.value == "deleted"
        for event in BackupResource(_client=client).history(backup_id=str(backup.id))
    )


def test_delete_filesystem_failure_does_not_record_deleted(
    client: OdooClient, sample_backup_entry: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_direct_delete_row_non_newest(
        client, sample_backup_entry, Path(cast("Path", sample_backup_entry["tmp"]))
    )
    path = cast("Path", sample_backup_entry["path"])
    backup = make_backup(
        id=uuid.UUID(cast("str", sample_backup_entry["id"])),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(path),
        filename=path.name,
        size_bytes=1024,
        sha256="abc123",
    )

    def refuse_unlink(self: Path, *, missing_ok: bool = False) -> None:
        if self == path:
            raise PermissionError("permission denied")
        Path.unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse_unlink)
    with pytest.raises(PermissionError):
        BackupResource(_client=client).delete(backup)

    row = client.get_catalog().get_by_id(str(backup.id))
    assert row is not None
    assert row["state"] == "available"
    assert path.is_file()
    assert not any(
        event.event_type.value == "deleted"
        for event in BackupResource(_client=client).history(backup_id=str(backup.id))
    )
