"""Opt-in disposable integration test for project-level PostgreSQL cluster lifecycle.

Requires Docker/Docker Compose on PATH. Skips automatically otherwise.
Proves: init → up/healthy → instance preflight → stop while preserving the volume.

Run with: ``pytest -m integration tests/integration/test_postgres_lifecycle.py``
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.postgres_compose import docker_available
from odoo_instance_sdk.resources.postgres import PostgresCluster

pytestmark = pytest.mark.integration


def _skip_if_no_docker() -> None:
    if not docker_available():
        pytest.skip("docker not available; skipping postgres lifecycle integration test")


def test_init_up_preflight_stop_preserves_volume(tmp_path: Path) -> None:
    _skip_if_no_docker()
    # init a git repo so repo_key is stable.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    from click.testing import CliRunner

    runner = CliRunner()
    init_result = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "python3",
            "--project",
            str(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "postgres:16-alpine",
            "--postgres-port",
            "5478",
            "--postgres-user",
            "odoo",
        ],
    )
    assert init_result.exit_code == 0, init_result.output
    assert (tmp_path / ".odcli" / "project.toml").is_file()
    assert "postgres" in (tmp_path / ".odcli" / "project.toml").read_text().lower()

    cluster = PostgresCluster.from_project(tmp_path)
    assert cluster.owned is True
    try:
        # up — should start the cluster and become healthy.
        cluster.ensure_running(timeout=120.0)
        state = cluster.status()
        assert state.value == "healthy"

        # volume should exist after up.
        compose_file = cluster._compose_file()
        assert compose_file.is_file()
        password_file = cluster._password_file()
        assert password_file.is_file()
        mode = password_file.stat().st_mode & 0o777
        assert mode == 0o600

        # instance preflight: build an OdooInstance bound to the cluster and
        # call run_foreground with a stubbed binary that exits 0 immediately,
        # proving the dependency preflight fires against the running cluster.
        from unittest.mock import patch

        from odoo_instance_sdk.client import OdooClient
        from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
        from odoo_instance_sdk.models import StartConfig
        from odoo_instance_sdk.resources.instance import OdooInstance

        client = OdooClient(config=OdooClientConfig(executable="/bin/true"))
        instance = OdooInstance(
            config=InstanceConfig(
                base_url="http://127.0.0.1:8069",
                start_config=StartConfig(http_port=8069, config_path="/tmp/odoo.conf"),
            ),
            _client=client,
            _postgres_cluster=cluster,
        )
        with patch(
            "odoo_instance_sdk.resources.instance.run_foreground_process",
            return_value=0,
        ):
            exit_code = instance.run_foreground()
        assert exit_code == 0  # preflight passed (cluster already healthy)

        # stop — preserves the volume.
        cluster.stop(timeout=30.0)
        stopped_state = cluster.status()
        assert stopped_state.value == "stopped"

        # Assert the named volume still exists after stop (preserved, not down -v).
        volume_name = f"pgdata_{cluster._project_id}"
        vol_inspect = subprocess.run(
            ["docker", "volume", "inspect", volume_name],
            capture_output=True,
            text=True,
            check=False,
        )
        assert vol_inspect.returncode == 0, (
            f"volume {volume_name} should persist after stop (got rc={vol_inspect.returncode}, "
            f"stderr={vol_inspect.stderr.strip()})"
        )

        # Restart (idempotent ensure_running).
        cluster.ensure_running(timeout=60.0)
        assert cluster.status().value == "healthy"
    finally:
        # Best-effort cleanup: stop the cluster, do NOT delete the volume.
        import contextlib

        with contextlib.suppress(Exception):
            cluster.stop(timeout=10.0)
        # ponytail: volume preserved per spec; manual `docker compose down -v` to remove.
