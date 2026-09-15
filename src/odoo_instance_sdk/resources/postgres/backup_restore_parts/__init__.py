from __future__ import annotations

# ruff: noqa: F401
import hashlib
import json
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    LockConflictError,
    PostgresClusterError,
    PostgresClusterNotOwnedError,
    PostgresClusterTimeoutError,
    PostgresClusterUnhealthyError,
    PostgresClusterUnreachableError,
    PostgresImageNotTrustedError,
)
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.locks import exclusive_lock_until, postgres_cluster_lock_path
from odoo_instance_sdk.internal.postgres_compose import (
    ComposeRunner,
    SubprocessComposeRunner,
    compose_project_name,
    compose_stop,
    compose_up,
    compose_volume_name,
    derive_state,
    docker_available,
    ensure_docker_or_raise,
    ensure_password_file,
    inspect_container_identity,
    inspect_volume_identity,
    is_oci_digest,
    render_compose_yaml,
    resolve_image_digest,
    write_compose_file_atomic,
)
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.models import ClusterResourceSnapshot, PostgresClusterState, StartConfig
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, PostgresClusterClaim

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue, PlanObservation
    from odoo_instance_sdk.internal.pg.server import ServerSummary
    from odoo_instance_sdk.internal.proc import (
        DeadlineProcessExecutor,
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        RunContext,
    )
    from odoo_instance_sdk.models import ServerUnavailabilityReason
from odoo_instance_sdk.resources.postgres.backup_restore_parts.backup import _BackupMixin
from odoo_instance_sdk.resources.postgres.backup_restore_parts.restore import _RestoreMixin


@dataclass(frozen=True, slots=True, kw_only=True)
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
