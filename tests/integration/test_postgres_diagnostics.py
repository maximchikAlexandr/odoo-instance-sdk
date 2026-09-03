"""Opt-in real-PostgreSQL coverage for the native diagnostics boundary.

Run with ``pytest -m integration tests/integration/test_postgres_diagnostics.py``.
The test is deliberately skipped only when Docker or the host ``psql`` client
is unavailable; those diagnostics identify the environment blocker instead of
turning an unavailable integration environment into a false pass.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import PostgresClusterStartError
from odoo_instance_sdk.internal.pg.stats import build_stats_sql
from odoo_instance_sdk.internal.postgres_compose import docker_ready
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster

pytestmark = pytest.mark.integration


def _free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _require_tools() -> str:
    ready, diagnostic = docker_ready(timeout=3.0)
    if not ready:
        pytest.skip(f"docker is not ready ({diagnostic}); PostgreSQL integration is unavailable")
    psql = shutil.which("psql")
    if psql is None:
        pytest.skip("psql is missing on PATH; native PostgreSQL integration is unavailable")
    return psql


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


@pytest.mark.serial
@pytest.mark.timeout(180)
def test_real_diagnostics_blocking_stats_bloat_init_status_and_native_psql(  # noqa: C901
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    psql = _require_tools()
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
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--config",
            str(source_config),
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
    primary_failure: BaseException | None = None
    blocker: subprocess.Popen[str] | None = None
    waiter: subprocess.Popen[str] | None = None
    volume_name = f"pgdata_{cluster.to_diagnostic_dict()['project_id']}"
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
        database = instance.databases
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

        blocker = _psql_process(
            psql,
            cluster,
            password,
            "postgres",
            "-c",
            "BEGIN; LOCK TABLE odcli_diag_fixture IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(30);",
        )
        deadline = time.monotonic() + 10.0
        holder = "0"
        while blocker.poll() is None and time.monotonic() < deadline:
            probe = database.execute_sql(
                "SELECT count(*) FROM pg_locks l "
                "JOIN pg_class c ON c.oid = l.relation "
                "WHERE c.relname = 'odcli_diag_fixture' AND l.granted;",
                timeout=5.0,
            )
            holder = probe.stdout.strip()
            if holder == "1":
                break
            time.sleep(0.05)
        assert blocker.poll() is None, (
            "blocking PostgreSQL session exited before acquiring its lock"
        )
        assert holder == "1", (
            f"blocking PostgreSQL session did not hold the fixture lock: {holder!r}"
        )

        waiter = _psql_process(
            psql,
            cluster,
            password,
            "postgres",
            "-c",
            "BEGIN; LOCK TABLE odcli_diag_fixture IN ACCESS SHARE MODE; SELECT pg_sleep(30);",
            stdout=subprocess.PIPE,
        )
        time.sleep(0.2)
        assert waiter.poll() is None, (
            f"waiting PostgreSQL session exited before the blocker was observed: "
            f"{waiter.stderr.read() if waiter.stderr is not None else ''}"
        )
        locks = database.locks("postgres", top=20, timeout=10.0)
        deadline = time.monotonic() + 5.0
        while not locks.rows and waiter.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
            locks = database.locks("postgres", top=20, timeout=10.0)
        assert locks.rows, "expected a real blocked session in pg_locks"
        assert any(row.blocking_pids for row in locks.rows)
        assert all(len(row.query_preview) <= 240 for row in locks.rows)

        for active_child in (waiter, blocker):
            if active_child.poll() is None:
                active_child.terminate()
                active_child.wait(timeout=5.0)
        terminated = database.execute_sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE pid <> pg_backend_pid() AND query LIKE '%pg_sleep(30)%';",
            timeout=5.0,
        )
        assert terminated.returncode == 0, terminated.stderr
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            released = database.execute_sql(
                "SELECT count(*) FROM pg_locks l "
                "JOIN pg_class c ON c.oid = l.relation "
                "WHERE c.relname = 'odcli_diag_fixture' AND l.granted;",
                timeout=5.0,
            )
            if released.stdout.strip() == "0":
                break
            time.sleep(0.2)

        try:
            stats = database.stats("postgres", top=20, timeout=10.0)
        except Exception as exc:
            diagnostic = database.execute_sql(build_stats_sql(top=20, timeout=10.0), timeout=10.0)
            pytest.fail(f"real stats diagnostic failed: {exc}; stderr={diagnostic.stderr!r}")
        assert any(row.table == "odcli_diag_fixture" for row in stats.tables)
        assert any(index.index == "odcli_diag_fixture_pkey" for index in stats.indexes)
        assert all(
            isinstance(row.total_bytes, int) and row.total_bytes >= 0 for row in stats.tables
        )
        assert "cumulative_statistics" in {warning.code for warning in stats.warnings}

        available_extensions = {
            line.strip()
            for line in database.execute_sql(
                "SELECT name FROM pg_available_extensions "
                "WHERE name IN ('pg_buffercache', 'pgstattuple') ORDER BY name;",
                timeout=5.0,
            ).stdout.splitlines()
            if line.strip()
        }
        installed_before = {
            line.strip()
            for line in database.execute_sql(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('pg_buffercache', 'pgstattuple') ORDER BY extname;",
                timeout=5.0,
            ).stdout.splitlines()
            if line.strip()
        }
        assert available_extensions == {"pg_buffercache", "pgstattuple"}

        first_init = database.init_monitoring("postgres", timeout=20.0)
        installed_after_first = {
            line.strip()
            for line in database.execute_sql(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('pg_buffercache', 'pgstattuple') ORDER BY extname;",
                timeout=5.0,
            ).stdout.splitlines()
            if line.strip()
        }
        second_init = database.init_monitoring("postgres", timeout=20.0)
        installed_after_second = {
            line.strip()
            for line in database.execute_sql(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('pg_buffercache', 'pgstattuple') ORDER BY extname;",
                timeout=5.0,
            ).stdout.splitlines()
            if line.strip()
        }
        assert installed_after_first == available_extensions
        assert installed_after_second == installed_after_first
        assert set(first_init.installed) == available_extensions - installed_before
        assert set(first_init.already_present) == installed_before
        assert first_init.skipped == ()
        assert second_init.installed == ()
        assert set(second_init.already_present) == installed_after_first
        assert second_init.skipped == ()

        bloat = database.bloat("postgres", top=20, exact_max_scan_mb=64, timeout=10.0)
        assert any(row.table == "odcli_diag_fixture" for row in bloat.tables)
        assert any(index.index == "odcli_diag_fixture_pkey" for index in bloat.indexes)
        assert bloat.capabilities.pgstattuple is True
        assert any(
            row.table == "odcli_diag_fixture" and row.method == "exact" for row in bloat.tables
        )
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

        from click.testing import CliRunner

        native = CliRunner().invoke(
            cli,
            ["--project", str(tmp_path), "psql", "-c", "SELECT current_database();"],
        )
        assert native.exit_code == 0, native.output
        native_stdout = capfd.readouterr().out
        assert "current_database" in native_stdout
        assert "postgres" in native_stdout

        enriched = CliRunner().invoke(
            cli,
            ["--project", str(tmp_path), "postgres", "status", "--json"],
        )
        assert enriched.exit_code == 0, enriched.output
        status_payload = json.loads(enriched.output)
        assert status_payload["result"]["server"] is not None
        assert status_payload["result"]["server_unavailability_reason"] is None
        assert cluster.status().value == "healthy"
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        for cleanup_child in (waiter, blocker):
            if cleanup_child is not None and cleanup_child.poll() is None:
                cleanup_child.terminate()
                try:
                    cleanup_child.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    cleanup_child.kill()
                    cleanup_child.wait(timeout=5.0)
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
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if primary_failure is None:
            assert cleanup.returncode == 0, cleanup.stderr
            assert (
                subprocess.run(
                    ["docker", "volume", "inspect", volume_name], capture_output=True, check=False
                ).returncode
                != 0
            )
