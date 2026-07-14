from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Literal

import msgspec


class BackupFormat(enum.StrEnum):
    ZIP = "zip"
    DUMP = "dump"


class BackupState(enum.StrEnum):
    DOWNLOADING = "downloading"
    AVAILABLE = "available"
    FAILED = "failed"
    DELETED = "deleted"


class BackupEventType(enum.StrEnum):
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_SUCCEEDED = "download_succeeded"
    DOWNLOAD_FAILED = "download_failed"
    VALIDATION_SUCCEEDED = "validation_succeeded"
    VALIDATION_FAILED = "validation_failed"
    VALIDATION_UNAVAILABLE = "validation_unavailable"
    DELETED = "deleted"


class BackupValidationStatus(enum.StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class Backup(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    id: uuid.UUID
    source_base_url: str
    database_name: str
    format: BackupFormat
    filestore_requested: bool
    path: str
    filename: str
    size_bytes: int
    sha256: str
    downloaded_at: datetime


class BackupEvent(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    backup_id: uuid.UUID
    sequence: int
    event_type: BackupEventType
    occurred_at: datetime
    path: str | None = None
    validator: str | None = None
    exit_code: int | None = None
    message: str | None = None


class BackupValidationResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    valid: bool
    errors: tuple[str, ...] = ()
    db_name: str | None = None
    db_version: str | None = None


class BackupDeletionResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    file_existed: bool
    already_deleted: bool
    deleted_at: datetime


class StartConfig(msgspec.Struct, forbid_unknown_fields=True):
    http_port: int = 8069
    http_interface: str = "127.0.0.1"
    config_path: str | None = None
    addons_path: list[str] | None = None
    data_dir: str | None = None
    dbfilter: str | None = None
    workers: int | None = None
    max_cron_threads: int | None = None
    log_level: Literal["debug", "info", "warning", "error", "critical", "notset"] | None = None
    log_handler: str | None = None
    dev_mode: Literal["all"] | list[str] | None = None
    db_host: str | None = None
    db_port: int | None = None
    db_user: str | None = None
    db_password: str | None = None
    db_name: str | None = None
    load_language: str | None = None

    def __repr__(self) -> str:
        parts: list[str] = []
        for f in msgspec.structs.fields(self):
            val = getattr(self, f.name)
            if f.name == "db_password" and val is not None:
                parts.append(f"{f.name}=<redacted>")
            else:
                parts.append(f"{f.name}={val!r}")
        return f"StartConfig({', '.join(parts)})"


class CommandResult(msgspec.Struct):
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float


class OdooProcess(msgspec.Struct):
    id: str
    pid: int
    args: list[str]
    started_at: float

    def __repr__(self) -> str:
        masked: list[str] = []
        for i, a in enumerate(self.args):
            if i > 0 and self.args[i - 1] == "--config":
                masked.append("<redacted>")
            else:
                masked.append(a)
        return f"OdooProcess(id={self.id!r}, pid={self.pid!r}, args={masked!r}, started_at={self.started_at!r})"


class ProcessStatus(msgspec.Struct):
    state: Literal["running", "exited"]
    returncode: int | None = None


class ReadinessResult(msgspec.Struct):
    ok: bool
    elapsed: float
    attempts: int
    final_status: str | None = None


class RestoreResult(msgspec.Struct):
    new_db: str
    source: Backup


class DropResult(msgspec.Struct):
    db: str
