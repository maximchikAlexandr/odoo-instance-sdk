from __future__ import annotations

import hashlib

import pytest

from odoo_instance_sdk.models import BackupFormat
from tests.fixtures import make_backup


class TestRestore:
    def test_remote_backup_to_local_restore(self, instance, tmp_path, monkeypatch, httpx_mock):

        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)

        from tests.fixtures.odoo_database_server import BACKUP_ZIP_CONTENT

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / "test_backup.zip"
        backup_file.write_bytes(BACKUP_ZIP_CONTENT)

        backup = make_backup(
            source_base_url="http://localhost:8069",
            database_name="testdb",
            path=str(backup_file),
            filename=backup_file.name,
            size_bytes=len(BACKUP_ZIP_CONTENT),
        )

        catalog = instance._client.get_catalog()
        catalog.start_download(
            str(backup.id),
            backup.source_base_url,
            backup.database_name,
            backup.format.value,
            backup.filestore_requested,
            backup.path,
        )
        catalog.success_download(str(backup.id), backup.filename, backup.size_bytes, backup.sha256)

        httpx_mock.add_response(
            url="http://localhost:8069/web/database/list",
            method="POST",
            json={"result": []},
        )

        httpx_mock.add_response(
            url="http://localhost:8069/web/database/restore",
            method="POST",
            json={"result": True},
        )

        httpx_mock.add_response(
            url="http://localhost:8069/web/database/list",
            method="POST",
            json={"result": ["testdb"]},
        )

        result = instance.databases.restore(backup, "testdb")
        assert result.new_db == "testdb"

    def test_forged_backup_rejected(self, instance, tmp_path, monkeypatch):

        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)

        backup_file = tmp_path / "forged.zip"
        backup_file.write_bytes(b"forged content")
        backup = make_backup(
            source_base_url="http://localhost:8069",
            database_name="testdb",
            path=str(backup_file),
            filename=backup_file.name,
        )

        from odoo_instance_sdk.exceptions import BackupNotFoundError

        with pytest.raises(BackupNotFoundError):
            instance.databases.restore(backup, "testdb")

    def test_existing_target_rejected(self, instance, tmp_path, monkeypatch, httpx_mock):

        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / "backup.zip"
        backup_file.write_bytes(b"fake-content")

        backup = make_backup(
            source_base_url="http://localhost:8069",
            database_name="existing_db",
            path=str(backup_file),
            filename=backup_file.name,
            size_bytes=12,
        )

        catalog = instance._client.get_catalog()
        catalog.start_download(
            str(backup.id),
            backup.source_base_url,
            backup.database_name,
            backup.format.value,
            backup.filestore_requested,
            backup.path,
        )
        catalog.success_download(str(backup.id), backup.filename, backup.size_bytes, backup.sha256)

        httpx_mock.add_response(
            url="http://localhost:8069/web/database/list",
            method="POST",
            json={"result": ["existing_db"]},
        )

        from odoo_instance_sdk.exceptions import DatabaseAlreadyExistsError

        with pytest.raises(DatabaseAlreadyExistsError):
            instance.databases.restore(backup, "existing_db")

    def test_remote_restore_rejected(self, instance_remote, tmp_path):
        backup_file = tmp_path / "test.zip"
        backup = make_backup(
            source_base_url="http://example.com:8069",
            database_name="testdb",
            path=str(backup_file),
            filename=backup_file.name,
        )

        from odoo_instance_sdk.exceptions import NonLocalInstanceError

        with pytest.raises(NonLocalInstanceError):
            instance_remote.databases.restore(backup, "testdb")


class TestDrop:
    def test_drop_success(self, instance, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8069/web/database/drop",
            method="POST",
            json={"result": True},
        )

        httpx_mock.add_response(
            url="http://localhost:8069/web/database/list",
            method="POST",
            json={"result": []},
        )

        result = instance.databases.drop("testdb")
        assert result.db == "testdb"

    def test_drop_nonexistent(self, instance, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8069/web/database/drop",
            method="POST",
            json={"result": True},
        )
        httpx_mock.add_response(
            url="http://localhost:8069/web/database/list",
            method="POST",
            json={"result": []},
        )

        result = instance.databases.drop("nonexistent")
        assert result.db == "nonexistent"

    def test_remote_drop_rejected(self, instance_remote):
        from odoo_instance_sdk.exceptions import NonLocalInstanceError

        with pytest.raises(NonLocalInstanceError):
            instance_remote.databases.drop("testdb")


class TestValidationUnavailable:
    def test_validation_unavailable_records_event(
        self, instance, tmp_path, monkeypatch, httpx_mock
    ):
        from odoo_instance_sdk.models import BackupState

        monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: None,
        )

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / "test.zip"
        backup_file.write_bytes(b"not a real dump")

        backup = make_backup(
            source_base_url="http://localhost:8069",
            database_name="testdb",
            path=str(backup_file),
            filename=backup_file.name,
            format=BackupFormat.DUMP,
            sha256=hashlib.sha256(b"not a real dump").hexdigest(),
        )

        catalog = instance._client.get_catalog()
        catalog.start_download(
            str(backup.id),
            backup.source_base_url,
            backup.database_name,
            backup.format.value,
            backup.filestore_requested,
            backup.path,
        )
        catalog.success_download(str(backup.id), backup.filename, backup.size_bytes, backup.sha256)

        from odoo_instance_sdk.resources.backup import BackupResource

        res = BackupResource(_client=instance._client)

        from odoo_instance_sdk.exceptions import BackupValidationUnavailableError

        with pytest.raises(BackupValidationUnavailableError):
            res.validate(backup, raise_if_unavailable=True)

        row = catalog.get_by_id(str(backup.id))
        assert row is not None
        assert row["state"] == BackupState.AVAILABLE.value

        events = catalog.get_backup_history(backup_id=str(backup.id))
        kinds = [e.event_type.value for e in events]
        assert "validation_unavailable" in kinds
