from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.postgres_compose import compose_volume_name
from odoo_instance_sdk.internal.project_init import project_owned_data_dir
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _inspection(*, exists: bool = True) -> str:
    return json.dumps({"exists": exists, "is_template": False, "sessions": []})


def _executor(*, exists: bool = True) -> object:
    from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor

    initial = _inspection(exists=exists)
    checked = _inspection(exists=exists)

    def result_factory(step: object) -> ProcessResult:
        step_id = step.step_id  # type: ignore[attr-defined]
        if step_id in {"database.drop.planning-inspect", "database.drop.inspect"}:
            return ProcessResult(
                argv=step.argv,  # type: ignore[attr-defined]
                returncode=0,
                stdout=initial,
                stderr="",
                duration=0.0,
                cwd=step.cwd,  # type: ignore[attr-defined]
                environment=step.environment,  # type: ignore[attr-defined]
            )
        if step_id.endswith("revalidate-terminate"):
            return ProcessResult(
                argv=step.argv,  # type: ignore[attr-defined]
                returncode=0,
                stdout=initial,
                stderr="",
                duration=0.0,
                cwd=step.cwd,  # type: ignore[attr-defined]
                environment=step.environment,  # type: ignore[attr-defined]
            )
        if step_id.endswith("revalidate-drop"):
            return ProcessResult(
                argv=step.argv,  # type: ignore[attr-defined]
                returncode=0,
                stdout=checked,
                stderr="",
                duration=0.0,
                cwd=step.cwd,  # type: ignore[attr-defined]
                environment=step.environment,  # type: ignore[attr-defined]
            )
        if step_id.endswith("verify"):
            return ProcessResult(
                argv=step.argv,  # type: ignore[attr-defined]
                returncode=0,
                stdout="t\n",
                stderr="",
                duration=0.0,
                cwd=step.cwd,  # type: ignore[attr-defined]
                environment=step.environment,  # type: ignore[attr-defined]
            )
        if step_id.endswith("terminate"):
            return ProcessResult(
                argv=step.argv,  # type: ignore[attr-defined]
                returncode=0,
                stdout="1\n",
                stderr="",
                duration=0.0,
                cwd=step.cwd,  # type: ignore[attr-defined]
                environment=step.environment,  # type: ignore[attr-defined]
            )
        if step_id.endswith("execute"):
            return ProcessResult(
                argv=step.argv,  # type: ignore[attr-defined]
                returncode=0,
                stdout="",
                stderr="",
                duration=0.0,
                cwd=step.cwd,  # type: ignore[attr-defined]
                environment=step.environment,  # type: ignore[attr-defined]
            )
        return ProcessResult(
            argv=step.argv,  # type: ignore[attr-defined]
            returncode=0,
            stdout="",
            stderr="",
            duration=0.0,
            cwd=step.cwd,  # type: ignore[attr-defined]
            environment=step.environment,  # type: ignore[attr-defined]
        )

    return RecordingExecutor(result_factory=result_factory)


@pytest.mark.unit
def test_db_rm_deletes_proven_filestore_after_self_contained_restore(
    project_manifest: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public ``db rm`` removes filestore provenance after init and restore binding."""
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    monkeypatch.setattr(PostgresCluster, "_inspect_cluster_volume", lambda *_a, **_k: True)
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[postgres]\nmode = "compose"\nimage = "postgres:16"\nport = 5432\n',
        encoding="utf-8",
    )
    data_dir = project_owned_data_dir(project_manifest)
    data_dir.mkdir(parents=True, exist_ok=True)
    filestore = data_dir / "filestore" / "restored_db"
    filestore.mkdir(parents=True)
    (filestore / "blob").write_bytes(b"payload")
    catalog_path = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=catalog_path)
    cluster = PostgresCluster.from_project(project_manifest)
    claim = catalog._ensure_postgres_cluster_pending(
        cluster._project_id,
        cluster.compose_project_name,
        compose_volume_name(cluster._project_id),
    )
    active = catalog._activate_postgres_cluster(
        claim.cluster_id,
        cluster._project_id,
        cluster.compose_project_name,
        compose_volume_name(cluster._project_id),
    )
    backup_id = str(uuid.uuid4())
    backup_file = tmp_path / "backup.zip"
    backup_file.write_bytes(b"source-backup")
    catalog.start_download(
        backup_id,
        "http://127.0.0.1:8069",
        "restored_db",
        "zip",
        True,
        backup_file,
    )
    catalog.success_download(backup_id, backup_file.name, backup_file.stat().st_size, "")
    catalog.record_restore(
        cluster.endpoint_host,
        cluster.endpoint_port,
        "restored_db",
        backup_id,
        cluster_id=active.cluster_id,
        data_directory=data_dir,
    )

    from odoo_instance_sdk import OdooClient, OdooClientConfig
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.resources.instance import OdooInstance

    client = MagicMock(spec=OdooClient)
    client.config = OdooClientConfig(executable="odoo")
    client.get_catalog.return_value = catalog
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            configured_database_names=("restored_db",),
            db_host="127.0.0.1",
            db_port=5432,
            db_user="odoo",
            db_password="private-password",
        ),
        _client=client,
    )
    instance._postgres_cluster = cluster
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_instance",
        lambda _ctx: (None, instance),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path",
        lambda _ctx: project_manifest,
    )
    from odoo_instance_sdk.internal.pg.drop import build_database_drop_command as real_drop_command

    def build_drop_command(*args: object, **kwargs: object) -> object:
        patched = dict(kwargs)
        patched["executor"] = _executor()
        return real_drop_command(*args, **patched)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.drop.build_database_drop_command",
        build_drop_command,
    )

    result = CliRunner().invoke(
        cli,
        [
            "--project",
            str(project_manifest),
            "db",
            "rm",
            "restored_db",
            "--yes",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result"]["filestore_state"] == "deleted"
    assert not filestore.exists()
    catalog.close()


def test_db_rm_unknown_when_data_directory_missing(tmp_path: Path) -> None:
    from odoo_instance_sdk.internal.pg.drop import _cleanup_proven_filestore

    state, path = _cleanup_proven_filestore(None, "restored_db")
    assert state == "unknown"
    assert path is None
