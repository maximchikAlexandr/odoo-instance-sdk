from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.project import ProjectConfig, effective_ticket_settings


def test_ticket_settings_parse_and_serialize(tmp_path: Path) -> None:
    config = ProjectConfig._from_mapping(
        {
            "ticket_link_enabled": True,
            "ticket_base_url": "HTTPS://tracker.example/",
        },
        repository_root=tmp_path,
    )

    assert config.ticket_link_enabled is True
    assert config.ticket_base_url == "https://tracker.example"
    assert "ticket_link_enabled = true" in config.to_manifest()
    assert 'ticket_base_url = "https://tracker.example"' in config.to_manifest()
    assert "jira_" not in config.to_manifest().lower()


def test_historical_ticket_settings_migrate_without_vendor_keys(tmp_path: Path) -> None:
    config = ProjectConfig._from_mapping(
        {"jira_enabled": True, "jira_base_url": "https://tracker.example/"},
        repository_root=tmp_path,
    )

    assert config.ticket_link_enabled is True
    assert config.ticket_base_url == "https://tracker.example"
    assert "jira_" not in config.to_manifest().lower()


def test_incomplete_historical_ticket_settings_are_actionable(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"incomplete.*ticket_base_url"):
        ProjectConfig._from_mapping({"jira_enabled": True}, repository_root=tmp_path)


def test_project_ticket_settings_override_global_values(tmp_path: Path) -> None:
    config = ProjectConfig(
        repository_root=tmp_path,
        ticket_link_enabled=False,
        ticket_base_url="https://project.example",
    )

    effective = effective_ticket_settings(
        config,
        global_enabled=True,
        global_base_url="https://global.example",
    )

    assert effective.enabled is False
    assert effective.base_url == "https://project.example"


def test_legacy_manifest_remains_byte_stable_without_ticket_settings(tmp_path: Path) -> None:
    config = ProjectConfig._from_mapping({}, repository_root=tmp_path)

    assert config.ticket_link_enabled is None
    assert "ticket_link_enabled" not in config.to_manifest()
    assert "ticket_base_url" not in config.to_manifest()
