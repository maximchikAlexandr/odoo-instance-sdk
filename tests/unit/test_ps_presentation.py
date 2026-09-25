from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from click.testing import CliRunner
from rich import box
from rich.table import Table

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.output import render_rich_text
from odoo_instance_sdk.commands.ps import (
    _backend_row,
    _process_column_widths,
    _process_table,
    _render_ps_rich,
)
from odoo_instance_sdk.models import (
    BackendGroupReason,
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

if TYPE_CHECKING:
    from rich.console import Group

    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor


def _backend(
    *,
    database: str = "demo",
    reason: BackendGroupReason = "unique_database",
    pid: int = 700,
    scope: PidScope = PidScope.HOST,
) -> BackendProcessGroup:
    return BackendProcessGroup(
        database=database,
        sessions=(
            BackendSession(
                pid=pid,
                state="idle",
                application_name="odoo",
                user_name="odoo",
            ),
        ),
        pid_scope=scope,
        host_pids=(pid,),
        cpu_percent=None,
        memory_bytes=None,
        reason=reason,
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
                backend_groups=(
                    _backend(
                        database="shared_db",
                        reason="shared_database",
                        pid=701,
                        scope=PidScope.DOCKER_VM,
                    ),
                ),
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
                backend_groups=(_backend(scope=PidScope.DOCKER_VM),),
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

    _assert_process_table_boundaries(output)
    assert "backend" in output
    assert "docker_vm" in output
    assert "stopped" in output
    assert "source=odcli-" in output
    assert "codex" in output
    assert all(len(line) <= width for line in output.splitlines())


@pytest.mark.unit
def test_single_process_section_does_not_add_layout_placeholder() -> None:
    inventory = _inventory()
    single_section = ProcessInventory(
        schema_version=inventory.schema_version,
        generated_at=inventory.generated_at,
        sample_time=inventory.sample_time,
        project_id=inventory.project_id,
        shared=inventory.shared,
        main_checkout=None,
        environments=(),
    )

    rendered = _render_ps_rich(single_section, width=80)
    tables = [item for item in rendered.renderables if isinstance(item, Table)]

    assert len(tables) == 1
    _assert_process_table_boundaries(render_rich_text(rendered, width=80))


@pytest.mark.unit
def test_process_layout_measures_unicode_and_multiline_cells() -> None:
    rows = (("表", "ready", "host:1", "1", "1.0%", "2 KiB", "宽\n表格"),)
    widths = _process_column_widths(rows, width=80)
    output = render_rich_text(_process_table(rows, widths=widths), width=80)

    assert "表" in output
    assert "表格" in output
    assert all(len(line) <= 80 for line in output.splitlines())


@pytest.mark.unit
def test_public_ps_one_shot_uses_aligned_frame_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMonitor:
        def processes_command(self, *, project_id: str | None = None) -> object:
            return type("Command", (), {"run": lambda _self: _inventory()})()

    monkeypatch.setattr("odoo_instance_sdk.commands.ps.EnvironmentMonitor", FakeMonitor)
    result = CliRunner().invoke(cli, ["ps", "--all-projects"], env={"COLUMNS": "80"})

    assert result.exit_code == 0, result.output
    _assert_process_table_boundaries(result.output)
    assert all(len(line) <= 80 for line in result.output.splitlines())


@pytest.mark.unit
def test_ps_watch_recalculates_layout_for_each_successful_refresh(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.commands import ps as ps_command_module

    class FakeConsole:
        def __init__(self) -> None:
            self.widths = iter((80, 120))
            self.reads = 0

        @property
        def width(self) -> int:
            self.reads += 1
            return next(self.widths)

    class FakeLive:
        def __init__(self) -> None:
            self.frames: list[Group] = []

        def __enter__(self) -> FakeLive:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def update(self, renderable: Group, *, refresh: bool) -> None:
            self.frames.append(renderable)

    class FakeMonitor:
        def __init__(self) -> None:
            self.samples = iter((_inventory(), _inventory()))

        def processes(self, *, project_id: str | None = None) -> ProcessInventory:
            try:
                return next(self.samples)
            except StopIteration as exc:
                raise KeyboardInterrupt from exc

    console = FakeConsole()
    live = FakeLive()
    monkeypatch.setattr(ps_command_module, "Console", lambda: console)
    monkeypatch.setattr(ps_command_module, "Live", lambda *_args, **_kwargs: live)
    monkeypatch.setattr(time, "sleep", lambda _interval: None)

    with pytest.raises(KeyboardInterrupt):
        ps_command_module._run_ps_live(
            cast("EnvironmentMonitor", FakeMonitor()), project_id=None, interval=0.1
        )

    assert console.reads == 2
    assert len(live.frames) == 2
    first, second = live.frames
    first_tables = [item for item in first.renderables if isinstance(item, Table)]
    second_tables = [item for item in second.renderables if isinstance(item, Table)]
    assert [column.width for column in first_tables[0].columns] != [
        column.width for column in second_tables[0].columns
    ]
    for frame, width in zip(live.frames, (80, 120)):
        _assert_process_table_boundaries(render_rich_text(frame, width=width))


def _assert_process_table_boundaries(output: str) -> None:
    borders = tuple(
        (len(line), tuple(index for index, char in enumerate(line) if char in "┬┐"))
        for line in output.splitlines()
        if line.startswith("┌")
    )
    assert len(borders) >= 1
    assert len(set(borders)) == 1


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
    assert "storage volume=16.0 KiB" in output
    assert capsys.readouterr().out == ""


@pytest.mark.unit
def test_process_cells_escape_csi_and_osc_sequences() -> None:
    unsafe = BackendProcessGroup(
        database="demo",
        sessions=(
            BackendSession(
                pid=700,
                state="idle",
                application_name="client\x1b[2J",
                user_name="user\x1b]8;;https://evil.example\x07",
            ),
        ),
        pid_scope=PidScope.HOST,
        host_pids=(700,),
        cpu_percent=None,
        memory_bytes=None,
        reason="unique_database",
    )

    output = render_rich_text(_process_table([_backend_row(unsafe)]), width=180)

    assert "\x1b[2J" not in output
    assert "\x1b]8;;" not in output
    assert r"\x1b[2J" in output
    assert r"\x1b]8;;" in output


@pytest.mark.unit
def test_process_sections_preserve_attribution_order_and_unavailable_backend_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.commands import ps as ps_command_module

    collector_called = False

    class ExplodingMonitor:
        def __init__(self) -> None:
            nonlocal collector_called
            collector_called = True
            raise AssertionError("rendering must not construct a monitor")

    monkeypatch.setattr(ps_command_module, "EnvironmentMonitor", ExplodingMonitor)
    rendered = _render_ps_rich(_inventory())
    tables = [item for item in rendered.renderables if isinstance(item, Table)]
    output = render_rich_text(rendered, width=180)

    assert not collector_called
    assert output.index("Shared resources") < output.index("Main checkout")
    assert output.index("Main checkout") < output.index("Environment demo")
    assert output.count("database=shared_db") == 1
    assert output.count("attribution=shared_database") == 1
    assert output.count("attribution=unique_database") == 1

    shared_backend = _backend_row(
        _backend(
            database="shared_db",
            reason="shared_database",
            pid=701,
            scope=PidScope.DOCKER_VM,
        )
    )
    assert shared_backend[2] == "docker_vm:701"
    assert shared_backend[4] == "—"
    assert shared_backend[5] == "—"
    assert "20.0%" not in shared_backend[4]
    assert "4.0 KiB" not in shared_backend[5]
    assert "storage volume" not in render_rich_text(tables[0], width=180)


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
