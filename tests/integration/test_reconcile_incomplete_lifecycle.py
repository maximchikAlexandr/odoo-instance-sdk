from __future__ import annotations

import json
from pathlib import Path

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

pytestmark = pytest.mark.integration


def _inventory_executor(rows: list[dict[str, object]]) -> RecordingExecutor:
    encoded = json.dumps(rows)

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


def test_catalogue_restore_state_reaches_public_inventory_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Join catalogue evidence, PostgreSQL inventory, and SDK output projection."""
    project = tmp_path
    (project / ".odcli").mkdir()
    (project / ".odcli" / "project.toml").write_text(
        '[project]\ndefault_source_database = "staging"\n\n'
        '[postgres]\nmode = "compose"\nimage = "postgres:16"\nport = 5432\n',
        encoding="utf-8",
    )
    catalog = BackupCatalog(db_path=project / "catalog.sqlite3")
    client = OdooClient(config=OdooClientConfig(executable="odoo"), _catalog=catalog)
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(config_path=str(project / "odoo.conf"), http_port=8069),
            configured_database_names=("staging",),
            db_host="127.0.0.1",
            db_port=5432,
            db_user="odoo",
            db_password="not-persisted",
        ),
        _client=client,
    )
    cluster = PostgresCluster.from_project(project)
    instance._postgres_cluster = cluster
    claim = catalog._ensure_postgres_cluster_pending(
        cluster._project_id,
        cluster.compose_project_name,
        f"pgdata_{cluster._project_id}",
    )
    claim = catalog._activate_postgres_cluster(
        claim.cluster_id,
        cluster._project_id,
        cluster.compose_project_name,
        f"pgdata_{cluster._project_id}",
    )
    archive = project / "staging.zip"
    archive.write_bytes(b"archive")
    backup_id = "00000000-0000-0000-0000-000000000350"
    catalog.start_download(
        backup_id,
        "https://backup.example",
        "staging",
        "zip",
        False,
        archive,
    )
    catalog.success_download(backup_id, archive.name, archive.stat().st_size, "sha256")
    catalog.record_restore(
        cluster.endpoint_host,
        cluster.endpoint_port,
        "staging",
        backup_id,
        cluster_id=claim.cluster_id,
        state="incomplete",
    )

    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    result = instance.databases.list_inventory_command(
        project,
        tracked=True,
        executor=_inventory_executor(
            [{"name": "staging", "logical_size_bytes": 1, "active_sessions": 0}]
        ),
    ).run()

    assert len(result.databases) == 1
    database = result.databases[0]
    assert database.name == "staging"
    assert database.origin == "restore"
    assert database.restore_state == "incomplete"
    catalog.close()
