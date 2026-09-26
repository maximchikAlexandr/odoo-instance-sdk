from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError, MasterPasswordRequiredError
from odoo_instance_sdk.internal.dbprep.source import (
    _remote_password,
    remote_password_key,
    resolve_test_source,
)
from odoo_instance_sdk.internal.process_env import sanitized_child_environment
from odoo_instance_sdk.internal.project_env import effective_project_environment
from odoo_instance_sdk.models import DatabaseRefreshOptions
from odoo_instance_sdk.project import (
    ProjectConfig,
    RemoteSourceConfig,
)
from odoo_instance_sdk.project import (
    TestInstanceProjectConfig as LegacyConfig,
)


def _project(*sources: RemoteSourceConfig, legacy: bool = False) -> ProjectConfig:
    return ProjectConfig(
        repository_root=Path("/project"),
        test_instance=(
            LegacyConfig(
                base_url="https://legacy.example",
                database="legacy_db",
                git_branch="main",
            )
            if legacy
            else None
        ),
        remote_instances=sources,
    )


def test_named_source_resolution_is_explicit_and_keeps_provenance() -> None:
    source = RemoteSourceConfig(
        name="staging",
        base_url="https://staging.example",
        database="staging_db",
        git_branch="staging",
    )

    resolved = resolve_test_source(_project(source), DatabaseRefreshOptions(remote_name="STAGING"))

    assert resolved.source_name == "staging"
    assert resolved.config.base_url == "https://staging.example"
    assert resolved.config.database == "staging_db"
    assert resolved.branch == "staging"


def test_named_source_does_not_fall_back_to_legacy_or_first_profile() -> None:
    first = RemoteSourceConfig(
        name="lab", base_url="https://lab.example", database="lab_db", git_branch="main"
    )
    project = _project(first, legacy=True)

    with pytest.raises(ConfigError, match="unknown remote source 'missing'"):
        resolve_test_source(project, DatabaseRefreshOptions(remote_name="missing"))
    with pytest.raises(ConfigError, match=r"no \[test_instance\]"):
        resolve_test_source(_project(first))


def test_named_password_uses_process_precedence_and_rejects_empty_override() -> None:
    key = remote_password_key("staging")
    assert key == "ODCLI_REMOTE_STAGING_MASTER_PASSWORD"
    environment = effective_project_environment({key: "from-file"}, {key: "from-process"})
    assert _remote_password(environment, remote_name="staging") == "from-process"

    empty = effective_project_environment({key: "from-file"}, {key: ""})
    with pytest.raises(MasterPasswordRequiredError, match=key):
        _remote_password(empty, remote_name="staging")


def test_child_environment_removes_all_named_remote_passwords() -> None:
    environment = {
        "ODCLI_REMOTE_LAB_MASTER_PASSWORD": "lab-secret",
        "ODCLI_REMOTE_STAGING_MASTER_PASSWORD": "staging-secret",
        "ODCLI_TEST_MASTER_PASSWORD": "legacy-secret",
        "SAFE": "kept",
    }

    child = sanitized_child_environment(environment)

    assert child == {"SAFE": "kept"}
