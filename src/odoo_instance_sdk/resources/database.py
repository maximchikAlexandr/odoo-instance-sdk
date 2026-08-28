from __future__ import annotations

import contextlib
import hashlib
import os
import unicodedata
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
    ConfigError,
    DatabaseAlreadyExistsError,
    DatabaseError,
    DatabaseManagerUnavailableError,
    DropFailedError,
    InstanceConfigurationError,
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
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    Backup,
    BackupFormat,
    Database,
    DropResult,
    NoBackup,
    RestoreResult,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance

_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB
_RESET_ADMIN_PASSWORD_SCRIPT = """
user = env.ref('base.user_admin', raise_if_not_found=True)
user.ensure_one()
user.write({'password': 'admin'})
result = {'xml_id': 'base.user_admin', 'updated': True}
"""


def _normalize_source_git_branch(value: str | None) -> str | None:
    """Validate declarative branch metadata before any backup side effect."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError("source_git_branch must be text")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ConfigError(
            "source_git_branch must be non-empty after trimming and contain no control characters"
        )
    branch = value.strip()
    if not branch:
        raise ConfigError(
            "source_git_branch must be non-empty after trimming and contain no control characters"
        )
    return branch


def _stream_response_to_file(
    resp: httpx.Response,
    dest: Path,
    *,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
) -> tuple[int, str]:
    sha = hashlib.sha256()
    written = 0
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        for chunk in resp.iter_bytes(chunk_size=8192):
            written += len(chunk)
            if written > max_bytes:
                raise BackupDownloadError(f"Download exceeded {max_bytes} bytes")
            f.write(chunk)
            sha.update(chunk)
    return written, sha.hexdigest()


def _verify_database_via_psql(
    db_host: str | None,
    db_port: int,
    db_user: str | None,
    db_password: str | None,
    database_name: str,
) -> bool | None:
    """Probe whether a PostgreSQL database exists via the ``psql`` CLI.

    Return values:
      * ``True``  — psql ran successfully and stdout indicates the database
                    exists (e.g. a row from ``pg_database``).
      * ``False`` — psql ran successfully and stdout is empty: the database
                    is confirmed absent. Callers SHOULD record the drop.
      * ``None``  — inconclusive: psql not in PATH, returned non-zero, or
                    timed out. Callers MUST NOT treat this as a drop.
    """
    if "\\" in database_name:
        return None
    from odoo_instance_sdk.internal.postgres_transport import run_psql

    escaped = database_name.replace("'", "''")
    proc = run_psql(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        query=f"SELECT 1 FROM pg_database WHERE datname='{escaped}'",
        timeout=30,
    )
    if proc is None or proc.returncode != 0:
        return None
    return bool(proc.stdout.strip())


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

    @property
    def _cluster(self) -> tuple[str | None, int] | None:
        if self._instance.config.db_host is None:
            return None
        return (self._instance.config.db_host, self._instance.config.db_port or 5432)

    def _latest_backup_for(self, db_host: str | None, db_port: int, name: str) -> Backup | NoBackup:
        b = self._instance._client.get_catalog().latest_restore(db_host, db_port, name)
        return b if b is not None else NoBackup()

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

    def names(self) -> tuple[str, ...]:
        """Return database names without touching the local audit catalog."""
        try:
            with self._http() as http:
                resp = http.post(
                    self._url("list"),
                    json={"jsonrpc": "2.0", "method": "call", "params": {}},
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

    def list(self) -> tuple[Database, ...]:
        db_names = self.names()

        ck = self._cluster
        catalog = self._instance._client.get_catalog()

        databases = []
        for name in db_names:
            if ck is not None:
                db_host, db_port = ck
                backup = self._latest_backup_for(db_host, db_port, name)
            else:
                backup = NoBackup()
            databases.append(Database(name=name, backup=backup))

        if ck is not None:
            db_host, db_port = ck
            restored_names = catalog.distinct_restored_database_names(db_host, db_port)
            current_set = set(db_names)
            for rname in restored_names:
                if rname not in current_set:
                    catalog.record_database_dropped(db_host, db_port, rname)

        return tuple(databases)

    def exists(self, name: str) -> bool:
        try:
            databases = self.list()
        except DatabaseManagerUnavailableError:
            ck = self._cluster
            if ck is not None and self._instance.config.db_user is not None:
                db_host, db_port = ck
                result = _verify_database_via_psql(
                    db_host,
                    db_port,
                    self._instance.config.db_user,
                    self._instance.config.db_password,
                    name,
                )
                if result is True:
                    return True
                if result is False:
                    catalog = self._instance._client.get_catalog()
                    catalog.record_database_dropped(db_host, db_port, name)
                    return False
            raise

        ck = self._cluster
        found = any(db.name == name for db in databases)
        if not found and ck is not None:
            db_host, db_port = ck
            catalog = self._instance._client.get_catalog()
            if catalog.has_tracked_database(db_host, db_port, name):
                catalog.record_database_dropped(db_host, db_port, name)
        return found

    def __getitem__(self, index: int) -> Database:
        if not isinstance(index, int):
            raise TypeError(
                f"DatabaseResource indices must be integers, not {type(index).__name__}"
            )
        return self.list()[index]

    def current(self) -> Database:
        configured = self._instance.config.configured_database_names
        if not configured:
            return Database(name="", backup=NoBackup())

        name = configured[0]

        try:
            databases = self.list()
        except DatabaseManagerUnavailableError:
            ck = self._cluster
            if ck is not None and self._instance.config.db_user is not None:
                db_host, db_port = ck
                exists_result = _verify_database_via_psql(
                    db_host,
                    db_port,
                    self._instance.config.db_user,
                    self._instance.config.db_password,
                    name,
                )
                catalog = self._instance._client.get_catalog()
                if exists_result is True:
                    backup = self._latest_backup_for(db_host, db_port, name)
                    return Database(name=name, backup=backup)
                if exists_result is False:
                    catalog.record_database_dropped(db_host, db_port, name)
                    return Database(name=name, backup=NoBackup())
                return Database(name=name, backup=NoBackup())
            raise

        ck = self._cluster
        catalog = self._instance._client.get_catalog()

        found = any(db.name == name for db in databases)

        if not found:
            if ck is not None:
                db_host, db_port = ck
                catalog.record_database_dropped(db_host, db_port, name)
            return Database(name=name, backup=NoBackup())

        if ck is not None:
            db_host, db_port = ck
            backup = self._latest_backup_for(db_host, db_port, name)
        else:
            backup = NoBackup()

        return Database(name=name, backup=backup)

    def backup(
        self,
        database_name: str,
        *,
        format: BackupFormat = BackupFormat.ZIP,
        filestore: bool = True,
        destination: str | Path | None = None,
        timeout: float | None = None,
        source_git_branch: str | None = None,
    ) -> Backup:
        source_git_branch = _normalize_source_git_branch(source_git_branch)
        pwd = self._require_password()

        if destination is None:
            destination = self._instance._client.config.backups_directory
        else:
            destination = Path(destination)
        if destination is None:
            destination = get_backups_dir()
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(destination, 0o700)

        backup_id = str(uuid.uuid4())
        part_path = ensure_destination(destination, f"{backup_id}.{format.value}.part")
        part_preexisted = part_path.exists()
        catalog = self._instance._client.get_catalog()
        catalog.start_download(
            backup_id=backup_id,
            source_base_url=self.base_url,
            database_name=database_name,
            format=format.value,
            filestore_requested=filestore,
            path=part_path,
            source_git_branch=source_git_branch,
        )

        try:
            server_filename, size_bytes, sha256_hex = self._download_backup_part(
                database_name, pwd, part_path, timeout=timeout, format=format, filestore=filestore
            )

            actual_filename = make_download_filename(backup_id, server_filename)
            final_path = ensure_destination(destination, actual_filename)
            if final_path != part_path:
                if final_path.exists():
                    raise BackupDownloadError("Backup destination already exists")  # noqa: TRY301
                part_path.rename(final_path)
            os.chmod(final_path, 0o600)

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
                source_git_branch=source_git_branch,
            )
        except (OSError, BackupCatalogError, BackupDownloadError) as e:
            with contextlib.suppress(BackupCatalogError):
                catalog.fail_download(backup_id, type(e).__name__, format_error(e))
            if not part_preexisted and part_path.exists():
                with contextlib.suppress(OSError):
                    part_path.unlink()
            raise

    def _download_backup_part(
        self,
        database_name: str,
        password: str,
        part_path: Path,
        *,
        timeout: float | None,
        format: BackupFormat,
        filestore: bool,
    ) -> tuple[str | None, int, str]:
        """Fetch a backup, converting HTTPX failures without retaining them."""
        http_failure: str | None = None
        try:
            with self._http(timeout=timeout) as http:
                resp = http.post(
                    self._url("backup"),
                    data={
                        "master_pwd": password,
                        "name": database_name,
                        "backup_format": format.value,
                        "filestore": "true" if filestore else "false",
                    },
                )
                resp.raise_for_status()
                server_filename = extract_server_filename(resp.headers.get("content-disposition"))
                size_bytes, sha256_hex = _stream_response_to_file(resp, part_path)
        except httpx.HTTPStatusError as exc:
            # Keep only a status-derived value. The HTTPX exception retains
            # its request/response/stream graph, including master_pwd.
            http_failure = f"Backup request failed with HTTP status {exc.response.status_code}"
        except httpx.HTTPError:
            # Do not format the exception: its request may contain the remote
            # master password and response bodies can be unbounded.
            http_failure = "Backup request failed"

        if http_failure is not None:
            raise BackupDownloadError(http_failure) from None
        return server_filename, size_bytes, sha256_hex

    def reset_admin_password(self) -> AdminPasswordResetResult:
        """Reset ``base.user_admin`` on this instance's one bound database."""
        self._assert_local()
        configured = self._instance.config.configured_database_names
        if len(configured) != 1 or not configured[0].strip():
            raise InstanceConfigurationError(
                "Administrator password reset requires exactly one configured database"
            )

        database = configured[0]
        try:
            command = self._instance._run_shell_script_exclusive(
                _RESET_ADMIN_PASSWORD_SCRIPT,
                commit=True,
            )
        except Exception:
            # Shell diagnostics may contain application data. Never expose them through
            # this resource's error surface.
            raise DatabaseManagerUnavailableError("Administrator password reset failed") from None
        if command.returncode != 0:
            raise DatabaseManagerUnavailableError("Administrator password reset failed")

        environment_id: uuid.UUID | None = None
        if self._instance._environment_id is not None:
            with contextlib.suppress(ValueError):
                environment_id = uuid.UUID(self._instance._environment_id)
        return AdminPasswordResetResult(
            database=database,
            completed=True,
            xml_id="base.user_admin",
            environment_id=environment_id,
        )

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

        http_failure: tuple[int, str] | tuple[None, str] | None = None
        try:
            with open(backup_path, "rb") as fp, self._http(timeout=timeout) as http:
                resp = http.post(
                    self._url("restore"),
                    data={
                        "master_pwd": pwd,
                        "name": target_database_name,
                        "copy": "true" if copy else "false",
                        "neutralize_database": "true" if neutralize_database else "false",
                    },
                    files={
                        "backup_file": (backup.filename, fp, "application/octet-stream"),
                    },
                )
                if resp.is_error:
                    resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Convert outside the except scope so the SDK error has no HTTPX
            # cause/context/request/response/stream references.
            http_failure = (
                exc.response.status_code,
                f"Database restore failed with HTTP status {exc.response.status_code}",
            )
        except httpx.HTTPError:
            http_failure = (None, "Database restore request failed")

        if http_failure is not None:
            status_code, message = http_failure
            raise DatabaseError(status_code=status_code or 0, message=message, body=b"") from None

        if not self.exists(target_database_name):
            raise RestoreFailedError(
                f"Database {target_database_name!r} was not created after restore"
            )

        ck = self._cluster
        if ck is not None:
            db_host, db_port = ck
            catalog.record_restore(
                db_host,
                db_port,
                target_database_name,
                str(backup.id),
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
                if resp.is_error:
                    resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise DatabaseError(
                    status_code=exc.response.status_code,
                    message=format_error(exc.response.text),
                    body=exc.response.content,
                ) from exc

        if self.exists(database_name):
            raise DropFailedError(f"Database {database_name!r} still exists after drop")

        ck = self._cluster
        if ck is not None:
            db_host, db_port = ck
            catalog = self._instance._client.get_catalog()
            catalog.record_database_dropped(
                db_host,
                db_port,
                database_name,
            )

        return DropResult(db=database_name)
