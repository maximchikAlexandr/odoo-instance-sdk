from __future__ import annotations

import enum

import msgspec


class RuntimeState(enum.StrEnum):
    STOPPED = "stopped"
    READY = "ready"
    NOT_READY = "not_ready"


class GitActivityState(enum.StrEnum):
    CLEAN = "clean"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    ORPHAN = "orphan"


class PidScope(enum.StrEnum):
    HOST = "host"
    DOCKER_VM = "docker_vm"
    UNAVAILABLE = "unavailable"


class PortObservation(enum.StrEnum):
    FREE = "free"
    OCCUPIED = "occupied"
    UNKNOWN = "unknown"


class PgAdminEligibilityState(enum.StrEnum):
    ELIGIBLE = "eligible"
    ENVIRONMENT_NOT_READY = "environment_not_ready"
    DATABASE_UNRESOLVED = "database_unresolved"
    CLUSTER_NOT_OWNED = "cluster_not_owned"
    CLUSTER_UNHEALTHY = "cluster_unhealthy"


class PgAdminOpenState(enum.StrEnum):
    STARTED = "started"
    REUSED = "reused"
    RECONFIGURED = "reconfigured"


class HttpErrorCode(enum.StrEnum):
    invalid_request = "invalid_request"
    monitor_snapshot_failed = "monitor_snapshot_failed"
    environment_not_found = "environment_not_found"
    pgadmin_not_eligible = "pgadmin_not_eligible"
    database_not_found = "database_not_found"
    pgadmin_unavailable = "pgadmin_unavailable"


class PgAdminEligibility(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    state: PgAdminEligibilityState


class PgAdminOpenRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    environment_id: str

    def __repr__(self) -> str:
        return "PgAdminOpenRequest(environment_id=<redacted>)"


class PgAdminOpenResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    state: PgAdminOpenState
    url: str

    def __repr__(self) -> str:
        return f"PgAdminOpenResult(state={self.state!r}, url=<redacted>)"


class HttpError(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    code: HttpErrorCode
    message: str

    def __repr__(self) -> str:
        return f"HttpError(code={self.code!r}, message=<redacted>)"


class StopEnvironmentResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Outcome of stopping a persisted environment runtime."""

    status: str
    environment_id: str


class DetachedLaunchResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Outcome of launching Odoo detached through the persisted runtime identity."""

    pid: int
    owner_kind: str
    owner_id: str
    http_endpoint: str
    log_path: str
