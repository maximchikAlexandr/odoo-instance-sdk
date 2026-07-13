from __future__ import annotations

import types
import typing
from pathlib import Path
from typing import Any, Literal

import msgspec

from odoo_instance_sdk.exceptions import ConfigError


def _matches(value: object, annotation: object) -> bool:
    """Check if value matches a type annotation. Handles Union, Literal, and generics.

    msgspec.Struct's constructor does not validate types, and msgspec.convert()
    refuses to validate unions with custom classes (e.g. ``str | Path``), so
    the metaclass uses this isinstance-based check for those fields.
    """
    if annotation is type(None):
        return value is None
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        return any(_matches(value, arg) for arg in typing.get_args(annotation))
    if origin is typing.Literal:
        return value in typing.get_args(annotation)
    if origin is not None:
        return isinstance(value, origin)
    if isinstance(annotation, type):
        return isinstance(value, annotation)
    return True


class _StructMeta(type(msgspec.Struct)):  # type: ignore[misc]
    """Metaclass that validates union types containing custom classes and wraps errors.

    For fields with custom types in unions (e.g. ``str | Path``) msgspec's
    constructor is permissive and msgspec.convert() refuses to validate, so
    the metaclass does an isinstance-based check. ``forbid_unknown_fields=True``
    on the Struct class makes the constructor raise TypeError for unknown
    kwargs, which is re-raised as ConfigError.
    """

    def __call__(cls: type[Any], *args: Any, **kwargs: Any) -> Any:
        for f in msgspec.structs.fields(cls):
            if f.name in kwargs and not _matches(kwargs[f.name], f.type):
                raise ConfigError(
                    f"Invalid type for {f.name}: expected {f.type}, "
                    f"got {type(kwargs[f.name]).__name__}"
                )
        try:
            return super().__call__(*args, **kwargs)
        except TypeError as e:
            raise ConfigError(str(e)) from e


class OdooClientConfig(msgspec.Struct, metaclass=_StructMeta, forbid_unknown_fields=True):
    """Client configuration for OdooInstanceSDK."""

    executable: str | Path
    base_url: str
    master_pwd: str
    backup_dir: str | Path | None = None
    http_timeout: float = 30.0

    def __repr__(self) -> str:
        return (
            f"OdooClientConfig(executable={self.executable!r}, "
            f"base_url={self.base_url!r}, "
            f"master_pwd=<redacted>, "
            f"backup_dir={self.backup_dir!r}, "
            f"http_timeout={self.http_timeout!r})"
        )


class StartConfig(msgspec.Struct, metaclass=_StructMeta, forbid_unknown_fields=True):
    """Typed configuration for launching an Odoo HTTP server process."""

    http_port: int = 8069
    http_interface: str = "0.0.0.0"
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


class CommandResult(msgspec.Struct):
    """Result of a one-shot CLI command."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float


class OdooProcess(msgspec.Struct):
    """Represents a running/exited Odoo server process."""

    id: str
    pid: int
    args: list[str]
    started_at: float


class ProcessStatus(msgspec.Struct):
    """Status of a registered process."""

    state: Literal["running", "exited"]
    returncode: int | None = None


class ReadinessResult(msgspec.Struct):
    """Result of a wait_ready() call."""

    ok: bool
    elapsed: float
    attempts: int
    final_status: str | None = None


class BackupArtifact(msgspec.Struct):
    """A downloaded backup file."""

    path: Path
    source_db: str
    format: Literal["zip", "dump"]
    has_filestore: bool
    source_base_url: str


class RestoreResult(msgspec.Struct):
    """Result of a database restore."""

    new_db: str
    source: BackupArtifact


class DropResult(msgspec.Struct):
    """Result of a database drop."""

    db: str
