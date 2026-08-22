from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from odoo_instance_sdk.internal.observe import backup_exists
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentState,
)


def _env(*, backup_id: uuid.UUID | None) -> DevelopmentEnvironment:
    return DevelopmentEnvironment(
        id=uuid.uuid4(),
        name="env",
        repository_root="/repo",
        git_common_dir="/repo/.git",
        branch="main",
        base_ref="HEAD",
        worktree_path="/wt",
        generated_config_path="/wt/odoo.conf",
        python_environment_path="/venv",
        python_environment_owned=False,
        dependency_lock_path="/lock",
        http_interface="127.0.0.1",
        http_port=8069,
        db_mode=EnvironmentDatabaseMode.SHARED,
        state=EnvironmentState.READY,
        created_at=datetime.now(UTC),
        backup_id=backup_id,
    )


def test_backup_exists_is_none_when_unset() -> None:
    client = MagicMock()
    assert backup_exists(client, _env(backup_id=None)) is None
    client.backups.list.assert_not_called()


def test_backup_exists_uses_backup_resource_list() -> None:
    backup_id = uuid.uuid4()
    client = MagicMock()
    client.backups.list.return_value = [SimpleNamespace(id=backup_id)]
    client.get_catalog.side_effect = AssertionError("CLI must not open the catalog")

    assert backup_exists(client, _env(backup_id=backup_id)) is True
    client.backups.list.assert_called_once_with()
    client.get_catalog.assert_not_called()


def test_backup_exists_is_false_when_missing() -> None:
    client = MagicMock()
    client.backups.list.return_value = [SimpleNamespace(id=uuid.uuid4())]

    assert backup_exists(client, _env(backup_id=uuid.uuid4())) is False
