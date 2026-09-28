"""Public catalogue projection helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from odoo_instance_sdk.models import (
    BackupEnvironmentLink,
    BackupInspectResult,
    BackupRestoreLink,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupProjection


def backup_projection_to_inspect_result(projection: BackupProjection) -> BackupInspectResult:
    """Project one state-aware catalogue row into the public inspect result."""
    backup = projection.backup
    return BackupInspectResult(
        id=backup.id,
        source_base_url=backup.source_base_url,
        database_name=backup.database_name,
        format=backup.format,
        filestore_requested=backup.filestore_requested,
        path=backup.path,
        filename=backup.filename,
        size_bytes=backup.size_bytes,
        sha256=backup.sha256,
        downloaded_at=backup.downloaded_at,
        source_git_branch=backup.source_git_branch,
        source_name=backup.source_name,
        pinned=backup.pinned,
        state=projection.state,
        catalogue_time=projection.catalogue_time,
        file_present=projection.file_present,
        recorded_bytes=projection.recorded_bytes,
        occupied_bytes=projection.occupied_bytes,
        history=projection.history,
        restore_links=tuple(
            BackupRestoreLink(
                db_host=link.db_host,
                db_port=link.db_port,
                database_name=link.database_name,
                restored_at=link.restored_at,
            )
            for link in projection.restore_links
        ),
        environment_links=tuple(
            BackupEnvironmentLink(
                environment_id=link.environment_id,
                name=link.name,
                state=link.state,
                target_database=link.target_database,
            )
            for link in projection.environment_links
        ),
    )


__all__ = ["backup_projection_to_inspect_result"]
