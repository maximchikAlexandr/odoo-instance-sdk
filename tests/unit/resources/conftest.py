from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance


@pytest.fixture
def config() -> OdooClientConfig:
    return OdooClientConfig(executable="/usr/bin/odoo")


@pytest.fixture
def client(config: OdooClientConfig) -> OdooClient:
    return OdooClient(config=config)


@pytest.fixture
def instance(client: OdooClient) -> OdooInstance:
    return client.instance("http://localhost:8069", master_password="admin")


@pytest.fixture
def instance_no_pwd(client: OdooClient) -> OdooInstance:
    return client.instance("http://127.0.0.1:8069")


@pytest.fixture
def instance_remote(client: OdooClient) -> OdooInstance:
    return client.instance("http://example.com:8069", master_password="admin")


@pytest.fixture
def backup_fixtures(tmp_path: Path) -> dict[str, Path]:
    from tests.fixtures.backups import write_fixtures as write_backup_fixtures

    return write_backup_fixtures(tmp_path / "backups")


@pytest.fixture
def pg_restore_fixtures(tmp_path: Path) -> dict[str, Path]:
    from tests.fixtures.pg_restore import write_fixtures as write_pg_restore_fixtures

    return write_pg_restore_fixtures(tmp_path / "pg_restore")
