"""Genuine database-manager backup and archive validation helpers."""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


class ArchiveValidationError(ValueError):
    """Raised when a database-manager response is not a usable ZIP backup."""


@dataclass(frozen=True, slots=True)
class ArchiveIdentity:
    path: Path
    size_bytes: int
    sha256: str
    dump_member: str
    filestore_members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceBackupPlan:
    """A fresh-per-run backup destination and source identity."""

    endpoint: str
    database: str
    destination: Path

    def fetch(self, master_password: str, *, timeout: float = 300.0) -> ArchiveIdentity:
        return download_database_manager_backup(
            self.endpoint,
            master_password=master_password,
            database=self.database,
            destination=self.destination,
            timeout=timeout,
        )


def odoo_initialization_command(
    odoo_executable: Path,
    config: Path,
    database: str,
    *,
    addon: str,
) -> tuple[str, ...]:
    """Build the one-shot source/sentinel initialization command."""
    if not database or not addon:
        raise ValueError("database and addon are required")
    return (
        str(odoo_executable),
        "--config",
        str(config),
        "--database",
        database,
        "--init",
        addon,
        "--without-demo=all",
        "--stop-after-init",
    )


def archive_identity(path: Path) -> ArchiveIdentity:
    """Validate dump + filestore members and return the run-specific identity."""
    if not path.is_file():
        raise ArchiveValidationError(f"backup is not a regular file: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            files = {info.filename for info in members if not info.is_dir()}
            dump = next(
                (name for name in files if name == "dump.sql" or name.endswith("/dump.sql")), None
            )
            filestore = tuple(
                sorted(
                    name
                    for name in files
                    if name.startswith("filestore/") and not name.endswith("/")
                )
            )
            if dump is None:
                raise ArchiveValidationError("backup ZIP does not contain dump.sql")
            if not filestore:
                raise ArchiveValidationError("backup ZIP does not contain a filestore file")
    except (OSError, zipfile.BadZipFile) as error:
        raise ArchiveValidationError(f"invalid backup ZIP: {path}") from error
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return ArchiveIdentity(path, path.stat().st_size, digest.hexdigest(), dump, filestore)


def fresh_database_name(run_id: str, role: str) -> str:
    """Produce a PostgreSQL-safe database name that cannot cross run scopes."""
    normalized = "".join(char for char in run_id.lower() if char.isalnum())
    safe_role = "".join(char if char.isalnum() else "_" for char in role.lower()).strip("_")
    if not normalized or not safe_role:
        raise ValueError("run_id and role are required")
    return f"odcli_e2e_{safe_role}_{normalized}"[:63]


def source_database_name(run_id: str) -> str:
    return fresh_database_name(run_id, "source")


def target_sentinel_database_name(run_id: str) -> str:
    return fresh_database_name(run_id, "sentinel")


def download_database_manager_backup(
    endpoint: str,
    *,
    master_password: str,
    database: str,
    destination: Path,
    timeout: float = 300.0,
) -> ArchiveIdentity:
    """POST to Odoo's supported backup endpoint, then validate atomically."""
    if not master_password:
        raise ValueError("master_password must not be empty")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = urllib.parse.urlencode(
        {"master_pwd": master_password, "name": database, "backup_format": "zip"}
    ).encode()
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/web/database/backup",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ArchiveValidationError(f"backup endpoint returned HTTP {response.status}")
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(response.read())
            temporary.chmod(0o600)
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    return archive_identity(destination)
