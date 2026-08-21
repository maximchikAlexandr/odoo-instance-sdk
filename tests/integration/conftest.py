from __future__ import annotations

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
def instance_remote(client: OdooClient) -> OdooInstance:
    return client.instance("http://example.com:8069", master_password="admin")
