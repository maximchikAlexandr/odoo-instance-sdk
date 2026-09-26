from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import BackupNotAvailableError, StalePlanError
from odoo_instance_sdk.internal.backup_retention import write_retention_policy
from odoo_instance_sdk.internal.locks import backup_lock_path, exclusive_lock
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import BackupRetentionPolicy, BackupState
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from odoo_instance_sdk.storage.catalog.helpers import CopyJournalStage
from tests.unit.monitor_support import make_env


def _catalogue(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BackupCatalog:
    db_path = tmp_path / "catalog.sqlite3"
    locks = tmp_path / "locks"
    config = tmp_path / "config"
    backups = tmp_path / "backups"
    config.mkdir()
    backups.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: db_path
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_locks_dir", lambda **_kwargs: locks)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_config_root", lambda **_kwargs: config
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_backups_dir", lambda **_kwargs: backups
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
    path = tmp_path / "backups" / f"{backup_id}.zip"
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

    assert set(result.deleted_ids) == {uuid.UUID(old_id), uuid.UUID(named_old_id)}, result
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


def test_prune_cutoff_equality_and_deterministic_tie_protection(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    now = datetime(2026, 1, 31, tzinfo=UTC)
    policy = BackupRetentionPolicy(retention_days=14, auto_prune=False)
    _at_cutoff_id, _at_cutoff_path = _backup(
        catalog, tmp_path, project_id, now - timedelta(days=14), source_name="cutoff"
    )
    tie_a, _tie_a_path = _backup(catalog, tmp_path, project_id, now - timedelta(days=20))
    tie_b, _tie_b_path = _backup(catalog, tmp_path, project_id, now - timedelta(days=20))

    plan = client.backups._build_prune_plan(root, policy=policy, now=now)

    assert any(item.reason == "younger than retention cutoff" for item in plan.skipped)
    assert {str(item.backup_id) for item in plan.protected} == {min(tie_a, tie_b)}
    assert {str(item.backup_id) for item in plan.candidates} == {max(tie_a, tie_b)}


def test_prune_rejects_unknown_unowned_external_and_unlisted_files(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    old = datetime.now(UTC) - timedelta(days=30)
    unowned_id = str(uuid.uuid4())
    unowned_path = tmp_path / "backups" / "unowned.zip"
    unowned_path.write_bytes(b"unowned")
    catalog.start_download(
        unowned_id, "https://odoo.example", "database", "zip", True, unowned_path
    )
    catalog.success_download(unowned_id, unowned_path.name, 7, "unowned", downloaded_at=old)
    external_id, _external_managed_path = _backup(catalog, tmp_path, project_id, old)
    external_path = tmp_path / "external.zip"
    external_path.write_bytes(b"external")
    catalog._conn.execute(
        "UPDATE backups SET path = ? WHERE id = ?", (str(external_path), external_id)
    )
    local_archive = tmp_path / "local-archive.zip"
    local_archive.write_bytes(b"not in catalog")
    settings = tmp_path / "config" / "user.toml"
    policy = BackupRetentionPolicy(retention_days=14, auto_prune=False, path=str(settings))

    unowned = next(item for item in client.backups.list() if str(item.id) == unowned_id)
    with pytest.raises(BackupNotAvailableError, match="unknown or unowned"):
        client.backups.delete(unowned)
    plan = client.backups._build_prune_plan(root, policy=policy, now=datetime.now(UTC))

    assert unowned_id not in {str(item.backup_id) for item in plan.candidates}
    assert any(item.backup_id == uuid.UUID(external_id) for item in plan.skipped)
    assert local_archive.exists()


def test_prune_protects_busy_lock_and_live_environment_recovery_references(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    backup_id, _path = _backup(
        catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=30)
    )
    _other_id, _other_path = _backup(
        catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=29)
    )
    policy = BackupRetentionPolicy(retention_days=14, auto_prune=False)
    busy_id = backup_id
    with exclusive_lock(backup_lock_path(busy_id)):
        busy_plan = client.backups._build_prune_plan(root, policy=policy)
    assert any(item.backup_id == uuid.UUID(busy_id) for item in busy_plan.protected)
    assert any(item.reason == "busy lifecycle lock" for item in busy_plan.protected)

    environment_id = str(uuid.uuid4())
    catalog.create_environment(
        make_env(
            environment_id,
            repository_root=str(root),
            git_common_dir=str(root / ".git"),
            backup_id=backup_id,
        )
    )
    assert catalog.deletion_protection_reason(backup_id) == (
        "referenced by a non-removed environment"
    )
    catalog.update_environment_state(environment_id, "removed")
    catalog.upsert_copy_journal(
        environment_id,
        target_database="database",
        db_host=None,
        db_port=5432,
        db_user=None,
        backup_id=backup_id,
        stage=CopyJournalStage.PREPARED,
    )
    assert catalog.deletion_protection_reason(backup_id) == "referenced by unresolved recovery"


def test_prune_returns_truthful_partial_result_after_policy_drift(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    now = datetime.now(UTC)
    first_id, first_path = _backup(catalog, tmp_path, project_id, now - timedelta(days=30))
    second_id, second_path = _backup(catalog, tmp_path, project_id, now - timedelta(days=20))
    _newest_id, _newest_path = _backup(catalog, tmp_path, project_id, now - timedelta(days=5))
    settings = tmp_path / "config" / "user.toml"
    original = BackupRetentionPolicy(retention_days=14, auto_prune=False, path=str(settings))
    changed = BackupRetentionPolicy(retention_days=7, auto_prune=False, path=str(settings))
    write_retention_policy(original)
    command = client.backups.prune_command(root)
    policies = iter((original, original, changed))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.backup_retention.read_retention_policy",
        lambda: next(policies),
    )

    result = command.run()

    assert result.policy_changed is True
    assert result.warnings == ("backup retention policy changed; replan before pruning",)
    assert len(result.deleted_ids) == 1
    assert result.deleted_ids[0] in {uuid.UUID(first_id), uuid.UUID(second_id)}
    deleted_candidate = next(
        item for item in result.plan.candidates if item.backup_id == result.deleted_ids[0]
    )
    assert result.removed_bytes == deleted_candidate.size_bytes
    assert first_path.exists() != second_path.exists()
    assert any(event.event_type.value == "deleted" for event in client.backups.history())


def test_prune_revalidates_pin_latest_file_identity_and_is_repeatable(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    old = datetime.now(UTC) - timedelta(days=30)
    pinned_id, pinned_path = _backup(catalog, tmp_path, project_id, old)
    raced_id, raced_path = _backup(catalog, tmp_path, project_id, old)
    _backup(catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=5))
    settings = tmp_path / "config" / "user.toml"
    policy = BackupRetentionPolicy(retention_days=14, auto_prune=False, path=str(settings))
    plan = client.backups._build_prune_plan(root, policy=policy)
    client.backups.set_pinned(pinned_id, True)
    raced_path.unlink()
    raced_path.write_bytes(b"replacement")

    first = client.backups._execute_prune_plan(plan, dry_run=False)

    assert uuid.UUID(pinned_id) not in first.deleted_ids
    assert uuid.UUID(raced_id) not in first.deleted_ids
    assert pinned_path.exists() and raced_path.exists()
    second = client.backups.prune(root)
    assert uuid.UUID(pinned_id) not in second.deleted_ids
    assert uuid.UUID(raced_id) not in second.deleted_ids


def test_prune_rechecks_reference_race_after_plan_and_preserves_audit(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    backup_id, path = _backup(catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=30))
    _backup(catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=20))
    policy = BackupRetentionPolicy(retention_days=14, auto_prune=False)
    plan = client.backups._build_prune_plan(root, policy=policy)
    environment_id = str(uuid.uuid4())
    catalog.create_environment(
        make_env(
            environment_id,
            repository_root=str(root),
            git_common_dir=str(root / ".git"),
            backup_id=backup_id,
        )
    )

    result = client.backups._execute_prune_plan(plan, dry_run=False)

    assert uuid.UUID(backup_id) not in result.deleted_ids
    assert any(
        item.backup_id == uuid.UUID(backup_id)
        and item.reason == "referenced by a non-removed environment"
        for item in result.skipped
    )
    assert path.exists()
    row = catalog.get_by_id(backup_id)
    assert row is not None and row["state"] == BackupState.AVAILABLE.value
    assert not any(
        event.event_type.value == "deleted" for event in client.backups.history(backup_id=backup_id)
    )


def test_prune_rechecks_newest_race_after_plan(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    backup_id, path = _backup(catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=30))
    newest_id, _newest_path = _backup(
        catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=20)
    )
    policy = BackupRetentionPolicy(retention_days=14, auto_prune=False)
    plan = client.backups._build_prune_plan(root, policy=policy)
    catalog.record_deletion(newest_id)

    result = client.backups._execute_prune_plan(plan, dry_run=False)

    assert uuid.UUID(backup_id) not in result.deleted_ids
    assert any(
        item.backup_id == uuid.UUID(backup_id)
        and item.reason == "newest available backup in historical source group"
        for item in result.skipped
    )
    assert path.exists()


