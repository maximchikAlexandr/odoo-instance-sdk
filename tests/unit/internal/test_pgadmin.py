from __future__ import annotations

import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from odoo_instance_sdk.internal import pgadmin, pgadmin_container, pgadmin_files
from odoo_instance_sdk.models import PgAdminOpenResult, PgAdminOpenState

from .pgadmin_test_support import _paths


def test_open_pgadmin_lifecycle_uses_backend_identity_and_secret_free_docker_args(  # noqa: C901
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(pgadmin_files.PgAdminPaths, "from_defaults", classmethod(lambda cls: paths))
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: tmp_path)
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: False)
    monkeypatch.setattr(pgadmin_container, "_wait_ready", lambda port, *, deadline: None)

    postgres_inspect = {
        "Name": "/odcli_pg_project-postgres-1",
        "Config": {
            "User": "odoo",
            "Labels": {"com.docker.compose.project": "odcli_pg_project"},
        },
        "NetworkSettings": {"Networks": {"odcli_pg_project_default": {}}},
    }

    class Runner:
        requires_docker = False

        def __init__(self) -> None:
            self.calls: list[list[str]] = []
            self.pgadmin_inspect: dict[str, object] | None = None

        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            self.calls.append(args)
            if "ps" in args:
                return subprocess.CompletedProcess(
                    args, 0, '{"Service":"postgres","ID":"pgid"}\n', ""
                )
            if args[1:3] == ["inspect", "--format"] and args[-1] == "pgid":
                return subprocess.CompletedProcess(args, 0, json.dumps([postgres_inspect]), "")
            if args[1:4] == ["network", "inspect", "--format"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    json.dumps([{"Labels": {"com.docker.compose.project": "odcli_pg_project"}}]),
                    "",
                )
            if (
                args[1:3] == ["inspect", "--format"]
                and args[-1] == pgadmin_files.PGADMIN_CONTAINER_NAME
            ):
                return subprocess.CompletedProcess(args, 1, "", "Error: No such object: pgadmin")
            if args[1:3] == ["inspect", "--format"] and args[-1] == "pgadmin-id":
                assert self.pgadmin_inspect is not None
                return subprocess.CompletedProcess(args, 0, json.dumps([self.pgadmin_inspect]), "")
            if args[1:2] == ["exec"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    json.dumps(
                        [
                            {
                                "host": "odcli_pg_project-postgres-1",
                                "port": 5432,
                                "username": "odoo",
                                "maintenance_db": "demo",
                                "db_res": "demo",
                            }
                        ]
                    ),
                    "",
                )
            if args[1:2] == ["run"]:
                labels: dict[str, str] = {}
                for index, value in enumerate(args):
                    if value == "--label":
                        key, label_value = args[index + 1].split("=", 1)
                        labels[key] = label_value
                self.pgadmin_inspect = {
                    "Id": "pgadmin-id",
                    "Config": {"Labels": labels},
                }
                return subprocess.CompletedProcess(args, 0, "pgadmin-id\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

    runner = Runner()
    cluster = type(
        "Cluster",
        (),
        {
            "compose_runner": runner,
            "compose_file": tmp_path / "compose.yaml",
            "compose_project_name": "odcli_pg_project",
        },
    )()
    instance = type(
        "Instance", (), {"config": type("Config", (), {"db_password": "db-secret"})()}
    )()

    result = pgadmin.open_pgadmin_lifecycle(
        environment=object(), instance=instance, cluster=cluster, database="demo"
    )

    assert result.state.value == "started"
    assert result.url == "http://127.0.0.1:5050"
    run_call = next(call for call in runner.calls if call[1] == "run")
    rendered = " ".join(run_call)
    assert "db-secret" not in rendered
    assert "db-secret" not in " ".join(" ".join(call) for call in runner.calls)
    assert "--network" in run_call
    assert "odcli_pg_project_default" in run_call
    assert "--publish" in run_call
    assert "127.0.0.1:5050:80" in run_call
    assert "--user" in run_call and "5050" in run_call
    assert "--mount" in run_call
    assert "docker.sock" not in rendered
    assert "MaintenanceDB" in paths.servers_json.read_text()
    assert '"MaintenanceDB":"demo"' in paths.servers_json.read_text()
    assert '"DBRestriction":"demo"' in paths.servers_json.read_text()


def test_concurrent_lifecycle_calls_create_one_container_at_boundary(  # noqa: C901
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(pgadmin_files.PgAdminPaths, "from_defaults", classmethod(lambda cls: paths))
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: tmp_path)
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: False)
    monkeypatch.setattr(pgadmin_container, "_wait_ready", lambda *_args, **_kwargs: None)

    postgres_inspect = {
        "Name": "/odcli_pg_project-postgres-1",
        "Config": {
            "User": "odoo",
            "Labels": {"com.docker.compose.project": "odcli_pg_project"},
        },
        "NetworkSettings": {"Networks": {"odcli_pg_project_default": {}}},
    }

    class Runner:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []
            self.create_count = 0
            self._container: dict[str, object] | None = None
            self._lock = threading.Lock()

        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:  # noqa: C901
            with self._lock:
                self.calls.append(args)
                if "ps" in args:
                    return subprocess.CompletedProcess(
                        args, 0, '{"Service":"postgres","ID":"pgid"}\n', ""
                    )
                if args[1:3] == ["inspect", "--format"] and args[-1] == "pgid":
                    return subprocess.CompletedProcess(args, 0, json.dumps([postgres_inspect]), "")
                if args[1:4] == ["network", "inspect", "--format"]:
                    return subprocess.CompletedProcess(
                        args,
                        0,
                        json.dumps(
                            [{"Labels": {"com.docker.compose.project": "odcli_pg_project"}}]
                        ),
                        "",
                    )
                if args[1:3] == ["inspect", "--format"]:
                    if self._container is None:
                        return subprocess.CompletedProcess(args, 1, "", "No such object")
                    return subprocess.CompletedProcess(args, 0, json.dumps([self._container]), "")
                if args[1:2] == ["run"]:
                    self.create_count += 1
                    labels: dict[str, str] = {}
                    for index, value in enumerate(args):
                        if value == "--label":
                            key, label_value = args[index + 1].split("=", 1)
                            labels[key] = label_value
                    mounts: list[dict[str, object]] = []
                    for index, value in enumerate(args):
                        if value == "--mount":
                            mount = args[index + 1].removeprefix("type=bind,")
                            fields = dict(
                                item.split("=", 1) for item in mount.split(",") if "=" in item
                            )
                            mounts.append(
                                {
                                    "Source": fields["source"],
                                    "Destination": fields["destination"],
                                    "RW": "readonly" not in mount,
                                }
                            )
                    port = next(value for value in args if value.startswith("127.0.0.1:"))
                    host_port = port.split(":", 2)[1]
                    network = args[args.index("--network") + 1]
                    self._container = {
                        "Id": "pgadmin-id",
                        "Name": f"/{pgadmin_files.PGADMIN_CONTAINER_NAME}",
                        "Config": {
                            "Image": pgadmin_files.PGADMIN_IMAGE,
                            "User": str(pgadmin_files.PGADMIN_RUNTIME_UID),
                            "Env": [
                                "PGADMIN_CONFIG_SERVER_MODE=False",
                                "PGADMIN_REPLACE_SERVERS_ON_STARTUP=True",
                                f"PGADMIN_DEFAULT_EMAIL={pgadmin_files.PGADMIN_DEFAULT_EMAIL}",
                                f"PGADMIN_DEFAULT_PASSWORD_FILE={pgadmin_files.PGADMIN_PASSWORD_DESTINATION}",
                                f"PGPASS_FILE={pgadmin_files.PGADMIN_PGPASS_DESTINATION}",
                            ],
                            "Labels": labels,
                        },
                        "State": {"Running": True},
                        "NetworkSettings": {"Networks": {network: {}}},
                        "Mounts": mounts,
                        "HostConfig": {
                            "PortBindings": {
                                "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": host_port}]
                            }
                        },
                    }
                    return subprocess.CompletedProcess(args, 0, "pgadmin-id\n", "")
                if args[1:2] == ["exec"]:
                    return subprocess.CompletedProcess(
                        args,
                        0,
                        json.dumps(
                            [
                                {
                                    "host": "odcli_pg_project-postgres-1",
                                    "port": 5432,
                                    "username": "odoo",
                                    "maintenance_db": "demo",
                                    "db_res": "demo",
                                }
                            ]
                        ),
                        "",
                    )
                pytest.fail(f"unexpected docker argv: {args}")

    runner = Runner()
    cluster = type(
        "Cluster",
        (),
        {
            "compose_runner": runner,
            "compose_file": tmp_path / "compose.yaml",
            "compose_project_name": "odcli_pg_project",
        },
    )()
    instance = type("Instance", (), {"config": type("Config", (), {"db_password": "secret"})()})()
    start = threading.Barrier(2)

    def invoke() -> PgAdminOpenResult:
        start.wait()
        return pgadmin.open_pgadmin_lifecycle(
            environment=object(), instance=instance, cluster=cluster, database="demo"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(), range(2)))

    assert [result.state for result in results].count(PgAdminOpenState.STARTED) == 1
    assert [result.state for result in results].count(PgAdminOpenState.REUSED) == 1
    assert runner.create_count == 1
    assert not any(call[1:2] == ["rm"] for call in runner.calls)
