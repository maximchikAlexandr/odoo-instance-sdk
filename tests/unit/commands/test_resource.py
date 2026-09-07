from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands import resource as resource_command
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.paths import get_backups_dir, get_catalog_path
from odoo_instance_sdk.internal.pg.inventory import DatabaseInventoryItem, DatabaseInventoryResult
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    RunContext,
    StepEvent,
    SubprocessExecutor,
    prepared_command,
)
from odoo_instance_sdk.models import (
    ClusterContainer,
    ClusterMetrics,
    ClusterResourceSnapshot,
    PidScope,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster


@pytest.mark.unpatched_xdg
def test_normal_catalog_and_cache_paths_create_fresh_xdg_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))

    catalog_path = get_catalog_path()
    backups_dir = get_backups_dir()

    assert catalog_path.parent.is_dir()
    assert backups_dir.parent.is_dir()

    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = BackupCatalog(db_path=catalog_path)
    catalog.close()
    assert catalog_path.is_file()


def test_resource_command_plan_composes_prepared_probe_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = PreparedStep(
        step_id="probe.psql",
        argv=("psql", "--no-password"),
        read_only=True,
    )
    child = prepared_command(
        lambda _context: DatabaseInventoryResult(cluster="127.0.0.1:5432", databases=()),
        (probe,),
        executor=SubprocessExecutor(),
    )
    plan = resource_command._ResourcePlan(
        catalog=None,
        data_root=tmp_path / "data",
        backup_root=tmp_path / "backups",
        database_command=child,
    )
    monkeypatch.setattr(resource_command, "_build_resource_plan", lambda _executor: plan)

    command = resource_command._resource_command()

    assert [step.step_id for step in command.plan.steps] == ["resource.inventory", "probe.psql"]
    assert [step.step_id for step in command.commands] == ["probe.psql"]


def test_resource_command_closes_failed_child_action_before_outer_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = PreparedAction(
        step_id="probe.volume",
        action="docker-volume-inspect",
        read_only=True,
    )

    def callback(context: RunContext[DatabaseInventoryResult]) -> DatabaseInventoryResult:
        context.action(probe.step_id)
        raise RuntimeError("volume probe failed")

    child = Command.create(
        ExecutionPlan(steps=(probe.public_projection(),)),
        callback,
        (probe,),
        executor=SubprocessExecutor(),
    )
    plan = resource_command._ResourcePlan(
        catalog=None,
        data_root=tmp_path / "data",
        backup_root=tmp_path / "backups",
        database_command=child._prepared(),
    )
    monkeypatch.setattr(resource_command, "_build_resource_plan", lambda _executor: plan)

    events: list[StepEvent] = []
    result = resource_command._resource_command().run(observer=events.append)

    assert result is not None
    assert [(event.step_id, event.kind) for event in events] == [
        ("resource.inventory", "started"),
        ("probe.volume", "started"),
        ("probe.volume", "failed"),
        ("resource.inventory", "completed"),
    ]


@pytest.mark.unpatched_xdg
def test_resource_leaves_do_not_create_absent_xdg_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_root = tmp_path / "xdg-data"
    cache_root = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_root))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))

    runner = CliRunner()
    for leaf in ("list", "doctor"):
        result = runner.invoke(cli, ["resource", leaf, "--format", "json"])
        assert result.exit_code == 0, result.output
        assert not data_root.exists()
        assert not cache_root.exists()


@pytest.mark.parametrize("snapshot_available", [True, False], ids=["measured", "unavailable"])
def test_resource_project_projection_includes_measured_and_unavailable_cluster_leaves(
    monkeypatch: pytest.MonkeyPatch,
    project_manifest: Path,
    source_config: Path,
    tmp_path: Path,
    snapshot_available: bool,
) -> None:
    data_root = tmp_path / "xdg-data"
    cache_root = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_root))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))
    source_config.write_text(
        source_config.read_text(encoding="utf-8") + "logfile = /tmp/project.log\n",
        encoding="utf-8",
    )
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[postgres]\nmode = "compose"\nimage = "postgres:16"\nport = 5432\n',
        encoding="utf-8",
    )
    database = DatabaseInventoryResult(
        cluster="127.0.0.1:5432",
        databases=(
            DatabaseInventoryItem(
                cluster="127.0.0.1:5432",
                cluster_id=None,
                name="comerta",
                logical_size_bytes=1024,
                active_sessions=0,
                is_default=True,
            ),
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.resource._project_root", lambda: project_manifest
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.inventory.build_database_inventory_command",
        lambda *_args, **_kwargs: Command.create(ExecutionPlan(), lambda _context: database, ()),
    )
    snapshot = ClusterResourceSnapshot(
        container=ClusterContainer(
            id="container",
            name="postgres",
            image="postgres:16",
            pid=None,
            pid_scope=PidScope.DOCKER_VM,
        ),
        metrics=ClusterMetrics(
            cpu_percent=None,
            memory_usage_bytes=None,
            memory_limit_bytes=None,
            volume_usage_bytes=4096,
            sampled_at=None,
        ),
        unavailability_reason=None,
        sampled_at=None,
    )
    probe = PreparedAction(
        step_id="resource.snapshot.test",
        action="docker-volume-inspect",
        read_only=True,
    )

    def snapshot_callback(context: RunContext[ClusterResourceSnapshot]) -> ClusterResourceSnapshot:
        context.action(probe.step_id)
        if not snapshot_available:
            raise RuntimeError("volume probe unavailable")
        context.complete_action(probe.step_id)
        return snapshot

    snapshot_command = Command.create(
        ExecutionPlan(steps=(probe.public_projection(),)),
        snapshot_callback,
        (probe,),
        executor=SubprocessExecutor(),
    )
    monkeypatch.setattr(
        PostgresCluster,
        "resource_snapshot_command",
        lambda _self, *, executor=None: snapshot_command,
    )

    result = CliRunner().invoke(cli, ["resource", "list", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = result.exception or None
    assert payload is None
    import json

    resources = json.loads(result.stdout)["result"]["resources"]
    kinds = {item["type"] for item in resources}
    assert {"project", "database", "volume", "log", "filestore"} <= kinds
    assert (
        next(item for item in resources if item["type"] == "database")["completeness"] == "complete"
    )
    volume = next(item for item in resources if item["type"] == "volume")
    if snapshot_available:
        assert volume["completeness"] == "complete", volume
        assert volume["measured_bytes"] == 4096
        assert volume["stable_identity"].startswith("volume:")
        project = next(item for item in resources if item["type"] == "project")
        assert volume["relationships"] == [project["stable_identity"]]
    else:
        assert volume["completeness"] == "partial"
        assert volume["completeness_reason"] == "volume probe unavailable"
    assert (
        next(item for item in resources if item["type"] == "log")["ownership_confidence"]
        == "proven"
    )
    assert (
        next(item for item in resources if item["type"] == "filestore")["ownership_confidence"]
        == "unknown"
    )
