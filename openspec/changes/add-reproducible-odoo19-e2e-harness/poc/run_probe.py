"""Disposable MYL-149 vertical-slice probe; this is evidence, not production code."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import xmlrpc.client
import zipfile
from pathlib import Path

ODOO_IMAGE = (
    "docker.io/library/odoo@sha256:a627eda6b4154eead21c4fca55f84f1671d870ca111aa57f93ca305861bc4613"
)
POSTGRES_IMAGE = "docker.io/library/postgres@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"
DB_NAME = "odcli_poc"


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, capture_output=True, env=env, check=False)
    if result.returncode:
        raise RuntimeError(
            f"{args[0]} failed with exit {result.returncode}: {(result.stdout + result.stderr)[-10000:]}"
        )
    return result


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_tcp(port: int, *, container: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            state = run(
                "docker", "inspect", "--format", "{{.State.Running}}", container
            ).stdout.strip()
            if state != "true":
                logs = subprocess.run(
                    ("docker", "logs", "--tail", "80", container),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                raise RuntimeError((logs.stdout + logs.stderr)[-10000:])
            time.sleep(0.25)
    raise TimeoutError(f"port {port} was not ready")


def wait_postgres(container: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ("docker", "exec", container, "pg_isready", "-U", "odoo", "-d", "postgres"),
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise TimeoutError(f"PostgreSQL container {container} was not ready")


def wait_http(port: int, *, container: str, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/web/health", timeout=2):
                return
        except OSError:
            state = run(
                "docker", "inspect", "--format", "{{.State.Running}}", container
            ).stdout.strip()
            if state != "true":
                logs = subprocess.run(
                    ("docker", "logs", "--tail", "120", container),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                raise RuntimeError((logs.stdout + logs.stderr)[-10000:])
            time.sleep(0.5)
    raise TimeoutError(f"Odoo on port {port} was not ready")


def write_config(
    path: Path,
    *,
    host: str,
    port: int,
    password: str,
    admin: str,
    data: str,
    http_host: str,
    http_port: int,
) -> None:
    path.write_text(
        "[options]\n"
        f"admin_passwd = {admin}\n"
        f"db_host = {host}\n"
        f"db_port = {port}\n"
        "db_user = odoo\n"
        f"db_password = {password}\n"
        f"data_dir = {data}\n"
        f"http_interface = {http_host}\n"
        f"http_port = {http_port}\n"
        "list_db = True\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def main() -> None:
    addon_root = Path(__file__).parent / "addons"
    user_spec = f"{os.getuid()}:{os.getgid()}"
    run_id = secrets.token_hex(6)
    names = {
        key: f"odcli-poc-{key}-{run_id}"
        for key in ("net", "src-pg", "src-pg-vol", "src", "dst-pg", "dst-pg-vol", "dst")
    }
    started: list[str] = []
    started_at = time.monotonic()
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix=".runtime-") as raw:
        root = Path(raw)
        pg_password = secrets.token_urlsafe(24)
        master_password = secrets.token_urlsafe(24)
        secret_file = root / "pg-password"
        secret_file.write_text(pg_password, encoding="utf-8")
        secret_file.chmod(0o600)
        source_data = root / "source-data"
        target_data = root / "target-data"
        source_data.mkdir()
        target_data.mkdir()
        source_etc = root / "source-etc"
        target_etc = root / "target-etc"
        source_etc.mkdir()
        target_etc.mkdir()
        source_config = source_etc / "odoo.conf"
        target_container_config = target_etc / "odoo.conf"
        write_config(
            source_config,
            host=names["src-pg"],
            port=5432,
            password=pg_password,
            admin=master_password,
            data="/var/lib/odoo",
            http_host="0.0.0.0",
            http_port=8069,
        )
        write_config(
            target_container_config,
            host=names["dst-pg"],
            port=5432,
            password=pg_password,
            admin=master_password,
            data="/var/lib/odoo",
            http_host="0.0.0.0",
            http_port=8069,
        )
        try:
            run("docker", "network", "create", names["net"])
            run("docker", "volume", "create", names["src-pg-vol"])
            run("docker", "volume", "create", names["dst-pg-vol"])
            pg_ports: dict[str, int] = {}
            for key in ("src-pg", "dst-pg"):
                pg_ports[key] = free_port()
                pg_volume = names["src-pg-vol"] if key == "src-pg" else names["dst-pg-vol"]
                run(
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    names[key],
                    "--network",
                    names["net"],
                    "-p",
                    f"127.0.0.1:{pg_ports[key]}:5432",
                    "-e",
                    "POSTGRES_USER=odoo",
                    "-e",
                    "POSTGRES_DB=postgres",
                    "-e",
                    "POSTGRES_PASSWORD_FILE=/run/secrets/pg_password",
                    "-v",
                    f"{secret_file}:/run/secrets/pg_password:ro",
                    "-v",
                    f"{pg_volume}:/var/lib/postgresql/data",
                    POSTGRES_IMAGE,
                )
                started.append(names[key])
                wait_tcp(pg_ports[key], container=names[key])
                wait_postgres(names[key])

            source_port = free_port()
            run(
                "docker",
                "run",
                "--rm",
                "--network",
                names["net"],
                "--user",
                user_spec,
                "-v",
                f"{source_etc}:/etc/odoo:ro",
                "-v",
                f"{source_data}:/var/lib/odoo",
                "-v",
                f"{addon_root}:/mnt/extra-addons:ro",
                "--entrypoint",
                "/usr/bin/odoo",
                ODOO_IMAGE,
                "server",
                "-c",
                "/etc/odoo/odoo.conf",
                "--addons-path",
                "/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons",
                "-d",
                DB_NAME,
                "-i",
                "odcli_e2e_probe",
                "--without-demo=all",
                "--stop-after-init",
            )
            run(
                "docker",
                "run",
                "-d",
                "--name",
                names["src"],
                "--network",
                names["net"],
                "--user",
                user_spec,
                "-p",
                f"127.0.0.1:{source_port}:8069",
                "-v",
                f"{source_etc}:/etc/odoo:ro",
                "-v",
                f"{source_data}:/var/lib/odoo",
                "-v",
                f"{addon_root}:/mnt/extra-addons:ro",
                "--entrypoint",
                "/usr/bin/odoo",
                ODOO_IMAGE,
                "server",
                "-c",
                "/etc/odoo/odoo.conf",
                "--addons-path",
                "/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons",
                "-d",
                DB_NAME,
            )
            started.append(names["src"])
            wait_http(source_port, container=names["src"])

            run(
                "docker",
                "run",
                "--rm",
                "--network",
                names["net"],
                "--user",
                user_spec,
                "-v",
                f"{target_etc}:/etc/odoo:ro",
                "-v",
                f"{target_data}:/var/lib/odoo",
                "--entrypoint",
                "/usr/bin/odoo",
                ODOO_IMAGE,
                "server",
                "-c",
                "/etc/odoo/odoo.conf",
                "-d",
                "odcli_poc_sentinel",
                "-i",
                "base",
                "--without-demo=True",
                "--stop-after-init",
            )
            target_http_port = free_port()
            run(
                "docker",
                "run",
                "-d",
                "--name",
                names["dst"],
                "--network",
                names["net"],
                "--user",
                user_spec,
                "-p",
                f"127.0.0.1:{target_http_port}:8069",
                "-v",
                f"{target_etc}:/etc/odoo:ro",
                "-v",
                f"{target_data}:/var/lib/odoo",
                "-v",
                f"{addon_root}:/mnt/extra-addons:ro",
                "--entrypoint",
                "/usr/bin/odoo",
                ODOO_IMAGE,
                "server",
                "-c",
                "/etc/odoo/odoo.conf",
                "--addons-path",
                "/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons",
            )
            started.append(names["dst"])
            wait_tcp(target_http_port, container=names["dst"])
            target_pg_port = pg_ports["dst-pg"]
            target_host_config = root / "target-host.conf"
            write_config(
                target_host_config,
                host="127.0.0.1",
                port=target_pg_port,
                password=pg_password,
                admin=master_password,
                data=str(target_data),
                http_host="127.0.0.1",
                http_port=target_http_port,
            )

            project = root / "project"
            project.mkdir()
            run("git", "init", "-b", "main", str(project))
            (project / "README.md").write_text("poc\n", encoding="utf-8")
            run("git", "-C", str(project), "add", "README.md")
            run(
                "git",
                "-C",
                str(project),
                "-c",
                "user.name=OdCLI POC",
                "-c",
                "user.email=poc@example.invalid",
                "commit",
                "-m",
                "chore: seed poc",
            )
            env = dict(os.environ)
            for key in ("DATA", "CONFIG", "CACHE"):
                env[f"XDG_{key}_HOME"] = str(root / f"xdg-{key.lower()}")
            run(
                "uv",
                "run",
                "odcli",
                "init",
                "--no-input",
                "--project",
                str(project),
                "--odoo-bin",
                "/usr/bin/true",
                "--python",
                shutil.which("python3") or "python3",
                "--config",
                str(target_host_config),
                "--database",
                DB_NAME,
                env=env,
            )
            manifest = project / ".odcli" / "project.toml"
            with manifest.open("a", encoding="utf-8") as stream:
                stream.write(
                    f'\n[test_instance]\nbase_url = "http://127.0.0.1:{source_port}"\ndatabase = "{DB_NAME}"\ngit_branch = "19.0"\n'
                )
            dotenv = project / ".odcli" / ".env"
            dotenv.write_text(f"ODCLI_TEST_MASTER_PASSWORD={master_password}\n", encoding="utf-8")
            dotenv.chmod(0o600)
            try:
                refresh = run(
                    "uv",
                    "run",
                    "odcli",
                    "--project",
                    str(project),
                    "db",
                    "refresh",
                    "--restore",
                    "--format",
                    "json",
                    env=env,
                )
            except RuntimeError as error:
                logs = subprocess.run(
                    ("docker", "logs", "--tail", "120", names["dst"]),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                raise RuntimeError(
                    f"{error}\ntarget Odoo:\n{(logs.stdout + logs.stderr)[-10000:]}"
                ) from None
            result = json.loads(refresh.stdout)["result"]
            backup_path = Path(result["backup"]["path"])
            with zipfile.ZipFile(backup_path) as archive:
                filestore_members = [
                    name
                    for name in archive.namelist()
                    if name.startswith("filestore/") and not name.endswith("/")
                ]
            run("docker", "rm", "-f", names["dst"])
            started.remove(names["dst"])
            run(
                "docker",
                "run",
                "-d",
                "--name",
                names["dst"],
                "--network",
                names["net"],
                "--user",
                user_spec,
                "-p",
                f"127.0.0.1:{target_http_port}:8069",
                "-v",
                f"{target_etc}:/etc/odoo:ro",
                "-v",
                f"{target_data}:/var/lib/odoo",
                "-v",
                f"{addon_root}:/mnt/extra-addons:ro",
                "--entrypoint",
                "/usr/bin/odoo",
                ODOO_IMAGE,
                "server",
                "-c",
                "/etc/odoo/odoo.conf",
                "--addons-path",
                "/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons",
                "-d",
                result["restored_database"],
            )
            started.append(names["dst"])
            wait_http(target_http_port, container=names["dst"])
            common = xmlrpc.client.ServerProxy(
                f"http://127.0.0.1:{target_http_port}/xmlrpc/2/common"
            )
            uid = common.authenticate(result["restored_database"], "admin", "admin", {})
            models = xmlrpc.client.ServerProxy(
                f"http://127.0.0.1:{target_http_port}/xmlrpc/2/object"
            )
            probe = models.execute_kw(
                result["restored_database"],
                uid,
                "admin",
                "odcli.e2e.probe",
                "search_read",
                [[("marker", "=", "ODCLI-E2E-RESTORED")]],
                {"fields": ["name", "marker"], "limit": 1},
            )
            attachment = models.execute_kw(
                result["restored_database"],
                uid,
                "admin",
                "ir.attachment",
                "search_read",
                [[("name", "=", "odcli-e2e-attachment.txt")]],
                {"fields": ["datas", "store_fname"], "limit": 1},
            )
            assert (
                probe
                and attachment
                and attachment[0]["datas"] == "T2RDTGkgZmlsZXN0b3JlIHByb2JlCg=="
            )
            assert filestore_members and attachment[0]["store_fname"]
            print(
                json.dumps(
                    {
                        "backup_format": result["backup"]["format"],
                        "backup_sha256": result["backup"]["sha256"],
                        "filestore_members": len(filestore_members),
                        "fixture_marker": probe[0]["marker"],
                        "restored_database": result["restored_database"],
                        "elapsed_seconds": round(time.monotonic() - started_at, 3),
                    },
                    sort_keys=True,
                )
            )
        finally:
            for name in reversed(started):
                subprocess.run(("docker", "rm", "-f", "-v", name), capture_output=True, check=False)
            subprocess.run(
                ("docker", "network", "rm", names["net"]), capture_output=True, check=False
            )
            for key in ("src-pg-vol", "dst-pg-vol"):
                subprocess.run(
                    ("docker", "volume", "rm", names[key]), capture_output=True, check=False
                )


if __name__ == "__main__":
    main()
