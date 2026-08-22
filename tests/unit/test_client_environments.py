from __future__ import annotations

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


def test_environment_resource_has_no_run_shell_methods() -> None:
    client = _make_client()
    assert not hasattr(client.environments, "run")
    assert not hasattr(client.environments, "shell")
    assert not hasattr(client.environments, "start")
    assert not hasattr(client.environments, "stop")
