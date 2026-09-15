from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import msgspec

from odoo_instance_sdk.models._validators import (
    _require_datetime,
    _require_non_negative_float,
    _require_non_negative_int,
    _require_tuple,
)
from odoo_instance_sdk.models.backup import EnvironmentState
from odoo_instance_sdk.models.footprint import StorageFootprint
from odoo_instance_sdk.models.monitor import ClusterSnapshot
from odoo_instance_sdk.models.runtime import PidScope, RuntimeState


class ProcessGroupRuntime(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Aggregated CPU/RSS and identity for one Odoo process tree."""

    root_pid: int | None
    child_pids: tuple[int, ...] = ()
    process_count: int = 0
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    started_at: datetime | None = None
    state: RuntimeState = RuntimeState.STOPPED
    http_url: str | None = None
    http_port: int | None = None
    database_name: str | None = None
    commit_sha: str | None = None
    branch: str | None = None

    def __post_init__(self) -> None:
        _require_tuple(self.child_pids, "ProcessGroupRuntime.child_pids")
        _require_non_negative_int(self.process_count, "ProcessGroupRuntime.process_count")
        _require_non_negative_float(self.cpu_percent, "ProcessGroupRuntime.cpu_percent")
        _require_non_negative_float(
            self.memory_bytes if self.memory_bytes is not None else None,
            "ProcessGroupRuntime.memory_bytes",
        )
        if self.memory_bytes is not None:
            _require_non_negative_int(self.memory_bytes, "ProcessGroupRuntime.memory_bytes")


type BackendUnavailabilityReason = Literal[
    "psql_missing",
    "credentials_missing",
    "server_unreachable",
    "maintenance_database_unavailable",
    "authentication_failed",
    "privilege_denied",
    "timeout",
    "query_failed",
    "invalid_response",
    "vm_scoped_pid",
    "stale_pid",
]


type BackendGroupReason = Literal["unique_database", "shared_database"]


class BackendSession(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One PostgreSQL backend row from ``pg_stat_activity``."""

    pid: int
    state: str | None = None
    application_name: str | None = None
    user_name: str | None = None
    client_address: str | None = None
    backend_start: datetime | None = None
    query_start: datetime | None = None
    transaction_start: datetime | None = None
    wait_event_type: str | None = None
    wait_event: str | None = None

    def __post_init__(self) -> None:
        _require_non_negative_int(self.pid, "BackendSession.pid")


class BackendProcessGroup(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One PostgreSQL backend group attributable to an owner or shared resources."""

    database: str
    sessions: tuple[BackendSession, ...] = ()
    pid_scope: PidScope = PidScope.UNAVAILABLE
    host_pids: tuple[int, ...] = ()
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    sample_time: datetime | None = None
    reason: BackendGroupReason = "unique_database"
    unavailability_reason: BackendUnavailabilityReason | None = None

    def __post_init__(self) -> None:
        _require_tuple(self.sessions, "BackendProcessGroup.sessions")
        _require_tuple(self.host_pids, "BackendProcessGroup.host_pids")
        for pid in self.host_pids:
            _require_non_negative_int(pid, "BackendProcessGroup.host_pids item")
        _require_non_negative_float(self.cpu_percent, "BackendProcessGroup.cpu_percent")
        if self.memory_bytes is not None:
            _require_non_negative_int(self.memory_bytes, "BackendProcessGroup.memory_bytes")
        if self.reason not in {"unique_database", "shared_database"}:
            raise ValueError(f"unknown BackendProcessGroup.reason: {self.reason!r}")
        if self.unavailability_reason is not None and self.unavailability_reason not in {
            "psql_missing",
            "credentials_missing",
            "server_unreachable",
            "maintenance_database_unavailable",
            "authentication_failed",
            "privilege_denied",
            "timeout",
            "query_failed",
            "invalid_response",
            "vm_scoped_pid",
            "stale_pid",
        }:
            raise ValueError(
                f"unknown BackendProcessGroup.unavailability_reason: {self.unavailability_reason!r}"
            )

    @property
    def connection_count(self) -> int:
        return len(self.sessions)


type ProcessContributionSource = Literal["odcli-codex", "multica"]
type ProcessContributionOwnerKind = Literal["shared", "project", "environment"]
type ProcessContributionAvailability = Literal[
    "available",
    "unavailable",
    "stale",
]


class ProcessContribution(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One bounded external process contribution.

    Carries ``source``, a stable local identity, lifecycle state, owner kind/ID,
    confirmed PIDs and PID scope when observable, CPU/RSS, sample time, and
    explicit availability. SHALL NOT carry prompts, transcripts, secrets, raw
    command lines, or environment values.
    """

    source: ProcessContributionSource
    local_identity: str
    owner_kind: ProcessContributionOwnerKind
    owner_id: str | None = None
    lifecycle_state: str
    root_pid: int | None = None
    child_pids: tuple[int, ...] = ()
    pid_scope: PidScope = PidScope.UNAVAILABLE
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    sample_time: datetime | None = None
    availability: ProcessContributionAvailability = "unavailable"
    unavailability_reason: str | None = None

    def __post_init__(self) -> None:
        if self.source not in {"odcli-codex", "multica"}:
            raise ValueError(f"unknown ProcessContribution.source: {self.source!r}")
        if self.owner_kind not in {"shared", "project", "environment"}:
            raise ValueError(f"unknown ProcessContribution.owner_kind: {self.owner_kind!r}")
        if self.availability not in {"available", "unavailable", "stale"}:
            raise ValueError(f"unknown ProcessContribution.availability: {self.availability!r}")
        if not self.local_identity.strip():
            raise ValueError("ProcessContribution.local_identity must not be empty")
        if not self.lifecycle_state.strip():
            raise ValueError("ProcessContribution.lifecycle_state must not be empty")
        _require_tuple(self.child_pids, "ProcessContribution.child_pids")
        if self.root_pid is not None:
            _require_non_negative_int(self.root_pid, "ProcessContribution.root_pid")
        for pid in self.child_pids:
            _require_non_negative_int(pid, "ProcessContribution.child_pids item")
        _require_non_negative_float(self.cpu_percent, "ProcessContribution.cpu_percent")
        if self.memory_bytes is not None:
            _require_non_negative_int(self.memory_bytes, "ProcessContribution.memory_bytes")


class SharedResourcesBlock(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Process/resource block owned once at the project level."""

    project_id: str
    postgres_container: ClusterSnapshot | None = None
    backend_groups: tuple[BackendProcessGroup, ...] = ()
    external_contributions: tuple[ProcessContribution, ...] = ()

    def __post_init__(self) -> None:
        _require_tuple(self.backend_groups, "SharedResourcesBlock.backend_groups")
        _require_tuple(
            self.external_contributions,
            "SharedResourcesBlock.external_contributions",
        )


class CheckoutProcessBlock(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Process/resource block for the main checkout or one environment worktree."""

    owner_kind: Literal["main_checkout", "environment"]
    owner_id: str
    project_id: str
    name: str
    branch: str | None = None
    database: str | None = None
    db_mode: Literal["shared", "copy"] | None = None
    lifecycle_state: EnvironmentState | None = None
    odoo: ProcessGroupRuntime | None = None
    backend_groups: tuple[BackendProcessGroup, ...] = ()
    external_contributions: tuple[ProcessContribution, ...] = ()
    storage: StorageFootprint | None = None

    def __post_init__(self) -> None:
        if self.owner_kind not in {"main_checkout", "environment"}:
            raise ValueError(f"unknown CheckoutProcessBlock.owner_kind: {self.owner_kind!r}")
        _require_tuple(self.backend_groups, "CheckoutProcessBlock.backend_groups")
        _require_tuple(
            self.external_contributions,
            "CheckoutProcessBlock.external_contributions",
        )


class ProcessInventory(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One frozen projection of process and resource usage from one snapshot.

    Three ownership kinds in order: shared project resources, the main checkout,
    and each non-removed environment. Rich, JSON, and TOON project the same
    model; three different field sets SHALL NOT exist.
    """

    schema_version: int
    generated_at: Annotated[datetime, "odcli-structural"]
    sample_time: Annotated[datetime, "odcli-structural"]
    project_id: str | None
    shared: tuple[SharedResourcesBlock, ...] = ()
    main_checkout: CheckoutProcessBlock | None = None
    environments: tuple[CheckoutProcessBlock, ...] = ()
    complete: Annotated[bool, "odcli-structural"] = True
    unavailability_reason: str | None = None

    def __post_init__(self) -> None:
        _require_tuple(self.shared, "ProcessInventory.shared")
        _require_tuple(self.environments, "ProcessInventory.environments")
        _require_datetime(self.sample_time, "ProcessInventory.sample_time")
        _require_datetime(self.generated_at, "ProcessInventory.generated_at")
