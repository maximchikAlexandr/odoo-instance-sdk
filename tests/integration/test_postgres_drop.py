"""Opt-in disposable PostgreSQL E2E coverage for the guarded database drop."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import PostgresClusterStartError
from odoo_instance_sdk.internal.pg.drop import build_database_drop_command
from odoo_instance_sdk.internal.postgres_compose import docker_ready
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _psql(
    psql: str,
    *,
    port: int,
    password: str,
    database: str,
    sql: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    env.pop("PGOPTIONS", None)
    return subprocess.run(
        [
            psql,
            "-X",
            "-q",
            "-t",
            "-A",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            "odoo",
            "-d",
            database,
            "-c",
            sql,
        ],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )


def _session_process(
    psql: str, *, port: int, password: str, database: str
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    env.pop("PGOPTIONS", None)
    return subprocess.Popen(
        [
            psql,
            "-X",
            "-q",
            "-t",
            "-A",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            "odoo",
            "-d",
            database,
            "-c",
            "SELECT pg_sleep(120)",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _assert_absent(psql: str, *, port: int, password: str, database: str) -> None:
    result = _psql(
        psql,
        port=port,
        password=password,
        database="postgres",
        sql=f"SELECT NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='{database}')",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().lower() in {"t", "true", "1"}


@pytest.mark.skipif(
    os.environ.get("ODCLI_RUN_DISPOSABLE_DB_DROP_E2E") != "1",
    reason="set ODCLI_RUN_DISPOSABLE_DB_DROP_E2E=1 for the disposable PostgreSQL E2E",
)
@pytest.mark.serial
@pytest.mark.timeout(240)
def test_disposable_database_drop_success_and_forced_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready, diagnostic = docker_ready(timeout=3.0)
    if not ready:
        pytest.skip(f"docker unavailable for disposable PostgreSQL E2E: {diagnostic}")
    psql = shutil.which("psql")
    if psql is None:
        pytest.skip("psql unavailable for disposable PostgreSQL E2E")

    port = _free_port()
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        "[project]\n"
        'default_source_database = "odcli_drop_default"\n\n'
        "[postgres]\n"
        'mode = "compose"\n'
        'image = "postgres:16-alpine"\n'
        f"port = {port}\n"
        'user = "odoo"\n',
        encoding="utf-8",
    )
    runtime_root = Path(tempfile.mkdtemp(prefix=".odcli-db-drop-e2e-", dir=Path.cwd()))
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.get_project_postgres_dir",
        lambda _project_id: runtime_root,
    )
    cluster = PostgresCluster.from_project(tmp_path)
    waiter: subprocess.Popen[str] | None = None
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        try:
            digest = cluster.resolve_image_digest(timeout=120.0)
            cluster.approve_image(digest, timeout=30.0)
            cluster.ensure_running(timeout=120.0)
        except PostgresClusterStartError as exc:
            pytest.skip(f"disposable PostgreSQL cluster setup blocked: {exc}")

        password = cluster.password_file.read_text(encoding="utf-8").strip()
        client = OdooClient(config=OdooClientConfig(executable="true"), _catalog=catalog)
        instance = OdooInstance(
            config=InstanceConfig(
                base_url="http://127.0.0.1:8069",
                configured_database_names=("odcli_drop_default",),
                db_host=cluster.endpoint_host,
                db_port=cluster.endpoint_port,
                db_user="odoo",
                db_password=password,
                default_cwd=tmp_path,
            ),
            _client=client,
            _postgres_cluster=cluster,
        )

        success_name = "odcli_drop_success"
        forced_name = "odcli_drop_forced"
        for name in (success_name, forced_name):
            created = _psql(
                psql,
                port=port,
                password=password,
                database="postgres",
                sql=f'CREATE DATABASE "{name}"',
            )
            assert created.returncode == 0, created.stderr

        build_database_drop_command(instance, tmp_path, success_name).run()
        _assert_absent(psql, port=port, password=password, database=success_name)

        waiter = _session_process(psql, port=port, password=password, database=forced_name)
        time.sleep(0.5)
        build_database_drop_command(
            instance,
            tmp_path,
            forced_name,
            force_connections=True,
        ).run()
        _assert_absent(psql, port=port, password=password, database=forced_name)

        rows = catalog._conn.execute(
            "SELECT database_name, event_type FROM database_events "
            "WHERE db_port=? ORDER BY database_name",
            (port,),
        ).fetchall()
        assert [(row["database_name"], row["event_type"]) for row in rows] == [
            (forced_name, "dropped"),
            (success_name, "dropped"),
        ]
    finally:
        if waiter is not None and waiter.poll() is None:
            waiter.terminate()
            waiter.wait(timeout=10)
        catalog.close()
        try:
            if cluster.compose_file.is_file():
                cleanup = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "--project-name",
                        cluster.compose_project_name,
                        "-f",
                        str(cluster.compose_file),
                        "down",
                        "--volumes",
                        "--remove-orphans",
                    ],
                    cwd=cluster.compose_file.parent,
                    capture_output=True,
                    check=False,
                    timeout=60.0,
                    text=True,
                )
                assert cleanup.returncode == 0, cleanup.stderr
        finally:
            shutil.rmtree(runtime_root)
