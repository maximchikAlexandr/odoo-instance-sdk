from __future__ import annotations

import os
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.db_name import validate_db_name, validate_filestore_containment
from tests.cases.normalization import INVALID_DB_NAMES, VALID_DB_NAMES


@pytest.mark.parametrize("name", VALID_DB_NAMES)
def test_valid_db_names(name: str) -> None:
    validate_db_name(name)


@pytest.mark.parametrize("name", INVALID_DB_NAMES)
def test_invalid_db_names(name: str) -> None:
    with pytest.raises((ConfigError, ValueError)):
        validate_db_name(name)


def test_db_name_too_long() -> None:
    with pytest.raises(ConfigError):
        validate_db_name("a" * 64)


def test_filestore_containment_valid(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "filestore").mkdir(parents=True)
    assert (
        validate_filestore_containment(data_dir, "mydb")
        == (data_dir / "filestore" / "mydb").resolve()
    )


def test_filestore_path_traversal_blocked(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "filestore").mkdir(parents=True)
    with pytest.raises(ConfigError):
        validate_filestore_containment(data_dir, "..")


def test_filestore_symlink_escape_blocked(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    fs_root = data_dir / "filestore"
    fs_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, fs_root / "evil")
    with pytest.raises(ConfigError):
        validate_filestore_containment(data_dir, "evil")
