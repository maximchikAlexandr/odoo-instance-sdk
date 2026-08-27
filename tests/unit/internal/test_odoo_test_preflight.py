from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.test_selection import preflight_installed_modules


def _instance(*names: str) -> Any:
    return SimpleNamespace(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            configured_database_names=names,
            db_host="127.0.0.1",
            db_port=5432,
            db_user="odoo",
            db_password="secret",
        )
    )


def _completed(
    stdout: str = "sale\n", *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["psql"], returncode, stdout, stderr)


def test_preflight_targets_bound_database_with_one_read_only_query() -> None:
    with patch(
        "odoo_instance_sdk.internal.test_selection.run_psql",
        return_value=_completed("sale\nstock\n"),
    ) as query:
        result = preflight_installed_modules(_instance("bound_db"), ("sale", "stock"))

    assert result.database == "bound_db"
    assert result.installed_modules == ("sale", "stock")
    query.assert_called_once()
    call = query.call_args.kwargs
    assert call["database"] == "bound_db"
    assert "SELECT name FROM ir_module_module" in call["query"]
    assert "state = 'installed'" in call["query"]
    assert "UPDATE" not in call["query"]
    assert "INSERT" not in call["query"]
    assert "DELETE" not in call["query"]


@pytest.mark.parametrize("names", [(), ("one", "two")])
def test_preflight_rejects_missing_or_ambiguous_database_without_query(
    names: tuple[str, ...],
) -> None:
    with (
        patch("odoo_instance_sdk.internal.test_selection.run_psql") as query,
        pytest.raises(ConfigError, match="exactly one configured database"),
    ):
        preflight_installed_modules(_instance(*names), ("sale",))
    query.assert_not_called()


def test_preflight_rejects_missing_or_uninstalled_module() -> None:
    with (
        patch(
            "odoo_instance_sdk.internal.test_selection.run_psql",
            return_value=_completed("sale\n"),
        ),
        pytest.raises(ConfigError, match="not installed"),
    ):
        preflight_installed_modules(_instance("bound_db"), ("sale", "stock"))


def test_preflight_rejects_invalid_or_unsorted_modules_before_query() -> None:
    for modules in (("stock", "sale"), ("sale", "sale"), ("sale-bad",)):
        with (
            patch("odoo_instance_sdk.internal.test_selection.run_psql") as query,
            pytest.raises(ConfigError),
        ):
            preflight_installed_modules(_instance("bound_db"), modules)
        query.assert_not_called()


def test_preflight_sanitizes_query_failure_and_does_not_spawn_odoo() -> None:
    with (
        patch(
            "odoo_instance_sdk.internal.test_selection.run_psql",
            return_value=_completed(
                returncode=2,
                stderr="password='secret' token=hidden /private/runtime/odoo.log",
            ),
        ),
        pytest.raises(ConfigError) as raised,
    ):
        preflight_installed_modules(_instance("bound_db"), ("sale",))
    message = str(raised.value)
    assert "secret" not in message
    assert "hidden" not in message
    assert "/private/runtime/odoo.log" not in message


def test_preflight_tool_unavailable_is_actionable() -> None:
    with (
        patch("odoo_instance_sdk.internal.test_selection.run_psql", return_value=None),
        pytest.raises(ConfigError, match="could not run bounded PostgreSQL query"),
    ):
        preflight_installed_modules(_instance("bound_db"), ("sale",))
