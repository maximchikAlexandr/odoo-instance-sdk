"""Tests for the optional OpenSpec checkout flag."""

from __future__ import annotations

import importlib

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.env.openspec_checkout import require_openspec_extension
from odoo_instance_sdk.exceptions import ConfigError


@pytest.mark.unit
def test_checkout_help_shows_openspec_change() -> None:
    result = CliRunner().invoke(cli, ["env", "checkout", "--help"])

    assert result.exit_code == 0
    assert "--openspec-change" in result.output


@pytest.mark.unit
def test_missing_openspec_extension_fails_with_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = importlib.import_module

    def fail_extension(name: str, package: str | None = None) -> object:
        if name.startswith("odcli_openspec"):
            raise ImportError(name)
        return real_import(name, package)

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.openspec_checkout.importlib.import_module",
        fail_extension,
    )

    with pytest.raises(ConfigError, match="uv tool install --with-executables-from odcli-openspec"):
        require_openspec_extension("demo-change")


@pytest.mark.unit
def test_openspec_change_id_rejects_a_path() -> None:
    with pytest.raises(ConfigError, match="not a path"):
        require_openspec_extension("../demo-change")
