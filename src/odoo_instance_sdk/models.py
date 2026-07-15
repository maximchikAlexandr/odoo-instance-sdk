from __future__ import annotations

import enum
import uuid
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

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
    """A successfully downloaded backup.

    Convention: ``downloaded_at`` is timezone-aware (UTC). ``NoBackup`` also
    uses a tz-aware default. Downstream code that reads
    ``db.backup.downloaded_at`` on a ``Backup | NoBackup`` union can rely on
    the value being tz-aware.
    """

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


class NoBackup(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    id: uuid.UUID = uuid.UUID(int=0)
    source_base_url: str = ""
    database_name: str = ""
    format: BackupFormat | None = None
    filestore_requested: bool = False
    path: str = ""
    filename: str = ""
    size_bytes: int = 0
    sha256: str = ""
    downloaded_at: datetime = datetime.fromtimestamp(0, UTC)


class Database(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    name: str
    backup: Backup | NoBackup


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

    @classmethod
    def from_odoo_config(cls, path: str | Path) -> StartConfig:
        """Build a StartConfig by reading fields from an odoo.conf file.

        Literal fields (``log_level``, ``dev_mode``) are validated by msgspec
        at construction; an invalid value raises ``msgspec.ValidationError``.
        """
        # local import: odoo_config -> urls -> exceptions; none import models,
        # but keeping it lazy avoids any import-order surprise at package init.
        from odoo_instance_sdk.internal.odoo_config import parse_odoo_config

        cfg = parse_odoo_config(path)

        def _get(name: str) -> str | None:
            v = cfg.get(name)
            return v if v else None

        def _int(name: str) -> int | None:
            v = _get(name)
            if v is None:
                return None
            try:
                return int(v)
            except ValueError:
                warnings.warn(
                    f"Invalid int for {name} in odoo.conf: {v!r}; using default",
                    stacklevel=3,
                )
                return None

        def _list(name: str) -> list[str] | None:
            v = _get(name)
            if v is None:
                return None
            return [s.strip() for s in v.split(",") if s.strip()]

        def _dev_mode() -> Literal["all"] | list[str] | None:
            v = _get("dev_mode")
            if v is None:
                return None
            if "," in v:
                return [s.strip() for s in v.split(",") if s.strip()]
            return cast("Literal['all']", v)

        return cls(
            http_port=_int("http_port") or 8069,
            http_interface=_get("http_interface") or "127.0.0.1",
            config_path=_get("config_path"),
            addons_path=_list("addons_path"),
            data_dir=_get("data_dir"),
            dbfilter=_get("dbfilter"),
            workers=_int("workers"),
            max_cron_threads=_int("max_cron_threads"),
            log_level=cast(
                "Literal['debug', 'info', 'warning', 'error', 'critical', 'notset'] | None",
                _get("log_level"),
            ),
            log_handler=_get("log_handler"),
            dev_mode=_dev_mode(),
            db_host=_get("db_host"),
            db_port=_int("db_port"),
            db_user=_get("db_user"),
            db_password=_get("db_password"),
            db_name=_get("db_name"),
            load_language=_get("load_language"),
        )

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
