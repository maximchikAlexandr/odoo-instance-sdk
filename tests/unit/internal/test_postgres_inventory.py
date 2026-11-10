from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.pg.inventory import (
    _INVENTORY_SQL,
    build_database_inventory_command,
)
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.integration.real_odoo.inventory_diagnostics import inventory_diagnostic


@pytest.mark.unit
@pytest.mark.parametrize(
    ("returncode", "stderr", "expected_category", "expected_sqlstate"),
    [
        (
            2,
            "FATAL: password authentication failed for user <secret> SQLSTATE 28P01",
            "auth",
            "28P01",
        ),
        (2, "connection refused SQLSTATE 08006", "connection", "08006"),
        (2, "ERROR: syntax error SQLSTATE 42601", "sql", "42601"),
        (2, "ERROR: statement timeout SQLSTATE 57014", "timeout", "57014"),
        (127, "psql: exec format error", "process", None),
        (2, "unclassified failure SQLSTATE ABCDE", "unknown", None),
        (999, "unclassified failure", "unknown", None),
    ],
)
def test_inventory_diagnostics_allowlist_categories_without_retaining_secrets(
    returncode: int,
    stderr: str,
    expected_category: str,
    expected_sqlstate: str | None,
) -> None:
    secret = "inventory-diagnostic-secret-canary"
    result = ProcessResult(
        argv=("psql", secret),
        returncode=returncode,
        stdout=secret,
        stderr=stderr.replace("<secret>", secret) + f" {secret}",
        duration=0.0,
        cwd=secret,
        environment=(("PGPASSWORD", secret),),
    )

    diagnostic = inventory_diagnostic(result)

    assert diagnostic == {
        "exit_code": returncode if -128 <= returncode <= 255 else None,
        "category": expected_category,
        "sqlstate": expected_sqlstate,
    }
    assert secret not in json.dumps(diagnostic)


@pytest.mark.unit
def test_inventory_diagnostic_marks_absent_and_unparsed_result_unknown() -> None:
    assert inventory_diagnostic(None) == {
        "exit_code": None,
        "category": "unknown",
        "sqlstate": None,
    }
    assert inventory_diagnostic(None, error=ValueError("private diagnostic text")) == {
        "exit_code": None,
        "category": "unknown",
        "sqlstate": None,
    }
    assert inventory_diagnostic(
        None, error=subprocess.TimeoutExpired("private argv", timeout=3)
    ) == {"exit_code": None, "category": "timeout", "sqlstate": None}


def _instance(
    project: Path, catalog: object, *, configured_database_names: tuple[str, ...] = ("feature_db",)
) -> OdooInstance:
    client = MagicMock(spec=OdooClient)
    client.config = OdooClientConfig(executable="odoo")
    client.get_catalog.return_value = catalog
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            configured_database_names=configured_database_names,
            db_host="127.0.0.1",
            db_port=5432,
            db_user="odoo",
            db_password="private-password",
        ),
        _client=client,
    )
    instance._postgres_cluster = PostgresCluster.from_project(project)
    return instance


def _executor(payload: list[dict[str, object]]) -> RecordingExecutor:
    encoded = json.dumps(payload)

    def result_factory(step: object) -> ProcessResult:
        return ProcessResult(
            argv=step.argv,  # type: ignore[attr-defined]
            returncode=0,
            stdout=encoded,
            stderr="",
            duration=0.0,
            cwd=step.cwd,  # type: ignore[attr-defined]
            environment=step.environment,  # type: ignore[attr-defined]
        )

    return RecordingExecutor(result_factory=result_factory)


