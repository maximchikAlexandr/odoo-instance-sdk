from __future__ import annotations

import tomllib
from pathlib import Path

from click.testing import CliRunner

from odoo_instance_sdk.cli import cli


def test_msgfmt_is_optional_and_not_declared_in_package_metadata() -> None:
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = metadata["project"]["dependencies"]
    optional = metadata["project"].get("optional-dependencies", {})
    declared = "\n".join(
        (*dependencies, *(item for values in optional.values() for item in values))
    )

    assert "msgfmt" not in declared.lower()
    assert "gettext" not in declared.lower()


def test_translation_command_has_no_gettext_wrapper_or_extra_flag() -> None:
    source = Path("src/odoo_instance_sdk/commands/translations.py").read_text(encoding="utf-8")

    assert "gettext" not in source.lower()
    assert "--msgfmt" not in source
    assert "download" not in source.lower()


def test_translation_help_has_no_gettext_or_msgfmt_flag() -> None:
    result = CliRunner().invoke(cli, ["translations", "export", "--help"])

    assert result.exit_code == 0
    assert "gettext" not in result.output.lower()
    assert "msgfmt" not in result.output.lower()
