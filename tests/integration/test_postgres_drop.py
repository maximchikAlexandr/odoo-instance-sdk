"""Opt-in disposable PostgreSQL E2E coverage for the guarded database drop."""

from __future__ import annotations

import json
import os
import select
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import PostgresClusterStartError
from odoo_instance_sdk.internal.pg.drop import build_database_drop_command
from odoo_instance_sdk.internal.postgres_compose import compose_volume_name, docker_ready
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from odoo_instance_sdk.storage.catalog.helpers import PostgresClusterClaim
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
            "SELECT pg_backend_pid(); SELECT pg_sleep(120)",
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


def _wait_for_session_ready(waiter: subprocess.Popen[str]) -> None:
    """Wait for psql to report its backend instead of sleeping heuristically."""
    if waiter.stdout is None:
        raise AssertionError("session waiter did not expose stdout")
    ready, _, _ = select.select([waiter.stdout], [], [], 10.0)
    assert ready, "session waiter did not report a backend in time"
    assert waiter.stdout.readline().strip().isdigit()


@dataclass(frozen=True, slots=True)
class _DisposableDropSetup:
    project_root: Path
    psql: str
    port: int
    password: str
    cluster: PostgresCluster
    catalog: BackupCatalog
    claim: PostgresClusterClaim
    instance: OdooInstance
    compose_file: Path
    volume_name: str
    backup_id: str


@dataclass(frozen=True, slots=True)
class _BootstrapCleanupSetup:
    project_root: Path
    psql: str
    port: int
    password: str
    cluster: PostgresCluster
    catalog: BackupCatalog
    claim: PostgresClusterClaim
    instance: OdooInstance
    init_args: tuple[str, ...]
    volume_name: str


