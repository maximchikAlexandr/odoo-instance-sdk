from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.backup_validation import (
    validate_dump,
    validate_zip,
)


class TestZipValidation:
    def test_valid_zip(self, backup_fixtures: dict[str, Path]) -> None:
        result = validate_zip(backup_fixtures["valid.zip"])
        assert result.valid is True
        assert result.db_name == "testdb"

    def test_large_odoo_dump_has_no_arbitrary_size_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dump = zipfile.ZipInfo("dump.sql")
        dump.file_size = 100 * 1024**3
        dump.compress_size = 2 * 1024**3
        dump.compress_type = zipfile.ZIP_DEFLATED
        manifest = zipfile.ZipInfo("manifest.json")
        manifest.file_size = 64
        manifest.compress_size = 64
        manifest.compress_type = zipfile.ZIP_DEFLATED

        class LargeBackup:
            def __enter__(self) -> LargeBackup:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def infolist(self) -> list[zipfile.ZipInfo]:
                return [dump, manifest]

            def open(self, name: str) -> io.BytesIO:
                assert name == "manifest.json"
                return io.BytesIO(b'{"db_name":"large","version":"13.0","major_version":"13.0"}')

            def testzip(self) -> None:
                return None

        monkeypatch.setattr(zipfile, "is_zipfile", lambda _path: True)
        monkeypatch.setattr(zipfile, "ZipFile", lambda _path: LargeBackup())
        monkeypatch.setattr(
            shutil,
            "disk_usage",
            lambda _path: shutil._ntuple_diskusage(200 * 1024**3, 0, 200 * 1024**3),
        )

        result = validate_zip(tmp_path / "large.zip")

        assert result.valid is True
        assert result.db_version == "13.0"
        assert result.uncompressed_bytes > 100_000_000_000

    def test_missing_member(self, backup_fixtures: dict[str, Path]) -> None:
        result = validate_zip(backup_fixtures["missing_member.zip"])
        assert result.valid is False
        assert any("dump.sql" in e for e in result.errors)

    def test_invalid_manifest(self, backup_fixtures: dict[str, Path]) -> None:
        result = validate_zip(backup_fixtures["invalid_manifest.zip"])
        assert result.valid is False

    def test_corrupted_zip(self, backup_fixtures: dict[str, Path]) -> None:
        result = validate_zip(backup_fixtures["corrupted.zip"])
        assert result.valid is False
        assert len(result.errors) > 0

    def test_not_a_zip(self, tmp_path: Path) -> None:
        f = tmp_path / "not.zip"
        f.write_text("not a zip file")
        result = validate_zip(f)
        assert result.valid is False
        assert "Not a valid ZIP" in str(result.errors)


class TestDumpValidation:
    def test_unavailable_when_pg_restore_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: None,
        )
        result = validate_dump(Path("/nonexistent"), raise_if_unavailable=False)
        assert result.unavailable is True

    def test_raise_if_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: None,
        )
        from odoo_instance_sdk.exceptions import BackupValidationUnavailableError

        with pytest.raises(BackupValidationUnavailableError):
            validate_dump(Path("/nonexistent"), raise_if_unavailable=True)

    def test_valid_dump_with_fake_pg_restore(
        self, pg_restore_fixtures: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_exit0"]),
        )
        result = validate_dump(
            Path("/nonexistent"),
            timeout=5.0,
        )
        assert result.valid is True

    def test_failing_pg_restore(
        self, pg_restore_fixtures: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "dummy.zip"
        f.write_text("not a backup")
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_exit1"]),
        )
        result = validate_dump(f, timeout=5.0)
        assert result.valid is False

    def test_dump_timeout(
        self, pg_restore_fixtures: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "dummy.zip"
        f.write_text("not a backup")
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_timeout"]),
        )
        result = validate_dump(f, timeout=1.0)
        assert result.valid is False
        assert any("timed out" in e for e in result.errors)