def test_prune_execution_lock_race_skips_captured_candidate(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    backup_id, path = _backup(catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=30))
    _backup(catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=20))
    plan = client.backups._build_prune_plan(
        root, policy=BackupRetentionPolicy(retention_days=14, auto_prune=False)
    )

    with exclusive_lock(backup_lock_path(backup_id)):
        result = client.backups._execute_prune_plan(plan, dry_run=False)

    assert uuid.UUID(backup_id) not in result.deleted_ids
    assert any(
        item.backup_id == uuid.UUID(backup_id) and item.reason == "busy lifecycle lock"
        for item in result.skipped
    )
    assert path.exists()


def test_prune_partial_filesystem_failure_reports_exact_outcome_and_audit(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalogue(client, tmp_path, monkeypatch)
    root, project_id = _project(catalog, tmp_path)
    _first_id, _first_path = _backup(
        catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=30)
    )
    _second_id, _second_path = _backup(
        catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=20)
    )
    _backup(catalog, tmp_path, project_id, datetime.now(UTC) - timedelta(days=5))
    plan = client.backups._build_prune_plan(
        root, policy=BackupRetentionPolicy(retention_days=14, auto_prune=False)
    )
    first, second = plan.candidates

    original_unlink = Path.unlink

    def fail_second(path: Path, *, missing_ok: bool = False) -> None:
        if path == Path(second.path):
            raise PermissionError("simulated removal failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_second)
    result = client.backups._execute_prune_plan(plan, dry_run=False)

    assert result.deleted_ids == (first.backup_id,)
    assert result.failed_ids == (second.backup_id,)
    assert result.removed_bytes == first.size_bytes
    assert not Path(first.path).exists()
    assert Path(second.path).exists()
    first_row = catalog.get_by_id(str(first.backup_id))
    second_row = catalog.get_by_id(str(second.backup_id))
    assert first_row is not None and first_row["state"] == BackupState.DELETED.value
    assert second_row is not None and second_row["state"] == BackupState.AVAILABLE.value
    assert any(
        event.event_type.value == "deleted"
        for event in client.backups.history(backup_id=str(first.backup_id))
    )
    assert not any(
        event.event_type.value == "deleted"
        for event in client.backups.history(backup_id=str(second.backup_id))
    )