@pytest.fixture
def disposable_drop_setup(
    tmp_path: Path,
    docker_visible_postgres_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_DisposableDropSetup]:
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
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.backup_restore_parts.backup._paths.get_catalog_path",
        lambda **_kwargs: catalog.db_path,
    )
    compose_file = cluster.compose_file
    primary_failure: BaseException | None = None
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
            volume_name,
        )
        claim = catalog._activate_postgres_cluster(
            claim.cluster_id,
            cluster._project_id,
            cluster.compose_project_name,
            volume_name,
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
        yield _DisposableDropSetup(
            project_root=tmp_path,
            psql=psql,
            port=port,
            password=password,
            cluster=cluster,
            catalog=catalog,
            claim=claim,
            instance=instance,
            compose_file=compose_file,
            volume_name=volume_name,
            backup_id=backup_id,
        )
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        try:
            catalog.close()
        finally:
            cleanup_postgres_project(
                compose_file=compose_file,
                compose_project_name=cluster.compose_project_name,
                volume_name=volume_name,
                primary_failure=primary_failure,
            )


def _create_restored_database(setup: _DisposableDropSetup, name: str) -> None:
    created = _psql(
        setup.psql,
        port=setup.port,
        password=setup.password,
        database="postgres",
        sql=f'CREATE DATABASE "{name}"',
    )
    assert created.returncode == 0, created.stderr
    setup.catalog.record_restore(
        setup.cluster.endpoint_host,
        setup.cluster.endpoint_port,
        name,
        setup.backup_id,
        cluster_id=setup.claim.cluster_id,
    )


@pytest.mark.skipif(
    os.environ.get("ODCLI_RUN_DISPOSABLE_DB_DROP_E2E") != "1",
    reason="set ODCLI_RUN_DISPOSABLE_DB_DROP_E2E=1 for the disposable PostgreSQL E2E",
)
@pytest.mark.serial
@pytest.mark.timeout(240)
def test_disposable_database_drop_success(disposable_drop_setup: _DisposableDropSetup) -> None:
    name = "odcli_drop_success"
    _create_restored_database(disposable_drop_setup, name)
    build_database_drop_command(
        disposable_drop_setup.instance, disposable_drop_setup.project_root, name
    ).run()
    _assert_absent(
        disposable_drop_setup.psql,
        port=disposable_drop_setup.port,
        password=disposable_drop_setup.password,
        database=name,
    )
    rows = disposable_drop_setup.catalog._conn.execute(
        "SELECT database_name, event_type FROM database_events "
        "WHERE db_port=? ORDER BY database_name",
        (disposable_drop_setup.port,),
    ).fetchall()
    assert [(row["database_name"], row["event_type"]) for row in rows] == [
        (name, "restored"),
        (name, "dropped"),
    ]


@pytest.mark.skipif(
    os.environ.get("ODCLI_RUN_DISPOSABLE_DB_DROP_E2E") != "1",
    reason="set ODCLI_RUN_DISPOSABLE_DB_DROP_E2E=1 for the disposable PostgreSQL E2E",
)
@pytest.mark.serial
@pytest.mark.timeout(240)
def test_disposable_database_drop_forced_session(
    disposable_drop_setup: _DisposableDropSetup,
) -> None:
    name = "odcli_drop_forced"
    _create_restored_database(disposable_drop_setup, name)
    waiter = _session_process(
        disposable_drop_setup.psql,
        port=disposable_drop_setup.port,
        password=disposable_drop_setup.password,
        database=name,
    )
    try:
        _wait_for_session_ready(waiter)
        build_database_drop_command(
            disposable_drop_setup.instance,
            disposable_drop_setup.project_root,
            name,
            force_connections=True,
        ).run()
        _assert_absent(
            disposable_drop_setup.psql,
            port=disposable_drop_setup.port,
            password=disposable_drop_setup.password,
            database=name,
        )
    finally:
        _terminate_waiter(waiter)


@pytest.fixture
def bootstrap_cleanup_setup(
    tmp_path: Path,
    docker_visible_postgres_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_BootstrapCleanupSetup]:
    if os.environ.get("ODCLI_RUN_BOOTSTRAP_CLEANUP_E2E") != "1":
        pytest.skip(
            "set ODCLI_RUN_BOOTSTRAP_CLEANUP_E2E=1 for the bootstrap cleanup public-boundary E2E"
        )
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
    init_args = (
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
    )
    primary_failure: BaseException | None = None
    cluster: PostgresCluster | None = None
    try:
        init_result = CliRunner().invoke(cli, init_args)
        assert init_result.exit_code == 0, init_result.output
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
        yield _BootstrapCleanupSetup(
            project_root=tmp_path,
            psql=psql,
            port=port,
            password=password,
            cluster=cluster,
            catalog=catalog,
            claim=claim,
            instance=instance,
            init_args=init_args,
            volume_name=volume_name,
        )
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        try:
            catalog.close()
        finally:
            if cluster is not None:
                cleanup_postgres_project(
                    compose_file=cluster.compose_file,
                    compose_project_name=cluster.compose_project_name,
                    volume_name=compose_volume_name(cluster._project_id),
                    primary_failure=primary_failure,
                )


def _invoke_public_db_rm(setup: _BootstrapCleanupSetup, monkeypatch: pytest.MonkeyPatch) -> Result:
    with monkeypatch.context() as public_cli:
        public_cli.setattr(
            "odoo_instance_sdk.commands.pg._database_instance",
            lambda _ctx: (None, setup.instance),
        )
        return CliRunner().invoke(
            cli,
            [
                "--project",
                str(setup.project_root),
                "db",
                "rm",
                "tmp",
                "--force-default",
                "--yes",
                "--format",
                "json",
            ],
        )


def _remove_owned_bootstrap_tmp(
    setup: _BootstrapCleanupSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    latest = setup.catalog._latest_database_event(
        setup.cluster.endpoint_host, setup.cluster.endpoint_port, "tmp"
    )
    assert latest is not None and latest["event_type"] == "bootstrapped"
    data_directory = Path(str(latest["data_directory"]))
    unrelated = data_directory / "filestore" / "unrelated"
    unrelated.mkdir(parents=True)
    (unrelated / "keep").write_text("keep", encoding="utf-8")
    result = _invoke_public_db_rm(setup, monkeypatch)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["result"]["terminated_sessions"] == 0
    _assert_absent(setup.psql, port=setup.port, password=setup.password, database="tmp")
    assert _volume_is_present(setup.volume_name)
    assert (unrelated / "keep").read_text(encoding="utf-8") == "keep"


@pytest.mark.serial
@pytest.mark.timeout(240)
def test_disposable_bootstrap_cleanup_public_cli_boundary(
    bootstrap_cleanup_setup: _BootstrapCleanupSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _remove_owned_bootstrap_tmp(bootstrap_cleanup_setup, monkeypatch)


@pytest.mark.serial
@pytest.mark.timeout(240)
def test_disposable_bootstrap_cleanup_refuses_foreign_volume(
    bootstrap_cleanup_setup: _BootstrapCleanupSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _remove_owned_bootstrap_tmp(bootstrap_cleanup_setup, monkeypatch)
    second_init = CliRunner().invoke(cli, bootstrap_cleanup_setup.init_args)
    assert second_init.exit_code == 0, second_init.output
    latest = bootstrap_cleanup_setup.catalog._latest_database_event(
        bootstrap_cleanup_setup.cluster.endpoint_host,
        bootstrap_cleanup_setup.cluster.endpoint_port,
        "tmp",
    )
    assert latest is not None and latest["event_type"] == "bootstrapped"
    event_count = bootstrap_cleanup_setup.catalog._conn.execute(
        "SELECT COUNT(*) FROM database_events WHERE database_name='tmp'"
    ).fetchone()[0]
    waiter = _session_process(
        bootstrap_cleanup_setup.psql,
        port=bootstrap_cleanup_setup.port,
        password=bootstrap_cleanup_setup.password,
        database="tmp",
    )
    try:
        _wait_for_session_ready(waiter)
        bootstrap_cleanup_setup.catalog._conn.execute(
            "UPDATE postgres_clusters SET volume_name='foreign-volume' WHERE cluster_id=?",
            (str(bootstrap_cleanup_setup.claim.cluster_id),),
        )
        bootstrap_cleanup_setup.catalog._conn.commit()
        refused = _invoke_public_db_rm(bootstrap_cleanup_setup, monkeypatch)
        assert refused.exit_code != 0
        assert waiter.poll() is None
        _assert_present(
            bootstrap_cleanup_setup.psql,
            port=bootstrap_cleanup_setup.port,
            password=bootstrap_cleanup_setup.password,
            database="tmp",
        )
        assert _volume_is_present(bootstrap_cleanup_setup.volume_name)
        latest = bootstrap_cleanup_setup.catalog._latest_database_event(
            bootstrap_cleanup_setup.cluster.endpoint_host,
            bootstrap_cleanup_setup.cluster.endpoint_port,
            "tmp",
        )
        assert latest is not None and latest["event_type"] == "bootstrapped"
        assert (
            bootstrap_cleanup_setup.catalog._conn.execute(
                "SELECT COUNT(*) FROM database_events WHERE database_name='tmp'"
            ).fetchone()[0]
            == event_count
        )
    finally:
        _terminate_waiter(waiter)
