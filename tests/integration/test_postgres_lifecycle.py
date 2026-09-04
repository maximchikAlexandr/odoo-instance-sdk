"""Opt-in disposable integration test for project-level PostgreSQL cluster lifecycle.

Requires Docker/Docker Compose on PATH. Skips automatically otherwise.
Proves: init → up/healthy → instance preflight → stop while preserving the volume.

Run with: ``pytest -m integration tests/integration/test_postgres_lifecycle.py``
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.postgres_compose import docker_ready
from odoo_instance_sdk.resources.postgres import PostgresCluster

pytestmark = pytest.mark.integration


def _skip_if_no_docker() -> None:
    ready, diagnostic = docker_ready(timeout=3.0)
    if not ready:
        pytest.skip(
            f"docker is not ready ({diagnostic}); skipping postgres lifecycle integration test"
        )


def _free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.serial
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
            str(_free_loopback_port()),
            "--postgres-user",
            "odoo",
        ],
    )
    assert init_result.exit_code == 0, init_result.output
    assert (tmp_path / ".odcli" / "project.toml").is_file()
    assert "postgres" in (tmp_path / ".odcli" / "project.toml").read_text().lower()

    cluster = PostgresCluster.from_project(tmp_path)
    assert cluster.owned is True
    primary_failure: BaseException | None = None
    try:
        digest = cluster.resolve_image_digest(timeout=45.0)
        cluster.approve_image(digest, timeout=45.0)
        # up — should start the cluster and become healthy.
        cluster.ensure_running(timeout=45.0)
        state = cluster.status()
        assert state.value == "healthy"

        # volume should exist after up.
        compose_file = cluster.compose_file
        assert compose_file.is_file()
        password_file = cluster.password_file
        assert password_file.is_file()
        mode = password_file.stat().st_mode & 0o777
        assert mode == 0o600

        # instance preflight: build an OdooInstance bound to the cluster and
        # call run_foreground with a stubbed binary that exits 0 immediately,
        # proving the dependency preflight fires against the running cluster.
        from odoo_instance_sdk.client import OdooClient
        from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
        from odoo_instance_sdk.models import StartConfig
        from odoo_instance_sdk.resources.instance import OdooInstance

        client = OdooClient(config=OdooClientConfig(executable=shutil.which("true") or "true"))
        http_port = _free_loopback_port()
        instance = OdooInstance(
            config=InstanceConfig(
                base_url=f"http://127.0.0.1:{http_port}",
                start_config=StartConfig(http_port=http_port, config_path="/tmp/odoo.conf"),
            ),
            _client=client,
            _postgres_cluster=cluster,
        )
        exit_code = instance.run_foreground()
        assert exit_code == 0  # preflight passed (cluster already healthy)

        # stop — preserves the volume.
        cluster.stop(timeout=30.0)
        stopped_state = cluster.status()
        assert stopped_state.value == "stopped"

        # Assert the named volume still exists after stop (preserved, not down -v).
        volume_name = f"pgdata_{cluster.to_diagnostic_dict()['project_id']}"
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
        cluster.ensure_running(timeout=45.0)
        assert cluster.status().value == "healthy"
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        # Clean up this exact disposable integration project and assert Docker did it.
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
