from __future__ import annotations

import os
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.db_name import validate_db_name, validate_filestore_containment


class TestValidateDbName:
    @pytest.mark.parametrize(
        "name",
        ["comerta", "comerta_cmrt_123", "db_1", "a", "A.b-c_d", "_under", "1bad"],
    )
    def test_valid_names(self, name: str) -> None:
        validate_db_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "../etc/passwd",
            "..",
            ".",
            "foo/bar",
            "foo\\bar",
            "foo\x00bar",
            "/abs",
            "-bad",
            "bad name",
        ],
    )
    def test_invalid_names(self, name: str) -> None:
        with pytest.raises((ConfigError, ValueError)):
            validate_db_name(name)

    def test_too_long(self) -> None:
        with pytest.raises(ConfigError):
            validate_db_name("a" * 64)

    def test_empty(self) -> None:
        with pytest.raises(ConfigError):
            validate_db_name("")

    def test_leading_dot(self) -> None:
        with pytest.raises(ConfigError):
            validate_db_name(".hidden")


class TestFilestoreContainment:
    def test_valid_containment(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        (data_dir / "filestore").mkdir(parents=True)
        result = validate_filestore_containment(data_dir, "mydb")
        assert result == (data_dir / "filestore" / "mydb").resolve()

    def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        (data_dir / "filestore").mkdir(parents=True)
        with pytest.raises(ConfigError):
            validate_filestore_containment(data_dir, "..")

    def test_symlink_escape_blocked(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        fs_root = data_dir / "filestore"
        fs_root.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        link = fs_root / "evil"
        os.symlink(outside, link)
        with pytest.raises(ConfigError):
            validate_filestore_containment(data_dir, "evil")
