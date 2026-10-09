"""Opt-in real-PostgreSQL coverage for the native diagnostics boundary.

Run with ``pytest -m integration tests/integration/test_postgres_diagnostics.py``.
The test is deliberately skipped only when Docker is unavailable; its real
PostgreSQL client is injected from the disposable image when the host lacks
``psql``.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import PostgresClusterStartError
from odoo_instance_sdk.internal.pg.stats import build_stats_sql
from odoo_instance_sdk.internal.postgres_compose import docker_ready
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster
from tests.integration.postgres_cleanup import (
    cleanup_postgres_project,
    patch_compose_init_for_integration,
)

pytestmark = pytest.mark.integration


def _free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _require_docker() -> None:
    ready, diagnostic = docker_ready(timeout=30.0)
    if not ready:
        pytest.skip(f"docker is not ready ({diagnostic}); PostgreSQL integration is unavailable")


def _install_container_psql(
    *, tmp_path: Path, cluster: PostgresCluster, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Inject the image's real psql client when the host does not provide one."""
    bin_dir = tmp_path / "test-bin"
    bin_dir.mkdir()
    executable = bin_dir / "psql"
    executable.write_text(
        "#!"
        + sys.executable
        + "\n"
        + "import os\n"
        + "import sys\n"
        + "\n"
        + "args = []\n"
        + "skip_value = False\n"
        + "for argument in sys.argv[1:]:\n"
        + "    if skip_value:\n"
        + "        skip_value = False\n"
        + "        continue\n"
        + "    if argument in {'-h', '--host', '-p', '--port'}:\n"
        + "        skip_value = True\n"
        + "        continue\n"
        + "    if argument.startswith('--host=') or argument.startswith('--port='):\n"
        + "        continue\n"
        + "    args.append(argument)\n"
        + "command = [\n"
        + "    'docker', 'compose', '--project-name',\n"
        + "    os.environ['ODCLI_TEST_PG_COMPOSE_PROJECT'], '-f',\n"
        + "    os.environ['ODCLI_TEST_PG_COMPOSE_FILE'], 'exec', '-T', 'postgres',\n"
        + "    'sh', '-c',\n"
        + '    "PGPASSWORD=\\"$(cat /run/secrets/postgres_password)\\" exec psql \\"$@\\"",\n'
        + "    'psql', *args,\n"
        + "]\n"
        + "os.execvp(command[0], command)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("ODCLI_TEST_PG_COMPOSE_PROJECT", cluster.compose_project_name)
    monkeypatch.setenv("ODCLI_TEST_PG_COMPOSE_FILE", str(cluster.compose_file))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return str(executable)


def _psql_process(
    psql: str,
    cluster: PostgresCluster,
    password: str,
    database: str,
    *args: str,
    stdout: int | None = subprocess.PIPE,
) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["PGPASSWORD"] = password
    environment.pop("PGOPTIONS", None)
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
            cluster.endpoint_host,
            "-p",
            str(cluster.endpoint_port),
            "-U",
            "odoo",
            "-d",
            database,
            *args,
        ],
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )


@dataclass(frozen=True)
class _DiagnosticsContext:
    project: Path
    cluster: PostgresCluster
    psql: str
    password: str
    database: DatabaseResource


