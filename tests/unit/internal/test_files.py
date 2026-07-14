from __future__ import annotations

import pytest

from odoo_instance_sdk.internal.files import (
    ensure_destination,
    extract_server_filename,
    make_download_filename,
)


class TestExtractServerFilename:
    def test_extract_rfc5987_filename(self) -> None:
        # UTF-8 charset prefix must be stripped
        assert extract_server_filename("attachment; filename*=UTF-8''na%C3%AFve.zip") == "naïve.zip"

    def test_extract_rfc5987_no_charset(self) -> None:
        # Plain filename* without charset
        assert extract_server_filename("attachment; filename*=test.zip") == "test.zip"

    def test_filename_star_takes_precedence(self) -> None:
        # filename* takes precedence over filename
        assert (
            extract_server_filename("attachment; filename=\"old.zip\"; filename*=UTF-8''new.zip")
            == "new.zip"
        )

    def test_plain_filename_still_works(self) -> None:
        # Plain filename still works
        assert extract_server_filename('attachment; filename="plain.zip"') == "plain.zip"

    def test_none_returns_none(self) -> None:
        assert extract_server_filename(None) is None


class TestMakeDownloadFilename:
    def test_backup_id_prefix_attached(self) -> None:
        name = make_download_filename("abc-123", "server.zip")
        assert name == "abc-123_server.zip"

    def test_unsafe_filename_falls_back(self) -> None:
        name = make_download_filename("abc-123", "../../etc/passwd")
        assert "/" not in name
        assert ".." not in name
        assert name.endswith("_odoo_backup.zip")

    def test_none_uses_default(self) -> None:
        name = make_download_filename("abc-123", None)
        assert name.endswith("_odoo_backup.zip")


class TestEnsureDestination:
    def test_resolves_within_base(self, tmp_path) -> None:
        dest = ensure_destination(tmp_path, "backup.zip")
        assert dest == (tmp_path / "backup.zip").resolve()

    def test_escape_rejected(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="escape"):
            ensure_destination(tmp_path, "../escape.zip")


if __name__ == "__main__":
    pytest.main([__file__])
