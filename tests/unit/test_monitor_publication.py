from __future__ import annotations

from datetime import UTC, datetime

import pytest

from odoo_instance_sdk.internal.publication_routes import PublicationRoute
from odoo_instance_sdk.models import RuntimeMetrics, RuntimeState
from odoo_instance_sdk.resources.monitor.collection_parts.snapshot import _SnapshotMixin


def _runtime(state: RuntimeState, endpoint: str | None) -> RuntimeMetrics:
    return RuntimeMetrics(
        state=state,
        root_pid=42 if endpoint else None,
        child_pids=(),
        process_count=1 if endpoint else 0,
        cpu_percent=0.1 if endpoint else None,
        memory_bytes=1024 if endpoint else None,
        started_at=datetime.now(UTC) if endpoint else None,
        http_url=endpoint,
        http_port=8069 if endpoint else None,
        database_name="odoo" if endpoint else None,
        commit_sha="abc" if endpoint else None,
        branch="main" if endpoint else None,
    )


def _route(kind: str, owner_id: str) -> PublicationRoute:
    return PublicationRoute(
        owner_kind=kind,  # type: ignore[arg-type]
        owner_id=owner_id,
        project_id=owner_id if kind == "project" else "project_a",
        environment_id=owner_id if kind == "environment" else None,
        local_endpoint="http://127.0.0.1:8069",
        external_url=f"https://{owner_id}.example.test",
        route_identity="route-1",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "endpoint", "expected", "reason"),
    [
        (RuntimeState.READY, "http://127.0.0.1:8069", "available", None),
        (RuntimeState.STOPPED, None, "backend_unavailable", "runtime_stopped"),
        (
            RuntimeState.NOT_READY,
            "http://127.0.0.1:8069",
            "backend_unavailable",
            "runtime_not_ready",
        ),
    ],
)
def test_publication_projection_keeps_local_endpoint_and_route_identity(
    state: RuntimeState,
    endpoint: str | None,
    expected: str,
    reason: str | None,
) -> None:
    publication = _SnapshotMixin._publication_snapshot(
        "environment", "env-a", _runtime(state, endpoint), (_route("environment", "env-a"),), None
    )

    assert publication.state == expected
    assert publication.external_url == "https://env-a.example.test"
    assert publication.route_identity == "route-1"
    assert publication.reason == reason


@pytest.mark.unit
def test_publication_projection_isolated_when_route_state_cannot_be_read() -> None:
    publication = _SnapshotMixin._publication_snapshot(
        "project",
        "project-a",
        _runtime(RuntimeState.READY, "http://127.0.0.1:8069"),
        (),
        "publication_state_unavailable",
    )

    assert publication.state == "error"
    assert publication.external_url is None
    assert publication.reason == "publication_state_unavailable"


@pytest.mark.unit
def test_missing_route_is_explicitly_unpublished() -> None:
    publication = _SnapshotMixin._publication_snapshot(
        "project", "project-a", _runtime(RuntimeState.READY, "http://127.0.0.1:8069"), (), None
    )

    assert publication.state == "unpublished"
    assert publication.external_url is None