def _wait_until(predicate: Callable[[], bool], *, timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(description)
        time.sleep(min(0.05, remaining))


def _create_diagnostic_fixture(database: DatabaseResource) -> None:
    setup = database.execute_sql(
        "CREATE TABLE IF NOT EXISTS odcli_diag_fixture "
        "(id integer PRIMARY KEY, payload text); "
        "CREATE INDEX IF NOT EXISTS odcli_diag_fixture_payload_idx "
        "ON odcli_diag_fixture (payload); "
        "CREATE INDEX IF NOT EXISTS odcli_diag_fixture_payload_gin "
        "ON odcli_diag_fixture USING gin (to_tsvector('simple', payload)); "
        "INSERT INTO odcli_diag_fixture (id, payload) "
        "SELECT id, repeat('fixture token ', 32) FROM generate_series(1, 256) AS ids(id) "
        "ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload;",
        timeout=30.0,
    )
    assert setup.returncode == 0, setup.stderr


@pytest.fixture
def diagnostics_context(
    tmp_path: Path,
    docker_visible_postgres_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_DiagnosticsContext]:
    _require_docker()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_project_postgres_dir",
        lambda project_id: docker_visible_postgres_root / str(project_id) / "postgres",
    )
    patch_compose_init_for_integration(monkeypatch)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "integration@example.test"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "integration"], cwd=tmp_path, check=True)
    source_config = tmp_path / "odoo.conf"
    source_config.write_text(
        "[options]\n"
        "db_name = postgres\n"
        "db_host = 127.0.0.1\n"
        "db_port = 5432\n"
        "db_user = odoo\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n",
        encoding="utf-8",
    )

    from click.testing import CliRunner

    port = _free_loopback_port()
    init_result = CliRunner().invoke(
        cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--odoo-bin",
            sys.executable,
            "--python",
            sys.executable,
            "--config",
            str(source_config),
            "--database",
            "postgres",
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
        ],
    )
    assert init_result.exit_code == 0, init_result.output

    cluster = PostgresCluster.from_project(tmp_path)
    psql = _install_container_psql(tmp_path=tmp_path, cluster=cluster, monkeypatch=monkeypatch)
    compose_file = cluster.compose_file
    volume_name = f"pgdata_{cluster.to_diagnostic_dict()['project_id']}"
    primary_failure: BaseException | None = None
    try:
        try:
            digest = cluster.resolve_image_digest(timeout=60.0)
        except PostgresClusterStartError as exc:
            detail = str(exc)
            if "failed to resolve reference" in detail or "TLS handshake timeout" in detail:
                pytest.skip(f"PostgreSQL image registry is unavailable: {detail}")
            raise
        cluster.approve_image(digest, timeout=60.0)
        cluster.ensure_running(timeout=60.0)
        assert cluster.status().value == "healthy"
        password = cluster.password_file.read_text(encoding="utf-8").strip()
        source_config.write_text(
            "[options]\n"
            "db_name = postgres\n"
            "db_host = 127.0.0.1\n"
            "db_port = 5432\n"
            "db_user = odoo\n"
            f"db_password = {password}\n"
            "http_interface = 127.0.0.1\n"
            "http_port = 8069\n",
            encoding="utf-8",
        )

        client = OdooClient(config=OdooClientConfig(executable="true"))
        instance = OdooInstance(
            config=InstanceConfig(
                base_url="http://127.0.0.1:8069",
                start_config=StartConfig(http_port=8069, config_path="/tmp/odoo.conf"),
                configured_database_names=("postgres",),
                db_host=cluster.endpoint_host,
                db_port=cluster.endpoint_port,
                db_user="odoo",
                db_password=password,
                default_cwd=tmp_path,
            ),
            _client=client,
            _postgres_cluster=cluster,
        )
        yield _DiagnosticsContext(tmp_path, cluster, psql, password, instance.databases)
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        try:
            cleanup_postgres_project(
                compose_file=compose_file,
                compose_project_name=cluster.compose_project_name,
                volume_name=volume_name,
                primary_failure=None,
            )
        except BaseException as exc:
            if primary_failure is not None:
                raise BaseExceptionGroup(
                    "primary test failure and PostgreSQL cleanup failure", [primary_failure, exc]
                ) from None
            raise


@pytest.mark.serial
@pytest.mark.timeout(180)
def test_real_diagnostics_blocking_sessions_report_blockers(
    diagnostics_context: _DiagnosticsContext,
) -> None:
    context = diagnostics_context
    _create_diagnostic_fixture(context.database)
    blocker = _psql_process(
        context.psql,
        context.cluster,
        context.password,
        "postgres",
        "-c",
        "BEGIN; LOCK TABLE odcli_diag_fixture IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(30);",
    )
    waiter: subprocess.Popen[str] | None = None
    try:
        holder = "0"

        def lock_is_held() -> bool:
            nonlocal holder
            probe = context.database.execute_sql(
                "SELECT count(*) FROM pg_locks l "
                "JOIN pg_class c ON c.oid = l.relation "
                "WHERE c.relname = 'odcli_diag_fixture' AND l.granted;",
                timeout=5.0,
            )
            holder = probe.stdout.strip()
            return holder == "1"

        _wait_until(
            lock_is_held,
            timeout=10.0,
            description=f"blocking PostgreSQL session did not hold the fixture lock: {holder!r}",
        )
        assert blocker.poll() is None
        waiter = _psql_process(
            context.psql,
            context.cluster,
            context.password,
            "postgres",
            "-c",
            "BEGIN; LOCK TABLE odcli_diag_fixture IN ACCESS SHARE MODE; SELECT pg_sleep(30);",
        )

        def has_blocked_rows() -> bool:
            return bool(context.database.locks("postgres", top=20, timeout=10.0).rows)

        _wait_until(
            has_blocked_rows,
            timeout=10.0,
            description="expected a real blocked session in pg_locks",
        )
        locks = context.database.locks("postgres", top=20, timeout=10.0)
        assert any(row.blocking_pids for row in locks.rows)
        assert all(len(row.query_preview) <= 240 for row in locks.rows)
    finally:
        for active_child in (waiter, blocker):
            if active_child is None:
                continue
            if active_child.poll() is None:
                active_child.terminate()
                active_child.wait(timeout=5.0)
        terminated = context.database.execute_sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE pid <> pg_backend_pid() AND query LIKE '%pg_sleep(30)%';",
            timeout=5.0,
        )
        assert terminated.returncode == 0, terminated.stderr


