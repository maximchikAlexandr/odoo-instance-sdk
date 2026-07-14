from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.internal.backup_validation import (
    validate_dump,
    validate_zip,
)


class TestZipValidation:
    def test_valid_zip(self, backup_fixtures):
        result = validate_zip(backup_fixtures["valid.zip"])
        assert result.valid is True
        assert result.db_name == "testdb"

    def test_missing_member(self, backup_fixtures):
        result = validate_zip(backup_fixtures["missing_member.zip"])
        assert result.valid is False
        assert any("dump.sql" in e for e in result.errors)

    def test_invalid_manifest(self, backup_fixtures):
        result = validate_zip(backup_fixtures["invalid_manifest.zip"])
        assert result.valid is False

    def test_corrupted_zip(self, backup_fixtures):
        result = validate_zip(backup_fixtures["corrupted.zip"])
        assert result.valid is False
        assert len(result.errors) > 0

    def test_not_a_zip(self, tmp_path):
        f = tmp_path / "not.zip"
        f.write_text("not a zip file")
        result = validate_zip(f)
        assert result.valid is False
        assert "Not a valid ZIP" in str(result.errors)


class TestDumpValidation:
    def test_unavailable_when_pg_restore_missing(self, monkeypatch):
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: None,
        )
        result = validate_dump(Path("/nonexistent"), raise_if_unavailable=False)
        assert result.unavailable is True

    def test_raise_if_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: None,
        )
        from odoo_instance_sdk.exceptions import BackupValidationUnavailableError

        with pytest.raises(BackupValidationUnavailableError):
            validate_dump(Path("/nonexistent"), raise_if_unavailable=True)

    def test_valid_dump_with_fake_pg_restore(self, pg_restore_fixtures, monkeypatch):
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_exit0"]),
        )
        result = validate_dump(
            Path("/nonexistent"),
            timeout=5.0,
        )
        assert result.valid is True

    def test_failing_pg_restore(self, pg_restore_fixtures, tmp_path, monkeypatch):
        f = tmp_path / "dummy.zip"
        f.write_text("not a backup")
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_exit1"]),
        )
        result = validate_dump(f, timeout=5.0)
        assert result.valid is False

    def test_dump_timeout(self, pg_restore_fixtures, tmp_path, monkeypatch):
        f = tmp_path / "dummy.zip"
        f.write_text("not a backup")
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_timeout"]),
        )
        result = validate_dump(f, timeout=1.0)
        assert result.valid is False
        assert any("timed out" in e for e in result.errors)
