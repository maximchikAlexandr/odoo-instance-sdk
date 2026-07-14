from __future__ import annotations

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig


@pytest.fixture
def config():
    return OdooClientConfig(executable="/usr/bin/odoo")


@pytest.fixture
def client(config):
    return OdooClient(config=config)


@pytest.fixture
def instance(client):
    return client.instance("http://localhost:8069", master_password="admin")


@pytest.fixture
def instance_no_pwd(client):
    return client.instance("http://127.0.0.1:8069")


@pytest.fixture
def instance_remote(client):
    return client.instance("http://example.com:8069", master_password="admin")


@pytest.fixture
def backup_fixtures(tmp_path):
    from tests.fixtures.backups import write_fixtures as write_backup_fixtures

    return write_backup_fixtures(tmp_path / "backups")


@pytest.fixture
def pg_restore_fixtures(tmp_path):
    from tests.fixtures.pg_restore import write_fixtures as write_pg_restore_fixtures

    return write_pg_restore_fixtures(tmp_path / "pg_restore")