@pytest.mark.serial
@pytest.mark.timeout(180)
def test_real_diagnostics_stats_report_fixture(
    diagnostics_context: _DiagnosticsContext,
) -> None:
    context = diagnostics_context
    _create_diagnostic_fixture(context.database)
    try:
        stats = context.database.stats("postgres", top=20, timeout=10.0)
    except Exception as exc:
        diagnostic = context.database.execute_sql(
            build_stats_sql(top=20, timeout=10.0), timeout=10.0
        )
        pytest.fail(f"real stats diagnostic failed: {exc}; stderr={diagnostic.stderr!r}")
    assert any(row.table == "odcli_diag_fixture" for row in stats.tables)
    assert any(index.index == "odcli_diag_fixture_pkey" for index in stats.indexes)
    assert all(isinstance(row.total_bytes, int) and row.total_bytes >= 0 for row in stats.tables)
    assert "cumulative_statistics" in {warning.code for warning in stats.warnings}


@pytest.mark.serial
@pytest.mark.timeout(180)
def test_real_diagnostics_monitoring_init_is_idempotent(
    diagnostics_context: _DiagnosticsContext,
) -> None:
    database = diagnostics_context.database

    def extension_names(query: str) -> set[str]:
        return {
            line.strip()
            for line in database.execute_sql(query, timeout=5.0).stdout.splitlines()
            if line.strip()
        }

    available = extension_names(
        "SELECT name FROM pg_available_extensions "
        "WHERE name IN ('pg_buffercache', 'pgstattuple') ORDER BY name;"
    )
    installed_before = extension_names(
        "SELECT extname FROM pg_extension "
        "WHERE extname IN ('pg_buffercache', 'pgstattuple') ORDER BY extname;"
    )
    assert available == {"pg_buffercache", "pgstattuple"}
    first_init = database.init_monitoring("postgres", timeout=20.0)
    installed_after_first = extension_names(
        "SELECT extname FROM pg_extension "
        "WHERE extname IN ('pg_buffercache', 'pgstattuple') ORDER BY extname;"
    )
    second_init = database.init_monitoring("postgres", timeout=20.0)
    installed_after_second = extension_names(
        "SELECT extname FROM pg_extension "
        "WHERE extname IN ('pg_buffercache', 'pgstattuple') ORDER BY extname;"
    )
    assert installed_after_first == available
    assert installed_after_second == installed_after_first
    assert set(first_init.installed) == available - installed_before
    assert set(first_init.already_present) == installed_before
    assert first_init.skipped == ()
    assert second_init.installed == ()
    assert set(second_init.already_present) == installed_after_first
    assert second_init.skipped == ()


@pytest.mark.serial
@pytest.mark.timeout(180)
def test_real_diagnostics_bloat_supports_exact_mixed_and_estimate_modes(
    diagnostics_context: _DiagnosticsContext,
) -> None:
    database = diagnostics_context.database
    _create_diagnostic_fixture(database)
    database.init_monitoring("postgres", timeout=20.0)
    bloat = database.bloat("postgres", top=20, exact_max_scan_mb=64, timeout=10.0)
    assert any(row.table == "odcli_diag_fixture" for row in bloat.tables)
    assert any(index.index == "odcli_diag_fixture_pkey" for index in bloat.indexes)
    assert bloat.capabilities.pgstattuple is True
    assert any(row.table == "odcli_diag_fixture" and row.method == "exact" for row in bloat.tables)
    assert any(
        index.index == "odcli_diag_fixture_pkey" and index.method == "exact"
        for index in bloat.indexes
    )
    mixed_top_one = database.bloat("postgres", top=1, exact_max_scan_mb=64, timeout=10.0)
    assert len(mixed_top_one.indexes) == 1
    assert mixed_top_one.indexes[0].index == "odcli_diag_fixture_payload_gin"
    assert mixed_top_one.indexes[0].method == "estimate"
    estimate_only = database.bloat("postgres", top=20, exact_max_scan_mb=0, timeout=10.0)
    assert all(row.method in {"estimate", "unavailable"} for row in estimate_only.tables)
    assert all(index.method in {"estimate", "unavailable"} for index in estimate_only.indexes)
    assert not any(row.method == "exact" for row in estimate_only.tables)
    assert not any(index.method == "exact" for index in estimate_only.indexes)


@pytest.mark.serial
@pytest.mark.timeout(180)
def test_real_diagnostics_native_psql_and_status_are_healthy(
    diagnostics_context: _DiagnosticsContext,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from click.testing import CliRunner

    native = CliRunner().invoke(
        cli,
        ["--project", str(diagnostics_context.project), "psql", "-c", "SELECT current_database();"],
    )
    assert native.exit_code == 0, native.output
    native_stdout = capfd.readouterr().out
    assert "current_database" in native_stdout
    assert "postgres" in native_stdout
    enriched = CliRunner().invoke(
        cli,
        ["--project", str(diagnostics_context.project), "postgres", "status", "--format", "json"],
    )
    assert enriched.exit_code == 0, enriched.output
    status_payload = json.loads(enriched.output)
    assert status_payload["result"]["server"] is not None
    assert status_payload["result"]["server_unavailability_reason"] is None
    assert diagnostics_context.cluster.status().value == "healthy"
