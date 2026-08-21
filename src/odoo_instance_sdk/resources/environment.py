from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Union

import msgspec

from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient

EnvironmentSelector = Union[str, "DevelopmentEnvironment"]


class EnvironmentState(enum.StrEnum):
    CREATING = "creating"
    READY = "ready"
    FAILED = "failed"
    REMOVING = "removing"
    CLEANUP_FAILED = "cleanup_failed"
    REMOVED = "removed"


class EnvironmentDatabaseMode(enum.StrEnum):
    SHARED = "shared"
    COPY = "copy"


class DevelopmentEnvironment(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    id: uuid.UUID
    name: str
    repository_root: str
    git_common_dir: str
    branch: str
    base_ref: str
    worktree_path: str
    generated_config_path: str
    python_environment_path: str
    python_environment_owned: bool
    dependency_lock_path: str
    http_interface: str
    http_port: int
    db_mode: EnvironmentDatabaseMode
    source_db_name: str | None = None
    target_db_name: str | None = None
    backup_id: uuid.UUID | None = None
    state: EnvironmentState
    created_at: datetime
    last_used_at: datetime | None = None
    removed_at: datetime | None = None
    last_error: str | None = None


class EnvironmentCheckoutOptions(msgspec.Struct, frozen=True, kw_only=True):
    base_ref: str | None = None
    name: str | None = None
    config_path: Path | None = None
    db_mode: EnvironmentDatabaseMode = EnvironmentDatabaseMode.SHARED
    source_database: str | None = None
    target_database: str | None = None
    odoo_bin: Path | None = None
    python: str | Path | None = None
    create_venv: bool = False
    http_port: int | None = None


@dataclass(slots=True, kw_only=True)
class EnvironmentResource:
    _client: OdooClient

    def checkout(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> DevelopmentEnvironment:
        raise NotImplementedError("implemented in Slice 2")

    def sync_python(
        self,
        selector: EnvironmentSelector,
        *,
        upgrade: bool = False,
    ) -> DevelopmentEnvironment:
        raise NotImplementedError("implemented in Slice 2")

    def get(self, selector: EnvironmentSelector) -> DevelopmentEnvironment:
        raise NotImplementedError("implemented in Slice 2")

    def list(
        self,
        *,
        project: ProjectConfig | Path | None = None,
        include_removed: bool = False,
    ) -> list[DevelopmentEnvironment]:
        raise NotImplementedError("implemented in Slice 2")

    def remove(self, selector: EnvironmentSelector) -> None:
        raise NotImplementedError("implemented in Slice 2")
