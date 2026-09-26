from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import BackupNotAvailableError, StalePlanError
from odoo_instance_sdk.internal.backup_retention import write_retention_policy
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import BackupRetentionPolicy, BackupState
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _catalogue(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BackupCatalog:
    db_path = tmp_path / "catalog.sqlite3"
    locks = tmp_path / "locks"
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: db_path
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_locks_dir", lambda **_kwargs: locks)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_config_root", lambda **_kwargs: config
    )
    client._catalog = None
    return client.get_catalog()


def _project(catalog: BackupCatalog, tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    project_id = f"project_{repo_key(root, root / '.git')}"
    catalog._register_project(project_id, root, root / ".git")
    return root, project_id


def _backup(
    catalog: BackupCatalog,
    tmp_path: Path,
    project_id: str,
    when: datetime,
    *,
    source_name: str | None = None,
) -> tuple[str, Path]:
    backup_id = str(uuid.uuid4())
    path = tmp_path / f"{backup_id}.zip"
    payload = backup_id.encode()
    path.write_bytes(payload)
    catalog.start_download(
        backup_id,
        "https://odoo.example",
        "database",
        "zip",
        True,
        path,
        source_name=source_name,
        project_id=project_id,
    )
    catalog.success_download(
        backup_id,
        path.name,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        downloaded_at=when,
    )
    return backup_id, path


def test_pin_is_idempotent_and_audited(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    _root, project_id = _project(catalog, tmp_path)
    backup_id, _path = _backup(catalog, tmp_path, project_id, datetime.now(UTC))

    first = client.backups.set_pinned(backup_id, True)
    second = client.backups.set_pinned(backup_id, True)
    third = client.backups.set_pinned(backup_id, False)

    assert first.pinned is True and first.changed is True
    assert second.changed is False
    assert third.pinned is False and third.changed is True
    events = client.backups.history(backup_id=backup_id)
    pin_events = [event for event in events if event.event_type.value == "pin_set"]
    assert [event.message for event in reversed(pin_events)] == ["pinned=true", "pinned=false"]


def test_prune_keeps_newest_each_group_and_removes_only_old_files(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    newest = now - timedelta(days=20)
    old_id, old_path = _backup(catalog, tmp_path, project_id, old)
    newest_id, newest_path = _backup(catalog, tmp_path, project_id, newest)
    named_old_id, named_old_path = _backup(
        catalog, tmp_path, project_id, old, source_name="staging"
    )
    _named_newest_id, named_newest_path = _backup(
        catalog, tmp_path, project_id, newest, source_name="staging"
    )
    settings = tmp_path / "config" / "user.toml"
    write_retention_policy(
        BackupRetentionPolicy(retention_days=14, auto_prune=False, path=str(settings))
    )

    result = client.backups.prune(root)

    assert set(result.deleted_ids) == {uuid.UUID(old_id), uuid.UUID(named_old_id)}
    assert result.removed_bytes == len(old_id.encode()) + len(named_old_id.encode())
    assert not old_path.exists() and not named_old_path.exists()
    assert newest_path.exists() and named_newest_path.exists()
    assert catalog.get_by_id(old_id)["state"] == BackupState.DELETED.value  # type: ignore[index]
    assert catalog.get_by_id(newest_id)["state"] == BackupState.AVAILABLE.value  # type: ignore[index]


def test_prune_revalidates_policy_and_direct_delete_protects_pinned_backup(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    backup_id, path = _backup(catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=30))
    _backup(catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=20))
    settings = tmp_path / "config" / "user.toml"
    write_retention_policy(
        BackupRetentionPolicy(retention_days=14, auto_prune=False, path=str(settings))
    )
    command = client.backups.prune_command(root)
    write_retention_policy(
        BackupRetentionPolicy(retention_days=7, auto_prune=False, path=str(settings))
    )
    with pytest.raises(StalePlanError):
        command.run()

    client.backups.set_pinned(backup_id, True)
    backup = next(item for item in client.backups.list() if str(item.id) == backup_id)
    with pytest.raises(BackupNotAvailableError, match="pinned"):
        client.backups.delete(backup)
    assert path.exists()


def test_prune_preview_does_not_delete_or_write_audit(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    backup_id, path = _backup(catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=30))
    settings = tmp_path / "config" / "user.toml"
    write_retention_policy(
        BackupRetentionPolicy(retention_days=14, auto_prune=False, path=str(settings))
    )
    before = len(client.backups.history(backup_id=backup_id))

    result = client.backups.prune(root, dry_run=True)

    assert result.dry_run is True
    assert result.deleted_ids == ()
    assert path.exists()
    assert len(client.backups.history(backup_id=backup_id)) == before
