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
def instance_remote(client):
    return client.instance("http://example.com:8069", master_password="admin")
