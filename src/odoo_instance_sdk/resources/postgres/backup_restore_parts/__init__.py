from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from odoo_instance_sdk.internal.postgres_compose import (
    ComposeRunner,
    SubprocessComposeRunner,
)
from odoo_instance_sdk.resources.postgres.backup_restore_parts.backup import _BackupMixin
from odoo_instance_sdk.resources.postgres.backup_restore_parts.restore import _RestoreMixin


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class PostgresCluster(_BackupMixin, _RestoreMixin):
    """Project-level PostgreSQL cluster: ownership, status, readiness, managed lifecycle.

    This is the single operational abstraction — no Resource, no factory, no
    ``client.postgres`` facade. ``ensure_running()`` is the required idempotent
    operation; ``start()`` deliberately does not exist.
    """

    _repository_root: Path
    _project_id: str
    _mode: Literal["external", "compose"]
    _endpoint_host: str
    _endpoint_port: int
    _image: str | None = None
    _user: str | None = None
    _compose_runner: ComposeRunner = field(default_factory=SubprocessComposeRunner)
