"""Opt-in disposable PostgreSQL E2E coverage for the guarded database drop."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import PostgresClusterStartError
from odoo_instance_sdk.internal.pg.drop import build_database_drop_command
from odoo_instance_sdk.internal.postgres_compose import compose_volume_name, docker_ready
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.integration.postgres_cleanup import cleanup_postgres_project, patch_postgres_image_trust

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


def _terminate_waiter(waiter: subprocess.Popen[str]) -> None:
    """Bound the disposable psql teardown, escalating past SIGTERM."""
    if waiter.poll() is None:
        waiter.terminate()
        try:
            waiter.wait(timeout=10)
        except subprocess.TimeoutExpired:
            waiter.kill()
            waiter.wait(timeout=10)


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


def _assert_present(psql: str, *, port: int, password: str, database: str) -> None:
    result = _psql(
        psql,
        port=port,
        password=password,
        database="postgres",
        sql=f"SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname='{database}')",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().lower() in {"t", "true", "1"}


def _volume_is_present(volume_name: str) -> bool:
    return (
        subprocess.run(
            ["docker", "volume", "inspect", volume_name],
            capture_output=True,
            check=False,
            text=True,
        ).returncode
        == 0
    )


@pytest.mark.skipif(
    os.environ.get("ODCLI_RUN_DISPOSABLE_DB_DROP_E2E") != "1",
    reason="set ODCLI_RUN_DISPOSABLE_DB_DROP_E2E=1 for the disposable PostgreSQL E2E",
)
@pytest.mark.serial
@pytest.mark.timeout(240)
def test_disposable_database_drop_success_and_forced_session(  # noqa: C901
    tmp_path: Path,
    docker_visible_postgres_root: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.backup_restore_parts.backup._paths.get_project_postgres_dir",
        lambda project_id: docker_visible_postgres_root / str(project_id) / "postgres",
    )
    cluster = PostgresCluster.from_project(tmp_path)
    volume_name = compose_volume_name(cluster._project_id)
    compose_file = cluster.compose_file
    primary_failure: BaseException | None = None
    waiter: subprocess.Popen[str] | None = None
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.backup_restore_parts.backup._paths.get_catalog_path",
        lambda **_kwargs: catalog.db_path,
    )
    try:
        try:
            digest = cluster.resolve_image_digest(timeout=120.0)
            cluster.approve_image(digest, timeout=30.0)
            cluster.ensure_running(timeout=120.0)
        except PostgresClusterStartError as exc:
            pytest.skip(f"disposable PostgreSQL cluster setup blocked: {exc}")

        claim = catalog._ensure_postgres_cluster_pending(
            cluster._project_id,
            cluster.compose_project_name,
            compose_volume_name(cluster._project_id),
        )
        catalog._activate_postgres_cluster(
            claim.cluster_id,
            cluster._project_id,
            cluster.compose_project_name,
            compose_volume_name(cluster._project_id),
        )
        source_backup = tmp_path / "source-backup.zip"
        source_backup.write_bytes(b"disposable source backup")
        backup_id = "00000000-0000-0000-0000-000000000099"
        catalog.start_download(
            backup_id,
            "http://127.0.0.1:8069",
            "odcli_drop_default",
            "zip",
            False,
            source_backup,
        )
        catalog.success_download(
            backup_id,
            source_backup.name,
            source_backup.stat().st_size,
            "disposable",
        )
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
            catalog.record_restore(
                cluster.endpoint_host,
                cluster.endpoint_port,
                name,
                backup_id,
                cluster_id=claim.cluster_id,
            )

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
            (forced_name, "restored"),
            (forced_name, "dropped"),
            (success_name, "restored"),
            (success_name, "dropped"),
        ]
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        cleanup_failures: list[BaseException] = []
        if waiter is not None and waiter.poll() is None:
            try:
                _terminate_waiter(waiter)
            except BaseException as exc:
                cleanup_failures.append(exc)
        try:
            catalog.close()
        except BaseException as exc:
            cleanup_failures.append(exc)
        try:
            cleanup_postgres_project(
                compose_file=compose_file,
                compose_project_name=cluster.compose_project_name,
                volume_name=volume_name,
                primary_failure=None,
            )
        except BaseException as exc:
            cleanup_failures.append(exc)
        if primary_failure is not None and cleanup_failures:
            raise BaseExceptionGroup(
                "primary test failure and PostgreSQL cleanup failures",
                [primary_failure, *cleanup_failures],
            )
        if primary_failure is not None:
            raise primary_failure
        if cleanup_failures:
            raise BaseExceptionGroup("PostgreSQL cleanup failures", cleanup_failures)


@pytest.mark.skipif(
    os.environ.get("ODCLI_RUN_BOOTSTRAP_CLEANUP_E2E") != "1",
    reason="set ODCLI_RUN_BOOTSTRAP_CLEANUP_E2E=1 for the bootstrap cleanup public-boundary E2E",
)
@pytest.mark.serial
@pytest.mark.timeout(240)
def test_disposable_bootstrap_cleanup_public_cli_boundary(
    tmp_path: Path,
    docker_visible_postgres_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise normal bootstrap publication and public ``odcli db rm`` safety."""
    ready, diagnostic = docker_ready(timeout=3.0)
    if not ready:
        pytest.skip(f"docker unavailable for bootstrap cleanup E2E: {diagnostic}")
    psql = shutil.which("psql")
    if psql is None:
        pytest.skip("psql unavailable for bootstrap cleanup E2E")
    odoo_bin = os.environ.get("ODCLI_BOOTSTRAP_ODOO_BIN")
    if not odoo_bin or not Path(odoo_bin).is_file():
        pytest.skip("set ODCLI_BOOTSTRAP_ODOO_BIN to a runnable Odoo binary for bootstrap E2E")

    port = _free_port()
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.backup_restore_parts.backup._paths.get_project_postgres_dir",
        lambda project_id: docker_visible_postgres_root / str(project_id) / "postgres",
    )
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda **_kwargs: catalog.db_path,
    )
    patch_postgres_image_trust(monkeypatch)
    init_args = [
        "init",
        "--no-input",
        "--allow-partial",
        "--local-config",
        "--odoo-bin",
        odoo_bin,
        "--python",
        os.environ.get("ODCLI_BOOTSTRAP_PYTHON", sys.executable),
        "--project",
        str(tmp_path),
        "--postgres",
        "compose",
        "--postgres-image",
        "postgres:16-alpine",
        "--postgres-port",
        str(port),
        "--postgres-user",
        "odoo",
    ]
    primary_failure: BaseException | None = None
    waiter: subprocess.Popen[str] | None = None
    cluster: PostgresCluster | None = None
    try:
        init_result = CliRunner().invoke(cli, init_args)
        if init_result.exit_code != 0:
            pytest.skip(f"normal bootstrap setup blocked: {init_result.output}")
        cluster = PostgresCluster.from_project(tmp_path)
        volume_name = compose_volume_name(cluster._project_id)
        latest = catalog._latest_database_event(cluster.endpoint_host, cluster.endpoint_port, "tmp")
        assert latest is not None and latest["event_type"] == "bootstrapped"
        claim = catalog._get_postgres_cluster(cluster._project_id)
        assert claim is not None and claim.state == "active"
        password = cluster.password_file.read_text(encoding="utf-8").strip()
        client = OdooClient(config=OdooClientConfig(executable="true"), _catalog=catalog)
        instance = OdooInstance(
            config=InstanceConfig(
                base_url="http://127.0.0.1:8069",
                configured_database_names=("tmp",),
                db_host=cluster.endpoint_host,
                db_port=cluster.endpoint_port,
                db_user="odoo",
                db_password=password,
                default_cwd=tmp_path,
            ),
            _client=client,
            _postgres_cluster=cluster,
        )
        data_directory = Path(str(latest["data_directory"]))
        unrelated = data_directory / "filestore" / "unrelated"
        unrelated.mkdir(parents=True)
        (unrelated / "keep").write_text("keep", encoding="utf-8")

        with monkeypatch.context() as public_cli:
            public_cli.setattr(
                "odoo_instance_sdk.commands.pg._database_instance",
                lambda _ctx: (None, instance),
            )
            result = CliRunner().invoke(
                cli,
                [
                    "--project",
                    str(tmp_path),
                    "db",
                    "rm",
                    "tmp",
                    "--force-default",
                    "--yes",
                    "--format",
                    "json",
                ],
            )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["result"]["terminated_sessions"] == 0
        _assert_absent(psql, port=port, password=password, database="tmp")
        assert _volume_is_present(volume_name)
        assert (unrelated / "keep").read_text(encoding="utf-8") == "keep"

        second_init = CliRunner().invoke(cli, init_args)
        assert second_init.exit_code == 0, second_init.output
        latest = catalog._latest_database_event(cluster.endpoint_host, cluster.endpoint_port, "tmp")
        assert latest is not None and latest["event_type"] == "bootstrapped"
        event_count = catalog._conn.execute(
            "SELECT COUNT(*) FROM database_events WHERE database_name='tmp'"
        ).fetchone()[0]
        waiter = _session_process(psql, port=port, password=password, database="tmp")
        time.sleep(0.5)
        assert waiter.poll() is None
        catalog._conn.execute(
            "UPDATE postgres_clusters SET volume_name='foreign-volume' WHERE cluster_id=?",
            (str(claim.cluster_id),),
        )
        catalog._conn.commit()
        with monkeypatch.context() as public_cli:
            public_cli.setattr(
                "odoo_instance_sdk.commands.pg._database_instance",
                lambda _ctx: (None, instance),
            )
            refused = CliRunner().invoke(
                cli,
                [
                    "--project",
                    str(tmp_path),
                    "db",
                    "rm",
                    "tmp",
                    "--force-default",
                    "--yes",
                    "--format",
                    "json",
                ],
            )
        assert refused.exit_code != 0
        assert waiter.poll() is None
        _assert_present(psql, port=port, password=password, database="tmp")
        assert _volume_is_present(volume_name)
        latest = catalog._latest_database_event(cluster.endpoint_host, cluster.endpoint_port, "tmp")
        assert latest is not None and latest["event_type"] == "bootstrapped"
        assert (
            catalog._conn.execute(
                "SELECT COUNT(*) FROM database_events WHERE database_name='tmp'"
            ).fetchone()[0]
            == event_count
        )
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        if waiter is not None:
            _terminate_waiter(waiter)
        try:
            catalog.close()
        finally:
            if cluster is not None:
                volume_name = compose_volume_name(cluster._project_id)
                cleanup_postgres_project(
                    compose_file=cluster.compose_file,
                    compose_project_name=cluster.compose_project_name,
                    volume_name=volume_name,
                    primary_failure=primary_failure,
                )
