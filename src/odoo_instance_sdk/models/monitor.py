from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import msgspec

from odoo_instance_sdk.models._literals import (
    ClusterUnavailabilityReason,
    ServerUnavailabilityReason,
)
from odoo_instance_sdk.models.backup import EnvironmentState, PostgresClusterState
from odoo_instance_sdk.models.footprint import (
    ClusterContainer,
    ClusterEndpoint,
    ClusterMetrics,
    EnvironmentArtifacts,
    RuntimeMetrics,
    StorageFootprint,
)
from odoo_instance_sdk.models.git import GitActivity
from odoo_instance_sdk.models.postgres import PostgresServerInfo
from odoo_instance_sdk.models.runtime import PgAdminEligibility, PortObservation


class ClusterSnapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    mode: Literal["external", "compose"]
    owned: bool
    state: PostgresClusterState
    endpoint: ClusterEndpoint | None
    container: ClusterContainer | None
    metrics: ClusterMetrics | None
    unavailability_reason: ClusterUnavailabilityReason | None
    sampled_at: datetime | None
    server: PostgresServerInfo | None = None
    server_unavailability_reason: ServerUnavailabilityReason | None = None

    def __post_init__(self) -> None:
        if (
            self.server_unavailability_reason is not None
            and self.server_unavailability_reason
            not in {
                "psql_missing",
                "credentials_missing",
                "server_unreachable",
                "maintenance_database_unavailable",
                "authentication_failed",
                "privilege_denied",
                "timeout",
                "query_failed",
                "invalid_response",
            }
        ):
            raise ValueError(
                "unknown ClusterSnapshot.server_unavailability_reason: "
                f"{self.server_unavailability_reason!r}"
            )


class EnvironmentSnapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    id: str
    project_id: str
    name: str
    branch: str
    short_sha: str | None
    db_mode: Literal["shared", "copy"]
    database: str | None
    lifecycle_state: EnvironmentState
    allocated_http_port: int | None
    observed_port: PortObservation | None
    artifacts: EnvironmentArtifacts
    runtime: RuntimeMetrics
    git: GitActivity
    storage: StorageFootprint
    pgadmin: PgAdminEligibility


class ProjectSummary(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    id: str
    name: str
    display_hint: str
    repository_root: str
    environment_count: int
    cluster: ClusterSnapshot | None
    runtime: RuntimeMetrics | None


class CheckoutClusterSummary(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Compact cluster summary attached to one project group in CheckoutInventory."""

    project_id: str
    mode: Literal["external", "compose"]
    state: PostgresClusterState
    unavailability_reason: ClusterUnavailabilityReason | None = None


type CheckoutRowKind = Literal["main", "environment"]
type CheckoutOdooStatus = Literal["running", "stopped", "unavailable"]
type CheckoutDatabaseMode = Literal["shared", "copy"]


class CheckoutGitFacts(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Git facts of one checkout row: branch, short SHA, ahead/behind, diff."""

    branch: str
    short_sha: str | None = None
    ahead: int | None = None
    behind: int | None = None
    added_lines: int | None = None
    deleted_lines: int | None = None


class EnvironmentFactsSummary(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """One frozen environment-facts summary from a single provider.

    The minimal summary contains ``provider``, ``state``, ``text``, and
    concrete typed details. ``Any``, ``object``, and Rich renderables are
    not used. A failed/incompatible/slow provider SHALL NOT hide core rows
    or other providers' summaries.
    """

    provider: str
    state: Literal["available", "unavailable", "error"]
    text: str
    details: tuple[tuple[str, str], ...] = ()


class CheckoutRow(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One immutable checkout row of a CheckoutInventory.

    ``kind = main`` rows are the main checkout of a project and are not
    synthetic environments; ``kind = environment`` rows are non-removed
    catalog environments. The row carries only working identity and state:
    kind/name, project identity, branch and short SHA, canonical worktree
    path, compact Odoo status without PID or metrics, and the bound
    database/DB mode when applicable.
    """

    kind: CheckoutRowKind
    project_id: str
    environment_id: str | None = None
    name: str
    worktree_path: str
    odoo_status: CheckoutOdooStatus
    db_mode: CheckoutDatabaseMode | None = None
    database: str | None = None
    git: CheckoutGitFacts | None = None
    lifecycle_state: EnvironmentState | None = None
    facts: tuple[EnvironmentFactsSummary, ...] = ()


class CheckoutInventory(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One frozen projection of checkout rows from one snapshot.

    Built from one ``EnvironmentMonitor.snapshot()`` per sample plus Git
    facts of the main checkout. The main checkout of each project is the
    first row of its group with ``kind = main``; environment rows follow
    with ``kind = environment``. Rich, JSON, and TOON project the same
    model; three different field sets SHALL NOT exist.
    """

    schema_version: int
    generated_at: Annotated[datetime, "odcli-structural"]
    sample_time: Annotated[datetime, "odcli-structural"]
    project_id: str | None
    rows: tuple[CheckoutRow, ...]
    clusters: tuple[CheckoutClusterSummary, ...]
    complete: Annotated[bool, "odcli-structural"] = True
    unavailability_reason: str | None = None


class Snapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    schema_version: int
    generated_at: Annotated[datetime, "odcli-structural"]
    projects: tuple[ProjectSummary, ...]
    environments: tuple[EnvironmentSnapshot, ...]
