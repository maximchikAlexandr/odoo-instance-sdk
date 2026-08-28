from __future__ import annotations

import uuid
from datetime import UTC, datetime

import msgspec
import pytest

from odoo_instance_sdk import (
    AdminPasswordResetResult,
    Backup,
    BackupBranchOrigin,
    BackupFormat,
    BackupFreshness,
    BackupProvenanceComparison,
    BackupProvenanceStatus,
    DatabasePreparationAction,
    DatabasePreparationResult,
    DatabaseRefreshOptions,
    EnvironmentCheckoutPlan,
    EnvironmentDatabaseMode,
    EnvironmentPythonMode,
    NoBackup,
)


def test_backup_provenance_models_are_frozen_and_serializable() -> None:
    backup = Backup(
        uuid.uuid4(),
        "https://example.test",
        "remote",
        BackupFormat.ZIP,
        True,
        "/var/lib/backup.zip",
        "backup.zip",
        10,
        "a" * 64,
        datetime(2026, 1, 1, tzinfo=UTC),
        "release/19",
    )
    result = DatabasePreparationResult(
        mode=DatabasePreparationAction.DOWNLOAD,
        backup=backup,
        source_git_branch="release/19",
        branch_origin=BackupBranchOrigin.EXPLICIT,
        warnings=("warning",),
    )
    decoded = msgspec.json.decode(msgspec.json.encode(result), type=DatabasePreparationResult)

    assert decoded == result
    with pytest.raises(AttributeError):
        result.mode = DatabasePreparationAction.RESTORE  # type: ignore[misc]


def test_checkout_plan_has_only_secret_free_public_fields() -> None:
    plan = EnvironmentCheckoutPlan(
        name="repo:feature",
        branch="feature",
        effective_base_ref="release/19",
        db_mode=EnvironmentDatabaseMode.SHARED,
        source_database=None,
        target_database=None,
        python_mode=EnvironmentPythonMode.REUSE,
        provenance=BackupProvenanceComparison(
            status=BackupProvenanceStatus.UNKNOWN,
            expected_base_ref="release/19",
            recorded_branch=None,
        ),
        freshness=BackupFreshness.MISSING,
        preparation_actions=(DatabasePreparationAction.DOWNLOAD,),
        warnings=(),
    )
    field_names = {field.name for field in msgspec.structs.fields(plan)}

    assert field_names == {
        "name",
        "branch",
        "effective_base_ref",
        "db_mode",
        "source_database",
        "target_database",
        "python_mode",
        "provenance",
        "freshness",
        "preparation_actions",
        "warnings",
    }
    assert not field_names & {"config", "config_path", "path", "argv", "password", "env_id"}


def test_no_backup_has_nullable_branch_and_legacy_json_decodes() -> None:
    assert NoBackup().source_git_branch is None
    decoded = msgspec.json.decode(
        b'{"id":"00000000-0000-0000-0000-000000000000","source_base_url":"",'
        b'"database_name":"","format":null,"filestore_requested":false,"path":"",'
        b'"filename":"","size_bytes":0,"sha256":"",'
        b'"downloaded_at":"1970-01-01T00:00:00Z"}',
        type=NoBackup,
    )
    assert decoded.source_git_branch is None


def test_reset_result_does_not_require_environment_id() -> None:
    result = AdminPasswordResetResult(
        database="target_db", completed=True, xml_id="base.user_admin"
    )
    assert result.environment_id is None
    assert "password" not in {field.name for field in msgspec.structs.fields(result)}


def test_refresh_options_are_frozen_and_secret_free() -> None:
    options = DatabaseRefreshOptions(
        restore=True, source_branch="develop", reset_admin_password=True
    )
    assert options.restore is True
    assert "password" not in {field.name for field in msgspec.structs.fields(options)}
