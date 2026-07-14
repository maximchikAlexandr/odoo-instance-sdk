from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    BackupDownloadError,
    BackupNotAvailableError,
    DatabaseAlreadyExistsError,
    DatabaseError,
    DatabaseManagerUnavailableError,
    DropFailedError,
    MasterPasswordRequiredError,
    RestoreFailedError,
)
from odoo_instance_sdk.internal.files import (
    ensure_destination,
    extract_server_filename,
    make_download_filename,
)
from odoo_instance_sdk.internal.paths import get_backups_dir
from odoo_instance_sdk.internal.redact import format_error
from odoo_instance_sdk.internal.urls import assert_local, warn_if_cleartext_secret
from odoo_instance_sdk.models import Backup, BackupFormat, DropResult, RestoreResult

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance

_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB


def _stream_response_to_file(
    resp: httpx.Response,
    dest: Path,
    *,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
) -> tuple[int, str]:
    sha = hashlib.sha256()
    written = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_bytes(chunk_size=8192):
            written += len(chunk)
            if written > max_bytes:
                raise BackupDownloadError(f"Download exceeded {max_bytes} bytes")
            f.write(chunk)
            sha.update(chunk)
    return written, sha.hexdigest()


@dataclass(slots=True, kw_only=True)
class DatabaseResource:
    base_url: str
    master_password: str | None = field(repr=False, default=None)
    _instance: OdooInstance = field(repr=False, hash=False, compare=False)

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/web/database/{path}"

    def _require_password(self) -> str:
        if self.master_password is None:
            raise MasterPasswordRequiredError(
                f"Operation requires master password for {self.base_url}"
            )
        return self.master_password

    def _assert_local(self) -> None:
        assert_local(self.base_url)

    @contextlib.contextmanager
    def _http(self, timeout: float | None = None) -> Iterator[httpx.Client]:
        warn_if_cleartext_secret(self.base_url)
        effective = (
            timeout if timeout is not None else self._instance._client.config.http_timeout_seconds
        )
        with httpx.Client(
            timeout=httpx.Timeout(effective),
        ) as http:
            yield http

    def list(self) -> tuple[str, ...]:
        try:
            with self._http() as http:
                resp = http.post(
                    self._url("list"),
                    data={},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise DatabaseError(
                status_code=exc.response.status_code,
                message=format_error(exc.response.text),
                body=exc.response.content,
            ) from exc
        except httpx.HTTPError as exc:
            raise DatabaseManagerUnavailableError(
                f"Database manager unavailable on {self.base_url}: {format_error(exc)}"
            ) from exc
        if not isinstance(data, dict):
            raise DatabaseManagerUnavailableError(
                f"Unexpected response from {self.base_url}: not a JSON object"
            )
        result = data.get("result", [])
        if not isinstance(result, list):
            raise DatabaseManagerUnavailableError(
                f"Database listing disabled or unavailable on {self.base_url}"
            )
        return tuple(str(name) for name in result)

    def exists(self, name: str) -> bool:
        return name in self.list()

    def backup(
        self,
        database_name: str,
        *,
        format: BackupFormat = BackupFormat.ZIP,
        filestore: bool = True,
        destination: str | Path | None = None,
        timeout: float | None = None,
    ) -> Backup:
        pwd = self._require_password()
        # ponytail: remote backup sends master_pwd by spec design; warn_if_cleartext_secret covers HTTP

        if destination is None:
            destination = self._instance._client.config.backups_directory
        else:
            destination = Path(destination)
        if destination is None:
            destination = get_backups_dir()
        destination.mkdir(parents=True, exist_ok=True)

        backup_id = str(uuid.uuid4())
        part_path = ensure_destination(destination, f"{backup_id}.{format.value}.part")
        catalog = self._instance._client.get_catalog()
        catalog.start_download(
            backup_id=backup_id,
            source_base_url=self.base_url,
            database_name=database_name,
            format=format.value,
            filestore_requested=filestore,
            path=part_path,
        )

        try:
            with self._http(timeout=timeout) as http:
                resp = http.post(
                    self._url("backup"),
                    data={
                        "master_pwd": pwd,
                        "name": database_name,
                        "backup_format": format.value,
                        "filestore": "true" if filestore else "false",
                    },
                )
                resp.raise_for_status()
                server_filename = extract_server_filename(resp.headers.get("content-disposition"))
                size_bytes, sha256_hex = _stream_response_to_file(resp, part_path)

            actual_filename = make_download_filename(backup_id, server_filename)
            final_path = ensure_destination(destination, actual_filename)
            if final_path != part_path:
                part_path.rename(final_path)

            if not final_path.resolve().is_relative_to(destination.resolve()):
                with contextlib.suppress(OSError):
                    final_path.unlink()
                raise BackupDownloadError("Path traversal detected after rename")  # noqa: TRY301

            catalog.update_path(backup_id, final_path)
            downloaded_at = datetime.now(UTC)
            catalog.success_download(
                backup_id, final_path.name, size_bytes, sha256_hex, downloaded_at=downloaded_at
            )

            return Backup(
                id=uuid.UUID(backup_id),
                source_base_url=self.base_url,
                database_name=database_name,
                format=format,
                filestore_requested=filestore,
                path=str(final_path),
                filename=final_path.name,
                size_bytes=size_bytes,
                sha256=sha256_hex,
                downloaded_at=downloaded_at,
            )
        except (httpx.HTTPError, OSError, BackupCatalogError, BackupDownloadError) as e:
            with contextlib.suppress(BackupCatalogError):
                catalog.fail_download(backup_id, type(e).__name__, format_error(e))
            if part_path.exists():
                with contextlib.suppress(OSError):
                    part_path.unlink()
            raise

    def restore(
        self,
        backup: Backup,
        target_database_name: str,
        *,
        copy: bool = False,
        neutralize_database: bool = False,
        timeout: float | None = None,
    ) -> RestoreResult:
        self._assert_local()
        pwd = self._require_password()

        catalog = self._instance._client.get_catalog()
        catalog.verify_identity(backup)

        backup_path = Path(backup.path)
        if not backup_path.is_file() or not os.access(backup_path, os.R_OK):
            raise BackupNotAvailableError(f"Backup file not found or unreadable: {backup.path}")

        if self.exists(target_database_name):
            raise DatabaseAlreadyExistsError(
                f"Database {target_database_name!r} already exists on {self.base_url}"
            )

        with open(backup_path, "rb") as fp, self._http(timeout=timeout) as http:
            resp = http.post(
                self._url("restore"),
                data={
                    "master_pwd": pwd,
                    "copy": "true" if copy else "false",
                    "neutralize_database": "true" if neutralize_database else "false",
                },
                files={
                    "backup_file": (backup.filename, fp, "application/octet-stream"),
                },
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise DatabaseError(
                    status_code=exc.response.status_code,
                    message=format_error(exc.response.text),
                    body=exc.response.content,
                ) from exc

        if not self.exists(target_database_name):
            raise RestoreFailedError(
                f"Database {target_database_name!r} was not created after restore"
            )

        return RestoreResult(new_db=target_database_name, source=backup)

    def drop(
        self,
        database_name: str,
        *,
        timeout: float | None = None,
    ) -> DropResult:
        pwd = self._require_password()
        self._assert_local()

        with self._http(timeout=timeout) as http:
            resp = http.post(
                self._url("drop"),
                data={
                    "master_pwd": pwd,
                    "name": database_name,
                },
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise DatabaseError(
                    status_code=exc.response.status_code,
                    message=format_error(exc.response.text),
                    body=exc.response.content,
                ) from exc

        if self.exists(database_name):
            raise DropFailedError(f"Database {database_name!r} still exists after drop")

        return DropResult(db=database_name)
