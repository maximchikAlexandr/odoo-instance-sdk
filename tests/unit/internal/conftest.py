from __future__ import annotations

import pytest

from tests.fixtures.backups import write_fixtures as write_backup_fixtures
from tests.fixtures.pg_restore import write_fixtures as write_pg_restore_fixtures


@pytest.fixture
def backup_fixtures(tmp_path):
    return write_backup_fixtures(tmp_path / "backups")


@pytest.fixture
def pg_restore_fixtures(tmp_path):
    return write_pg_restore_fixtures(tmp_path / "pg_restore")
