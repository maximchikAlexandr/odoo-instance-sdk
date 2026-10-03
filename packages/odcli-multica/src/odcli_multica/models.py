"""Frozen request and observation models for the stateless integration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import msgspec

MULTICA_PY_VERSION = "0.1.0"
MULTICA_PY_REVISION = "c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed"
MULTICA_CLI_MINIMUM = "0.5.3"


@dataclass(frozen=True, slots=True)
class MulticaCompatibility:
    """Verified package/CLI identity required before a native operation."""

    package_version: str
    package_revision: str
    native_cli_version: str
    typed_checkout: bool
    typed_daemon_status: bool
    observed: bool


class ContextRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Explicit identities used to verify one native task checkout."""

    checkout_path: Path
    core_project: Path
    multica_project: str
    issue: str
    run: str
    repository_url: str | None = None
    core_repository_url: str | None = None
    workspace_id: str | None = None
    runtime_id: str | None = None


class PreparationRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """The caller-owned inputs for the second, core-adoption phase."""

    context: ContextRequest
    base_ref: str
    remote_name: str | None = None
    backup_id: uuid.UUID | str | None = None
    source_database: str | None = None


class VerifiedTaskContext(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Finite same-host evidence; this is not a lease or persisted binding."""

    checkout_path: str
    task_root: str
    repository_url: str
    workspace_id: str
    multica_project_id: str
    issue_id: str
    run_id: str
    runtime_id: str
    daemon_id: str
    observed_at: datetime


class ContextVerificationError(ValueError):
    """Raised when typed task evidence is absent, conflicting, or unsafe."""


TaskContext = VerifiedTaskContext
