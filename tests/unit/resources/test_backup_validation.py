from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import BackupNotAvailableError
from odoo_instance_sdk.models import BackupFormat
from odoo_instance_sdk.resources.backup import BackupResource
from tests.fixtures import make_backup


def _make(file_path: Path, **overrides: object):
    return make_backup(
        source_base_url="http://localhost:8069",
        database_name="testdb",
        path=str(file_path),
        filename=file_path.name,
        sha256=hashlib.sha256(file_path.read_bytes()).hexdigest(),
        **overrides,
    )


def _register(client, backup) -> None:
    catalog = client.get_catalog()
    catalog.start_download(
        str(backup.id),
        backup.source_base_url,
        backup.database_name,
        backup.format.value,
        backup.filestore_requested,
        Path(backup.path),
    )
    catalog.success_download(str(backup.id), backup.filename, backup.size_bytes, backup.sha256)


class TestBackupResourceValidate:
    def test_validate_missing_file(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        backup_file = tmp_path / "vanished.zip"
        backup_file.write_bytes(b"x")
        backup = _make(backup_file)
        _register(client, backup)
        backup_file.unlink()
        res = BackupResource(_client=client)
        with pytest.raises(BackupNotAvailableError):
            res.validate(backup)

    def test_validate_valid_backup_zip(self, client, tmp_path, monkeypatch, backup_fixtures):
        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)

        backup_root = tmp_path / "backup_storage"
        backup_root.mkdir(parents=True, exist_ok=True)
        src = backup_fixtures["valid.zip"]
        dst = backup_root / "valid.zip"
        dst.write_bytes(src.read_bytes())

        backup = _make(dst)
        _register(client, backup)
        res = BackupResource(_client=client)
        result = res.validate(backup)
        assert result.valid is True

    def test_validate_invalid_manifest(self, client, tmp_path, monkeypatch, backup_fixtures):
        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        backup_root = tmp_path / "backup_storage"
        backup_root.mkdir(parents=True, exist_ok=True)
        src = backup_fixtures["invalid_manifest.zip"]
        dst = backup_root / "invalid_manifest.zip"
        dst.write_bytes(src.read_bytes())

        backup = _make(dst)
        _register(client, backup)
        res = BackupResource(_client=client)
        result = res.validate(backup)
        assert result.valid is False
        assert any("manifest" in e.lower() for e in result.errors)

    def test_validate_corrupted_zip(self, client, tmp_path, monkeypatch, backup_fixtures):
        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        backup_root = tmp_path / "backup_storage"
        backup_root.mkdir(parents=True, exist_ok=True)
        src = backup_fixtures["corrupted.zip"]
        dst = backup_root / "corrupted.zip"
        dst.write_bytes(src.read_bytes())

        backup = _make(dst)
        _register(client, backup)
        res = BackupResource(_client=client)
        result = res.validate(backup)
        assert result.valid is False

    def test_validate_missing_member(self, client, tmp_path, monkeypatch, backup_fixtures):
        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        backup_root = tmp_path / "backup_storage"
        backup_root.mkdir(parents=True, exist_ok=True)
        src = backup_fixtures["missing_member.zip"]
        dst = backup_root / "missing_member.zip"
        dst.write_bytes(src.read_bytes())

        backup = _make(dst)
        _register(client, backup)
        res = BackupResource(_client=client)
        result = res.validate(backup)
        assert result.valid is False
        assert any("Missing" in e for e in result.errors)

    def test_validate_dump_with_pg_restore_exit_zero(
        self, client, tmp_path, monkeypatch, pg_restore_fixtures
    ):
        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_exit0"]),
        )
        backup_root = tmp_path / "backup_storage"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_file = backup_root / "test.dump"
        backup_file.write_bytes(b"fake-dump")

        backup = _make(backup_file, format=BackupFormat.DUMP)
        _register(client, backup)
        res = BackupResource(_client=client)
        result = res.validate(backup)
        assert result.valid is True

    def test_validate_dump_with_pg_restore_exit_one(
        self, client, tmp_path, monkeypatch, pg_restore_fixtures
    ):
        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_exit1"]),
        )
        backup_root = tmp_path / "backup_storage"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_file = backup_root / "test.dump"
        backup_file.write_bytes(b"fake-dump")

        backup = _make(backup_file, format=BackupFormat.DUMP)
        _register(client, backup)
        res = BackupResource(_client=client)
        result = res.validate(backup)
        assert result.valid is False

    def test_validate_dump_timeout(self, client, tmp_path, monkeypatch, pg_restore_fixtures):
        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_timeout"]),
        )
        backup_root = tmp_path / "backup_storage"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_file = backup_root / "test.dump"
        backup_file.write_bytes(b"fake-dump")

        backup = _make(backup_file, format=BackupFormat.DUMP)
        _register(client, backup)
        res = BackupResource(_client=client)
        result = res.validate(backup, timeout=1.0)
        assert result.valid is False

    def test_validate_dump_unavailable(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: None,
        )
        backup_root = tmp_path / "backup_storage"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_file = backup_root / "test.dump"
        backup_file.write_bytes(b"fake-dump")

        backup = _make(backup_file, format=BackupFormat.DUMP)
        _register(client, backup)
        res = BackupResource(_client=client)
        result = res.validate(backup)
        assert result.valid is False

    def test_validate_dump_unavailable_raises(self, client, tmp_path, monkeypatch):
        from odoo_instance_sdk.exceptions import BackupValidationUnavailableError

        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: None,
        )
        backup_root = tmp_path / "backup_storage"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_file = backup_root / "test.dump"
        backup_file.write_bytes(b"fake-dump")

        backup = _make(backup_file, format=BackupFormat.DUMP)
        _register(client, backup)
        res = BackupResource(_client=client)
        with pytest.raises(BackupValidationUnavailableError):
            res.validate(backup, raise_if_unavailable=True)

    def test_validate_records_audit_event(self, client, tmp_path, monkeypatch, backup_fixtures):
        from odoo_instance_sdk.models import BackupEventType

        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        backup_root = tmp_path / "backup_storage"
        backup_root.mkdir(parents=True, exist_ok=True)
        src = backup_fixtures["valid.zip"]
        dst = backup_root / "valid.zip"
        dst.write_bytes(src.read_bytes())

        backup = _make(dst)
        _register(client, backup)
        res = BackupResource(_client=client)
        res.validate(backup)

        events = client.get_catalog().get_backup_history(backup_id=str(backup.id))
        kinds = [e.event_type for e in events]
        assert BackupEventType.VALIDATION_SUCCEEDED in kinds
