from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.internal import pgadmin_files
from tests.fixtures.backups import write_fixtures as write_backup_fixtures
from tests.fixtures.pg_restore import write_fixtures as write_pg_restore_fixtures

from .pgadmin_test_support import _paths


@pytest.fixture
def backup_fixtures(tmp_path: Path) -> dict[str, Path]:
    return write_backup_fixtures(tmp_path / "backups")


@pytest.fixture
def pg_restore_fixtures(tmp_path: Path) -> dict[str, Path]:
    return write_pg_restore_fixtures(tmp_path / "pg_restore")


@pytest.fixture
def local_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> pgadmin_files.PgAdminPaths:
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: tmp_path)
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: False)
    return _paths(tmp_path)
