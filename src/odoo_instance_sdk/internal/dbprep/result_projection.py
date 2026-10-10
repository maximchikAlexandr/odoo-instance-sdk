"""Binding-aware projections returned by database preparation orchestration."""

from __future__ import annotations

from pathlib import Path

from odoo_instance_sdk.internal.dbprep.source import (
    TestSourceResolution,
    _LocalArchiveRestoreSource,
    _RestoreSource,
)
from odoo_instance_sdk.models import (
    Backup,
    BackupBranchOrigin,
    DatabasePreparationAction,
    DatabasePreparationResult,
)
from odoo_instance_sdk.project import ProjectConfig, managed_filestore_path


def coalesced_restore_result(
    backup: Backup,
    source: TestSourceResolution,
    project: ProjectConfig,
    *,
    project_id: str,
) -> DatabasePreparationResult:
    """Project the result when a fresh database can be reused."""
    return DatabasePreparationResult(
        mode=DatabasePreparationAction.RESTORE,
        backup=backup,
        source_kind="catalogue",
        backup_id=backup.id,
        source_git_branch=backup.source_git_branch,
        branch_origin=source.origin,
        restored_database=project.default_source_database,
        previous_default=project.default_source_database,
        effective_default=project.default_source_database,
        managed_filestore=(
            str(managed_filestore_path(project)) if project.managed_filestore is not None else None
        ),
        project_id=project_id,
        binding_published=False,
        warnings=("reused fresh project database",),
    )


def completed_restore_result(
    backup: Backup | None,
    restore_source: _RestoreSource,
    source: TestSourceResolution | None,
    *,
    target_database: str,
    source_config: Path,
    managed_filestore: Path,
    project_id: str,
    admin_password_reset: bool,
    previous_default: str | None,
) -> DatabasePreparationResult:
    """Project a completed restore with its published runtime binding."""
    return DatabasePreparationResult(
        mode=DatabasePreparationAction.RESTORE,
        backup=backup,
        source_kind=(
            "local_archive"
            if isinstance(restore_source, _LocalArchiveRestoreSource)
            else "catalogue"
        ),
        backup_id=backup.id if backup is not None else None,
        source_git_branch=backup.source_git_branch if backup is not None else None,
        branch_origin=source.origin if source is not None else BackupBranchOrigin.UNKNOWN,
        restored_database=target_database,
        target_database=target_database,
        effective_config=str(source_config),
        managed_filestore=str(managed_filestore),
        project_id=project_id,
        binding_published=True,
        admin_password_reset=admin_password_reset,
        default_switched=True,
        previous_default=previous_default,
        effective_default=target_database,
    )


def download_result(
    backup: Backup,
    source: TestSourceResolution,
    project: ProjectConfig,
    *,
    project_id: str,
) -> DatabasePreparationResult:
    """Project the download-only result with the current project binding."""
    return DatabasePreparationResult(
        mode=DatabasePreparationAction.DOWNLOAD,
        backup=backup,
        source_kind="catalogue",
        backup_id=backup.id,
        source_git_branch=source.branch,
        branch_origin=source.origin,
        previous_default=project.default_source_database,
        effective_default=project.default_source_database,
        managed_filestore=(
            str(managed_filestore_path(project)) if project.managed_filestore is not None else None
        ),
        project_id=project_id,
    )


__all__ = ["coalesced_restore_result", "completed_restore_result", "download_result"]
