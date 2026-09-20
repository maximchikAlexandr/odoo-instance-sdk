from __future__ import annotations

from typing import Literal

type ModuleJsonValue = (
    None | bool | int | float | str | list["ModuleJsonValue"] | dict[str, "ModuleJsonValue"]
)

type ClusterUnavailabilityReason = Literal[
    "external_not_owned",
    "stopped",
    "missing",
    "docker_unavailable",
    "inspect_failed",
    "stats_failed",
]

type ServerUnavailabilityReason = Literal[
    "psql_missing",
    "credentials_missing",
    "server_unreachable",
    "maintenance_database_unavailable",
    "authentication_failed",
    "privilege_denied",
    "timeout",
    "query_failed",
    "invalid_response",
]