@pytest.mark.unit
def test_inventory_is_direct_cluster_read_and_tracks_only_provenance(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    instance = _instance(project_manifest, catalog)
    executor = _executor(
        [
            {"name": "zeta", "logical_size_bytes": 20, "active_sessions": 2},
            {"name": "feature_db", "logical_size_bytes": 10, "active_sessions": 0},
        ]
    )
    before_changes = catalog._conn.total_changes

    result = build_database_inventory_command(instance, project_manifest, executor=executor).run()
    tracked = build_database_inventory_command(
        instance,
        project_manifest,
        tracked=True,
        executor=_executor(
            [
                {"name": "zeta", "logical_size_bytes": 20, "active_sessions": 2},
                {"name": "feature_db", "logical_size_bytes": 10, "active_sessions": 0},
            ]
        ),
    ).run()

    assert [item.name for item in result.databases] == ["feature_db", "zeta"]
    assert result.databases[0].is_default is False
    assert result.databases[0].origin == "unknown"
    assert tracked.databases == ()
    assert [step.step_id for step in executor.executed] == ["database.list.inventory"]
    assert catalog._conn.total_changes == before_changes
    catalog.close()


@pytest.mark.unit
def test_inventory_serializes_database_oid_as_a_json_number(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    instance = _instance(project_manifest, catalog)
    executor = _executor(
        [
            {
                "name": "feature_db",
                "database_oid": 16384,
                "logical_size_bytes": 10,
                "active_sessions": 0,
            }
        ]
    )

    result = build_database_inventory_command(instance, project_manifest, executor=executor).run()

    assert result.databases[0].database_oid == 16384
    assert "'database_oid', d.oid::bigint" in _INVENTORY_SQL
    catalog.close()


@pytest.mark.unit
def test_inventory_rejects_malformed_transport_rows_without_catalogue_reconciliation(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    catalog = MagicMock()
    instance = _instance(project_manifest, catalog)
    command = build_database_inventory_command(
        instance,
        project_manifest,
        executor=_executor([{"name": "bad", "logical_size_bytes": -1, "active_sessions": 0}]),
    )

    with pytest.raises(ConfigError, match="invalid logical size"):
        command.run()
    catalog.record_database_dropped.assert_not_called()


@pytest.mark.unit
def test_inventory_works_when_odoo_is_stopped_or_dbfilter_hides_databases(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    instance = _instance(project_manifest, catalog, configured_database_names=())
    result = build_database_inventory_command(
        instance,
        project_manifest,
        executor=_executor(
            [{"name": "hidden-from-odoo", "logical_size_bytes": 7, "active_sessions": 0}]
        ),
    ).run()

    assert [item.name for item in result.databases] == ["hidden-from-odoo"]
    catalog.close()


@pytest.mark.unit
def test_inventory_unavailable_postgres_is_a_non_reconciling_failure(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    instance = _instance(project_manifest, catalog)

    def failed_result(step: object) -> ProcessResult:
        return ProcessResult(
            argv=step.argv,  # type: ignore[attr-defined]
            returncode=1,
            stdout="",
            stderr="connection refused",
            duration=0.0,
            cwd=step.cwd,  # type: ignore[attr-defined]
            environment=step.environment,  # type: ignore[attr-defined]
        )

    before_changes = catalog._conn.total_changes
    with pytest.raises(ConfigError, match="query failed"):
        build_database_inventory_command(
            instance, project_manifest, executor=RecordingExecutor(result_factory=failed_result)
        ).run()
    assert catalog._conn.total_changes == before_changes
    catalog.close()


@pytest.mark.unit
def test_inventory_does_not_merge_foreign_cluster_restore_relationships(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    foreign = catalog._ensure_postgres_cluster_pending(
        "foreign-project", "foreign-compose", "foreign-volume"
    )
    foreign = catalog._activate_postgres_cluster(
        foreign.cluster_id, "foreign-project", "foreign-compose", "foreign-volume"
    )
    backup_id = str(uuid.uuid4())
    backup_file = tmp_path / "foreign.zip"
    backup_file.write_bytes(b"foreign")
    catalog.start_download(
        backup_id, "http://127.0.0.1:8069", "shared-name", "zip", True, backup_file
    )
    catalog.success_download(backup_id, backup_file.name, 7, "")
    catalog.record_restore(
        "127.0.0.1", 5432, "shared-name", backup_id, cluster_id=foreign.cluster_id
    )
    instance = _instance(project_manifest, catalog)
    result = build_database_inventory_command(
        instance,
        project_manifest,
        executor=_executor(
            [{"name": "shared-name", "logical_size_bytes": 1, "active_sessions": 0}]
        ),
    ).run()

    assert result.databases[0].origin == "unknown"
    assert result.databases[0].restore_backup_ids == ()
    catalog.close()


@pytest.mark.unit
def test_inventory_projects_incomplete_restore_state_without_marking_success(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[postgres]\nmode = "compose"\nimage = "postgres:16"\nport = 5432\n',
        encoding="utf-8",
    )
    instance = _instance(project_manifest, catalog)
    cluster = instance._postgres_cluster
    assert cluster is not None
    volume = f"pgdata_{cluster._project_id}"
    claim = catalog._ensure_postgres_cluster_pending(
        cluster._project_id, cluster.compose_project_name, volume
    )
    claim = catalog._activate_postgres_cluster(
        claim.cluster_id, cluster._project_id, cluster.compose_project_name, volume
    )
    backup_file = tmp_path / "incomplete.zip"
    backup_file.write_bytes(b"incomplete")
    backup_id = str(uuid.uuid4())
    catalog.start_download(
        backup_id, "http://127.0.0.1:8069", "feature_db", "zip", True, backup_file
    )
    catalog.success_download(backup_id, backup_file.name, backup_file.stat().st_size, "")
    catalog.record_restore(
        cluster.endpoint_host,
        cluster.endpoint_port,
        "feature_db",
        backup_id,
        cluster_id=claim.cluster_id,
        state="incomplete",
    )

    result = build_database_inventory_command(
        instance,
        project_manifest,
        tracked=True,
        executor=_executor([{"name": "feature_db", "logical_size_bytes": 1, "active_sessions": 0}]),
    ).run()

    assert len(result.databases) == 1
    assert result.databases[0].origin == "restore"
    assert result.databases[0].restore_state == "incomplete"
    catalog.close()
