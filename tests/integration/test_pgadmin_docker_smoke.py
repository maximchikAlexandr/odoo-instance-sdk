"""Opt-in disposable smoke test for the shared pgAdmin lifecycle.

Set ``ODCLI_PGADMIN_DOCKER_SMOKE=1`` to run this test.  It requires a working
Docker daemon, Docker Compose, registry access for the pinned pgAdmin image,
and (on Linux) ``getfacl``/``setfacl``.  The test skips when those external
prerequisites are unavailable and removes only its disposable Compose project
and the SDK-owned pgAdmin container.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.internal import pgadmin, pgadmin_files
from odoo_instance_sdk.internal.postgres_compose import SubprocessComposeRunner, docker_available
from odoo_instance_sdk.models import PgAdminOpenState

pytestmark = [pytest.mark.integration, pytest.mark.serial]


def _run(args: list[str], *, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def _skip_if_prerequisites_missing() -> None:
    if os.environ.get("ODCLI_PGADMIN_DOCKER_SMOKE") != "1":
        pytest.skip("set ODCLI_PGADMIN_DOCKER_SMOKE=1 to run the disposable Docker smoke test")
    if not docker_available():
        pytest.skip("docker is not available")
    for command in (["docker", "info"], ["docker", "compose", "version"]):
        try:
            result = _run(command, timeout=10.0)
        except (OSError, subprocess.SubprocessError):
            pytest.skip(f"external Docker prerequisite failed: {' '.join(command)}")
        if result.returncode != 0:
            pytest.skip(f"external Docker prerequisite failed: {' '.join(command)}")
    if pgadmin_files._linux() and not all(shutil.which(tool) for tool in ("getfacl", "setfacl")):
        pytest.skip("Linux pgAdmin smoke requires getfacl and setfacl")


def _ensure_image(image: str) -> None:
    inspected = _run(["docker", "image", "inspect", image], timeout=20.0)
    if inspected.returncode == 0:
        return
    pulled = _run(["docker", "pull", image], timeout=180.0)
    if pulled.returncode != 0:
        pytest.skip(f"Docker image unavailable: {image}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _compose_args(project: str, compose_file: Path, *extra: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "-f",
        str(compose_file),
        *extra,
    ]


def _pgadmin_container_id() -> str:
    inspected = _run(
        ["docker", "inspect", "--format", "{{.Id}}", pgadmin_files.PGADMIN_CONTAINER_NAME],
        timeout=10.0,
    )
    assert inspected.returncode == 0, inspected.stderr
    container_id = inspected.stdout.strip()
    assert container_id
    return container_id


def _active_pgpass(container_id: str) -> tuple[str, str]:
    mode = _run(
        [
            "docker",
            "exec",
            container_id,
            "stat",
            "-c",
            "%a",
            f"{pgadmin_files.PGADMIN_DATA_DESTINATION}/.pgpass",
        ],
        timeout=10.0,
    )
    assert mode.returncode == 0, mode.stderr
    content = _run(
        [
            "docker",
            "exec",
            container_id,
            "cat",
            f"{pgadmin_files.PGADMIN_DATA_DESTINATION}/.pgpass",
        ],
        timeout=10.0,
    )
    assert content.returncode == 0, content.stderr
    return mode.stdout.strip(), content.stdout


def _pgadmin_passwordless_query(
    container_id: str, host: str, database: str
) -> subprocess.CompletedProcess[str]:
    """Run SELECT 1 inside pgAdmin using only its active mounted passfile."""
    script = (
        "import sys,psycopg;"
        "connection=psycopg.connect(host=sys.argv[1],port=5432,user='odoo',"
        "dbname=sys.argv[2],passfile='/var/lib/pgadmin/.pgpass');"
        "cursor=connection.cursor();cursor.execute('SELECT 1');"
        "print(cursor.fetchone()[0]);cursor.close();connection.close()"
    )
    return _run(
        [
            "docker",
            "exec",
            container_id,
            "/venv/bin/python3",
            "-c",
            script,
            host,
            database,
        ],
        timeout=30.0,
    )


def _pgadmin_rejects_password(
    container_id: str, host: str, database: str, password: str
) -> subprocess.CompletedProcess[str]:
    """Prove a rotated PostgreSQL password is rejected inside pgAdmin."""
    script = (
        "import sys,psycopg\n"
        "try:\n"
        " psycopg.connect(host=sys.argv[1],port=5432,user='odoo',"
        "dbname=sys.argv[2],password=sys.argv[3]).close()\n"
        "except psycopg.Error:\n"
        " print('rejected')\n"
        "else:\n"
        " raise SystemExit('old credential was accepted')\n"
    )
    return _run(
        [
            "docker",
            "exec",
            container_id,
            "/venv/bin/python3",
            "-c",
            script,
            host,
            database,
            password,
        ],
        timeout=30.0,
    )


def _owned_pgadmin_container(*, network: str) -> bool:
    inspected = _run(
        ["docker", "inspect", "--format", "json", pgadmin_files.PGADMIN_CONTAINER_NAME]
    )
    if inspected.returncode != 0:
        return False
    try:
        payload = json.loads(inspected.stdout)
        row = payload[0] if isinstance(payload, list) and payload else payload
        labels = row["Config"]["Labels"]
    except (KeyError, TypeError, ValueError, IndexError):
        return False
    return (
        isinstance(labels, dict)
        and labels.get(pgadmin_files.PGADMIN_LABEL_MANAGED) == "true"
        and labels.get(pgadmin_files.PGADMIN_LABEL_NETWORK) == network
    )


def test_shared_pgadmin_fresh_reuse_and_cross_project_reconfigure(  # noqa: C901
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_if_prerequisites_missing()
    _ensure_image(pgadmin_files.PGADMIN_IMAGE)
    _ensure_image("postgres:16-alpine")

    existing = _run(["docker", "inspect", pgadmin_files.PGADMIN_CONTAINER_NAME], timeout=10.0)
    if existing.returncode == 0:
        pytest.skip("the fixed SDK pgAdmin container name is already in use")

    projects = (f"odoo_sdk_smoke_{os.getpid()}_a", f"odoo_sdk_smoke_{os.getpid()}_b")
    passwords = ("smoke-password-a", "smoke-password-b")
    rotation_password = "smoke-password-c"
    compose_files = tuple(tmp_path / project / "compose.yaml" for project in projects)
    for compose_file, password in zip(compose_files, passwords, strict=True):
        compose_file.parent.mkdir()
        compose_file.write_text(
            "services:\n"
            "  postgres:\n"
            "    image: postgres:16-alpine\n"
            "    environment:\n"
            "      POSTGRES_USER: odoo\n"
            f"      POSTGRES_PASSWORD: {password}\n"
            "    healthcheck:\n"
            '      test: ["CMD-SHELL", "pg_isready -U odoo -d postgres"]\n'
            "      interval: 2s\n"
            "      timeout: 3s\n"
            "      retries: 30\n"
            "      start_period: 5s\n",
            encoding="utf-8",
        )
    networks = tuple(f"{project}_default" for project in projects)
    compose_runner = SubprocessComposeRunner()
    data_root = tmp_path / "sdk-data"
    pgadmin_root = data_root / "pgadmin"
    private = pgadmin_root / "private"
    paths = pgadmin_files.PgAdminPaths(
        root=pgadmin_root,
        private_dir=private,
        data_dir=pgadmin_root / "data",
        admin_password=private / "admin-password",
        pgpass=private / ".pgpass",
        servers_json=private / "servers.json",
        metadata=private / "metadata.json",
        lock=tmp_path / "sdk-state" / "locks" / "pgadmin.lock",
    )
    monkeypatch.setattr(
        pgadmin_files.PgAdminPaths,
        "from_defaults",
        classmethod(lambda cls: paths),
    )
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: data_root)
    smoke_port = _free_port()
    monkeypatch.setattr(pgadmin_files, "select_port", lambda _paths: smoke_port)

    started_projects: list[tuple[str, Path]] = []
    for project, compose_file in zip(projects, compose_files, strict=True):
        try:
            compose_up = _run(
                _compose_args(project, compose_file, "up", "--detach", "--wait"), timeout=180.0
            )
        except (OSError, subprocess.SubprocessError) as exc:
            compose_up = None
            detail = str(exc)
        else:
            assert compose_up is not None
            detail = compose_up.stderr.strip()
        if compose_up is None or compose_up.returncode != 0:
            for old_project, old_file in started_projects:
                _run(
                    _compose_args(old_project, old_file, "down", "--volumes", "--remove-orphans"),
                    timeout=60.0,
                )
            _run(
                _compose_args(project, compose_file, "down", "--volumes", "--remove-orphans"),
                timeout=60.0,
            )
            pytest.skip(f"disposable postgres could not start: {detail}")
        started_projects.append((project, compose_file))

    clusters = tuple(
        SimpleNamespace(
            compose_runner=compose_runner,
            compose_file=compose_file,
            compose_project_name=project,
            _user="odoo",
        )
        for project, compose_file in zip(projects, compose_files, strict=True)
    )
    instances = tuple(
        SimpleNamespace(config=InstanceConfig(base_url="http://127.0.0.1", db_password=password))
        for password in passwords
    )
    primary_failure: BaseException | None = None
    try:
        for project, compose_file, database in (
            (projects[0], compose_files[0], "smoke_old"),
            (projects[1], compose_files[1], "smoke_new"),
        ):
            created = _run(
                _compose_args(
                    project,
                    compose_file,
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    "odoo",
                    "-d",
                    "postgres",
                    "-c",
                    f"CREATE DATABASE {database}",
                ),
                timeout=30.0,
            )
            assert created.returncode == 0, created.stderr

        started = pgadmin.open_pgadmin_lifecycle(
            environment=object(), instance=instances[0], cluster=clusters[0], database="smoke_old"
        )
        assert started.state is PgAdminOpenState.STARTED
        assert started.url.startswith("http://127.0.0.1:")
        old_container_id = _pgadmin_container_id()
        old_mode, old_active_pgpass = _active_pgpass(old_container_id)
        assert old_mode == "600"
        assert old_active_pgpass == f"{projects[0]}-postgres-1:5432:*:odoo:{passwords[0]}\n"
        authenticated_old = _pgadmin_passwordless_query(
            old_container_id, f"{projects[0]}-postgres-1", "smoke_old"
        )
        assert authenticated_old.returncode == 0, authenticated_old.stderr
        assert authenticated_old.stdout.strip() == "1"
        reused = pgadmin.open_pgadmin_lifecycle(
            environment=object(), instance=instances[0], cluster=clusters[0], database="smoke_old"
        )
        assert reused.state is PgAdminOpenState.REUSED
        reconfigured = pgadmin.open_pgadmin_lifecycle(
            environment=object(), instance=instances[1], cluster=clusters[1], database="smoke_new"
        )
        assert reconfigured.state is PgAdminOpenState.RECONFIGURED
        assert reconfigured.url == started.url == reused.url
        new_container_id = _pgadmin_container_id()
        assert new_container_id != old_container_id
        new_mode, new_active_pgpass = _active_pgpass(new_container_id)
        assert new_mode == "600"
        assert new_active_pgpass == f"{projects[1]}-postgres-1:5432:*:odoo:{passwords[1]}\n"
        authenticated_new = _pgadmin_passwordless_query(
            new_container_id, f"{projects[1]}-postgres-1", "smoke_new"
        )
        assert authenticated_new.returncode == 0, authenticated_new.stderr
        assert authenticated_new.stdout.strip() == "1"
        rotated = _run(
            _compose_args(
                projects[1],
                compose_files[1],
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "odoo",
                "-d",
                "postgres",
                "-c",
                f"ALTER ROLE odoo PASSWORD '{rotation_password}'",
            ),
            timeout=30.0,
        )
        assert rotated.returncode == 0, rotated.stderr
        rotated_instance = SimpleNamespace(
            config=InstanceConfig(base_url="http://127.0.0.1", db_password=rotation_password)
        )
        rotated_result = pgadmin.open_pgadmin_lifecycle(
            environment=object(),
            instance=rotated_instance,
            cluster=clusters[1],
            database="smoke_new",
        )
        assert rotated_result.state is PgAdminOpenState.RECONFIGURED
        rotated_container_id = _pgadmin_container_id()
        assert rotated_container_id != new_container_id
        rotated_mode, rotated_active_pgpass = _active_pgpass(rotated_container_id)
        assert rotated_mode == "600"
        assert rotated_active_pgpass == (
            f"{projects[1]}-postgres-1:5432:*:odoo:{rotation_password}\n"
        )
        authenticated_rotated = _pgadmin_passwordless_query(
            rotated_container_id, f"{projects[1]}-postgres-1", "smoke_new"
        )
        assert authenticated_rotated.returncode == 0, authenticated_rotated.stderr
        assert authenticated_rotated.stdout.strip() == "1"
        rejected_old = _pgadmin_rejects_password(
            rotated_container_id,
            f"{projects[1]}-postgres-1",
            "smoke_new",
            passwords[1],
        )
        assert rejected_old.returncode == 0, rejected_old.stderr
        assert rejected_old.stdout.strip() == "rejected"
        assert paths.pgpass.read_text(encoding="utf-8") == (
            f"{projects[1]}-postgres-1:5432:*:odoo:{rotation_password}\n"
        )
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        for project, compose_file in started_projects:
            cleanup = _run(
                _compose_args(project, compose_file, "down", "--volumes", "--remove-orphans"),
                timeout=60.0,
            )
            if primary_failure is None:
                assert cleanup.returncode == 0, cleanup.stderr
        if any(_owned_pgadmin_container(network=network) for network in networks):
            removed = _run(
                ["docker", "rm", "--force", pgadmin_files.PGADMIN_CONTAINER_NAME], timeout=20.0
            )
            if primary_failure is None:
                assert removed.returncode == 0, removed.stderr
