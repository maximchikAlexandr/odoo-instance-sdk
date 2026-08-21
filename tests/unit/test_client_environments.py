from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentResource,
    EnvironmentState,
)


def _make_client() -> OdooClient:
    return OdooClient(config=OdooClientConfig(executable="python3"))


def test_client_environments_is_environment_resource() -> None:
    client = _make_client()
    assert isinstance(client.environments, EnvironmentResource)


def test_client_catalog_absent() -> None:
    client = _make_client()
    assert not hasattr(client, "catalog")
    assert client._catalog is None


def test_types_importable() -> None:
    assert DevelopmentEnvironment is not None
    assert EnvironmentState.READY.value == "ready"
    assert EnvironmentDatabaseMode.SHARED.value == "shared"
    assert EnvironmentDatabaseMode.COPY.value == "copy"


def test_checkout_options_defaults() -> None:
    opts = EnvironmentCheckoutOptions()
    assert opts.db_mode == EnvironmentDatabaseMode.SHARED
    assert opts.create_venv is False


def test_environment_resource_methods_raise_not_implemented() -> None:
    client = _make_client()
    with pytest.raises(NotImplementedError):
        client.environments.checkout(project=Path("/repo"), branch="main")
    with pytest.raises(NotImplementedError):
        client.environments.get("test")
    with pytest.raises(NotImplementedError):
        client.environments.list()
    with pytest.raises(NotImplementedError):
        client.environments.remove("test")
    with pytest.raises(NotImplementedError):
        client.environments.sync_python("test")
