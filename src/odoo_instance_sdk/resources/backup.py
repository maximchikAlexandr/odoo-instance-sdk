from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    BackupNotAvailableError,
    BackupNotFoundError,
    BackupValidationUnavailableError,
)
from odoo_instance_sdk.internal.backup_validation import validate_dump, validate_zip
from odoo_instance_sdk.internal.urls import normalize_base_url
from odoo_instance_sdk.models import (
    Backup,
    BackupDeletionResult,
    BackupEvent,
    BackupFormat,
    BackupState,
    BackupValidationResult,
    BackupValidationStatus,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


@dataclass(slots=True, kw_only=True)
class BackupResource:
    _client: OdooClient

    def list(
        self,
        *,
        source_base_url: str | None = None,
        database_name: str | None = None,
        format: BackupFormat | None = None,
    ) -> tuple[Backup, ...]:
        catalog = self._client.get_catalog()
        if source_base_url is not None:
            source_base_url = normalize_base_url(source_base_url)
        backups = catalog.list_backups(
            source_base_url=source_base_url,
            database_name=database_name,
            format=format.value if format else None,
        )
        return tuple(backups)

    def latest(
        self,
        source_base_url: str,
        database_name: str,
        *,
        format: BackupFormat | None = None,
    ) -> Backup | None:
        catalog = self._client.get_catalog()
        return catalog.latest_backup(
            source_base_url=normalize_base_url(source_base_url),
            database_name=database_name,
            format=format.value if format else None,
        )

    def history(
        self,
        *,
        source_base_url: str | None = None,
        database_name: str | None = None,
        backup_id: str | None = None,
    ) -> tuple[BackupEvent, ...]:
        catalog = self._client.get_catalog()
        if source_base_url is not None:
            source_base_url = normalize_base_url(source_base_url)
        events = catalog.get_backup_history(
            source_base_url=source_base_url,
            database_name=database_name,
            backup_id=backup_id,
        )
        return tuple(events)

    def delete(self, backup: Backup) -> BackupDeletionResult:
        catalog = self._client.get_catalog()
        existing = catalog.get_by_id(str(backup.id))

        if existing is None:
            raise BackupNotFoundError(f"Backup {backup.id} not found in catalog")

        if existing["state"] == BackupState.DELETED.value:
            prior_deleted_at = (
                datetime.fromisoformat(existing["deleted_at"])
                if existing["deleted_at"]
                else datetime.now(UTC)
            )
            return BackupDeletionResult(
                file_existed=Path(backup.path).is_file(),
                already_deleted=True,
                deleted_at=prior_deleted_at,
            )

        catalog.verify_identity(backup)

        file_path = Path(backup.path)
        file_existed = file_path.is_file()
        with contextlib.suppress(OSError):
            file_path.unlink(missing_ok=True)

        catalog.record_deletion(str(backup.id))

        return BackupDeletionResult(
            file_existed=file_existed,
            already_deleted=False,
            deleted_at=datetime.now(UTC),
        )

    def validate(
        self,
        backup: Backup,
        *,
        raise_if_unavailable: bool = False,
        timeout: float = 60.0,
    ) -> BackupValidationResult:
        catalog = self._client.get_catalog()
        catalog.verify_identity(backup, verify_content=True)

        if not Path(backup.path).exists():
            raise BackupNotAvailableError(f"Backup file not found: {backup.path}")

        if backup.format == BackupFormat.DUMP:
            return self._validate_dump(
                backup, timeout=timeout, raise_if_unavailable=raise_if_unavailable
            )

        zip_result = validate_zip(Path(backup.path))
        return self._record_and_build(
            backup,
            BackupValidationStatus.VALID if zip_result.valid else BackupValidationStatus.INVALID,
            errors=zip_result.errors,
            db_name=zip_result.db_name,
            db_version=zip_result.db_version,
        )

    def _validate_dump(
        self,
        backup: Backup,
        *,
        timeout: float,
        raise_if_unavailable: bool,
    ) -> BackupValidationResult:
        try:
            dump_result = validate_dump(
                Path(backup.path),
                timeout=timeout,
                raise_if_unavailable=raise_if_unavailable,
            )
        except BackupValidationUnavailableError as e:
            self._record_and_build(
                backup,
                BackupValidationStatus.UNAVAILABLE,
                validator="pg_restore",
                errors=(str(e),),
            )
            raise

        if dump_result.unavailable:
            return self._record_and_build(
                backup,
                BackupValidationStatus.UNAVAILABLE,
                validator=None,
                exit_code=1,
                errors=dump_result.errors,
            )

        valid = dump_result.valid
        return self._record_and_build(
            backup,
            BackupValidationStatus.VALID if valid else BackupValidationStatus.INVALID,
            validator="pg_restore",
            exit_code=0 if valid else 1,
            errors=dump_result.errors,
        )

    def _record_and_build(
        self,
        backup: Backup,
        status: BackupValidationStatus,
        *,
        validator: str | None = None,
        exit_code: int | None = None,
        errors: tuple[str, ...] = (),
        db_name: str | None = None,
        db_version: str | None = None,
    ) -> BackupValidationResult:
        catalog = self._client.get_catalog()
        with contextlib.suppress(BackupCatalogError):
            catalog.record_validation(
                str(backup.id),
                status,
                validator=validator,
                exit_code=exit_code,
                message="; ".join(errors) or None,
            )
        valid = status == BackupValidationStatus.VALID
        return BackupValidationResult(
            valid=valid,
            errors=errors,
            db_name=db_name,
            db_version=db_version,
        )
