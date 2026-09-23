from __future__ import annotations

from datetime import UTC, datetime

import pytest
from rich import box
from rich.table import Table

from odoo_instance_sdk.commands.output import render_rich_text
from odoo_instance_sdk.commands.ps import _render_ps_rich
from odoo_instance_sdk.models import (
    BackendProcessGroup,
    BackendSession,
    CheckoutProcessBlock,
    ClusterContainer,
    ClusterEndpoint,
    ClusterMetrics,
    ClusterSnapshot,
    PidScope,
    PostgresClusterState,
    PostgresServerInfo,
    ProcessContribution,
    ProcessGroupRuntime,
    ProcessInventory,
    RuntimeState,
    SharedResourcesBlock,
)


def _backend(*, scope: PidScope = PidScope.HOST) -> BackendProcessGroup:
    return BackendProcessGroup(
        database="demo",
        sessions=(
            BackendSession(
                pid=700,
                state="idle",
                application_name="odoo",
                user_name="odoo",
            ),
        ),
        pid_scope=scope,
        host_pids=(700,),
        cpu_percent=None,
        memory_bytes=None,
        reason="unique_database",
        unavailability_reason="vm_scoped_pid" if scope is PidScope.DOCKER_VM else None,
    )


def _contribution() -> ProcessContribution:
    return ProcessContribution(
        source="odcli-codex",
        local_identity="session-1",
        owner_kind="environment",
        owner_id="env-1",
        lifecycle_state="running",
        root_pid=800,
        child_pids=(801,),
        pid_scope=PidScope.HOST,
        cpu_percent=3.0,
        memory_bytes=2048,
        availability="available",
    )


def _stopped_runtime() -> ProcessGroupRuntime:
    return ProcessGroupRuntime(
        root_pid=None,
        child_pids=(),
        process_count=0,
        cpu_percent=None,
        memory_bytes=None,
        state=RuntimeState.STOPPED,
    )


def _cluster() -> ClusterSnapshot:
    return ClusterSnapshot(
        mode="compose",
        owned=True,
        state=PostgresClusterState.HEALTHY,
        endpoint=ClusterEndpoint(host="127.0.0.1", port=5432),
        container=ClusterContainer(
            id="container-id-123456",
            name="postgres",
            image="postgres:16",
            pid=900,
            pid_scope=PidScope.DOCKER_VM,
        ),
        metrics=ClusterMetrics(
            cpu_percent=20.0,
            memory_usage_bytes=4096,
            memory_limit_bytes=8192,
            volume_usage_bytes=16384,
            sampled_at=datetime(2024, 1, 1, tzinfo=UTC),
        ),
        unavailability_reason=None,
        sampled_at=datetime(2024, 1, 1, tzinfo=UTC),
        server=PostgresServerInfo(
            version="16.4",
            postmaster_started_at=datetime(2024, 1, 1, tzinfo=UTC),
            uptime_seconds=10,
            connections_total=4,
            connections_active=2,
            connections_idle=2,
            max_connections=100,
            connectable_databases=1,
        ),
    )


def _inventory() -> ProcessInventory:
    runtime = ProcessGroupRuntime(
        root_pid=100,
        child_pids=(101, 102),
        process_count=3,
        cpu_percent=12.5,
        memory_bytes=4096,
        state=RuntimeState.READY,
        http_url="http://127.0.0.1:8069",
        database_name="demo",
        branch="main",
        commit_sha="abc123",
    )
    return ProcessInventory(
        schema_version=1,
        generated_at=datetime(2024, 1, 1, tzinfo=UTC),
        sample_time=datetime(2024, 1, 1, tzinfo=UTC),
        project_id="project-1",
        shared=(
            SharedResourcesBlock(
                project_id="project-1",
                postgres_container=_cluster(),
                backend_groups=(_backend(scope=PidScope.DOCKER_VM),),
            ),
        ),
        main_checkout=CheckoutProcessBlock(
            owner_kind="main_checkout",
            owner_id="main_checkout",
            project_id="project-1",
            name="main",
            odoo=runtime,
            backend_groups=(),
            external_contributions=(),
        ),
        environments=(
            CheckoutProcessBlock(
                owner_kind="environment",
                owner_id="env-1",
                project_id="project-1",
                name="demo",
                odoo=_stopped_runtime(),
                external_contributions=(_contribution(),),
            ),
        ),
    )


@pytest.mark.unit
def test_each_inventory_section_has_one_common_bordered_table() -> None:
    rendered = _render_ps_rich(_inventory())
    tables = [item for item in rendered.renderables if isinstance(item, Table)]

    assert len(tables) == 3
    for table in tables:
        assert table.box == box.SQUARE
        assert table.show_lines is True
        assert [column.header for column in table.columns] == [
            "Type",
            "State",
            "PID / scope",
            "Processes",
            "CPU",
            "Memory",
            "Details",
        ]


@pytest.mark.unit
@pytest.mark.parametrize("width", [80, 120, 180])
def test_process_tables_wrap_without_dropping_typed_rows(width: int) -> None:
    output = render_rich_text(_render_ps_rich(_inventory(), width=width), width=width)

    assert "backend" in output
    assert "docker_vm" in output
    assert "stopped" in output
    assert "source=odcli-" in output
    assert "codex" in output
    assert all(len(line) <= width for line in output.splitlines())


@pytest.mark.unit
def test_process_projection_keeps_type_specific_details_and_is_pure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inventory = _inventory()
    output = render_rich_text(_render_ps_rich(inventory), width=180)

    assert "database=demo" in output
    assert "endpoint=http://127.0.0.1:8069" in output
    assert "branch=main" in output
    assert "commit=abc123" in output
    assert "endpoint=127.0.0.1:5432" in output
    assert "version=16.4" in output
    assert "connections=2/4" in output
    assert "attribution=unique_database" in output
    assert "identity=odoo/odoo/idle" in output
    assert "availability=vm_scoped_pid" in output
    assert "identity=session-1" in output
    assert capsys.readouterr().out == ""


@pytest.mark.unit
def test_empty_section_has_explicit_unavailable_row() -> None:
    inventory = ProcessInventory(
        schema_version=1,
        generated_at=datetime(2024, 1, 1, tzinfo=UTC),
        sample_time=datetime(2024, 1, 1, tzinfo=UTC),
        project_id="project-1",
        shared=(SharedResourcesBlock(project_id="project-1"),),
        main_checkout=None,
    )

    output = render_rich_text(_render_ps_rich(inventory), width=180)

    assert "no proven process entry" in output
    assert "unavailable" in output
