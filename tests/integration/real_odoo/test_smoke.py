"""Container-only public smoke path for the real-Odoo PR tier."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.project import ProjectConfig

from .cleanup import write_odoo_config, write_owner_only_secret
from .compose import ComposeLifecycle, reserve_ports, wait_for_http
from .conftest import E2ERuntime
from .pins import E2E_PINS

pytestmark = [pytest.mark.real_odoo, pytest.mark.e2e_smoke, pytest.mark.serial]


def _invoke(
    runner: CliRunner,
    project: Path,
    environment: dict[str, str],
    *args: str,
    command_project: bool = False,
) -> dict[str, Any]:
    command = [*args]
    if command_project:
        command.extend(("--project", str(project)))
    else:
        command = ["--project", str(project), *command]
    result = runner.invoke(
        cli,
        [*command, "--format", "json"],
        env=environment,
    )
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document.get("ok") is True, document
    payload = document.get("result")
    assert isinstance(payload, dict), document
    return payload


def _record(record_property: Any, evidence: str, value: object) -> None:
    record_property(evidence.lower().replace("-", "_"), json.dumps(value, default=str))


def _git_project(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "init", "--quiet", str(path)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _replace_http_port(config: Path, port: int) -> None:
    lines = [
        line
        for line in config.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("http_port")
    ]
    lines.append(f"http_port = {port}")
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_target_proxy(project: Path, target_url: str, auxiliary_port: int) -> Path:
    """Proxy the owned container Odoo through the project runtime boundary."""
    proxy = project / "smoke-odoo-proxy.py"
    proxy.write_text(
        """from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

TARGET = __TARGET_URL__.rstrip(\"/\")


class Proxy(BaseHTTPRequestHandler):
    def _forward(self):
        length = int(self.headers.get(\"Content-Length\", \"0\"))
        body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {\"host\", \"connection\", \"content-length\"}
        }
        request = Request(TARGET + self.path, data=body, headers=headers, method=self.command)
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() not in {\"connection\", \"transfer-encoding\", \"content-length\"}:
                    self.send_header(key, value)
            self.send_header(\"Content-Length\", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    do_GET = _forward
    do_POST = _forward
    do_HEAD = _forward


if __name__ == \"__main__\":
    ThreadingHTTPServer((\"127.0.0.1\", __AUXILIARY_PORT__), Proxy).serve_forever()
""".replace("__TARGET_URL__", repr(target_url)).replace("__AUXILIARY_PORT__", str(auxiliary_port)),
        encoding="utf-8",
    )
    proxy.chmod(0o700)
    (project / ".env").write_text(
        f"ODCLI_SMOKE_TARGET_URL={target_url}\nODCLI_SMOKE_AUXILIARY_PORT={auxiliary_port}\n",
        encoding="utf-8",
    )
    (project / ".env").chmod(0o600)
    return proxy


def _start_target_odoo(runtime: E2ERuntime) -> None:
    compose_file = runtime.compose_file
    rendered = compose_file.read_text(encoding="utf-8")
    marker = "  target_init:\n"
    if marker not in rendered:
        raise AssertionError("target Compose service is missing")
    rendered = rendered.replace(
        marker,
        f'{marker}    ports:\n      - "127.0.0.1:{runtime.reservations[3].port}:8069"\n',
        1,
    )
    for argument in (
        f', "--database={runtime.topology.target_sentinel_database}"',
        ', "--init=base"',
        ', "--stop-after-init"',
    ):
        if argument not in rendered:
            raise AssertionError("target Compose service is not a bootstrap command")
        rendered = rendered.replace(argument, "", 1)
    compose_file.write_text(rendered, encoding="utf-8")
    runtime.reservations[3].release()
    result = ComposeLifecycle(compose_file, runtime.topology.project_name).run(
        "up",
        "--detach",
        "target_init",
    )
    assert result.returncode == 0, result.stderr
    lifecycle = ComposeLifecycle(compose_file, runtime.topology.project_name)
    try:
        # Keep a bounded reserve inside the frozen 180-second test budget for
        # diagnostics.  A timeout must explain whether Odoo exited or simply
        # failed its HTTP contract; it must never become a silent pytest kill.
        wait_for_http(
            f"http://127.0.0.1:{runtime.reservations[3].port}/web/health",
            # pytest-timeout includes fixture setup in this test.  Keep a
            # measured reserve for status/log collection rather than letting
            # the outer 180s budget kill the diagnostic path.
            timeout=90.0,
        )
        wait_for_http(
            f"http://127.0.0.1:{runtime.reservations[3].port}/web/database/selector",
            timeout=15.0,
        )
    except TimeoutError as error:
        status = lifecycle.run(
            "ps",
            "--all",
            "--format",
            "{{.Service}} {{.State}}",
            "target_init",
            timeout=4.0,
        )
        logs = lifecycle.run(
            "logs",
            "--no-color",
            "--tail",
            "200",
            "target_init",
            timeout=5.0,
        )
        detail = (logs.stdout + logs.stderr)[-6000:]
        secrets = (
            runtime.secret_file.read_text(encoding="utf-8").strip(),
            runtime.master_password_file.read_text(encoding="utf-8").strip(),
        )
        for secret in secrets:
            if secret:
                detail = detail.replace(secret, "<redacted>")
        raise RuntimeError(
            f"target Odoo readiness failed: {error}; service status: "
            f"{(status.stdout + status.stderr).strip()}; logs:\n{detail}"
        ) from error


def _align_target_master_password(runtime: E2ERuntime, source: E2ERuntime) -> None:
    """Give the local target and remote source one explicit test credential."""
    master_password = source.master_password_file.read_text(encoding="utf-8").strip()
    database_password = runtime.secret_file.read_text(encoding="utf-8").strip()
    write_owner_only_secret(runtime.master_password_file, master_password)
    write_odoo_config(
        runtime.container_config_file,
        database_host="target_postgres",
        database_port=5432,
        database_password=database_password,
        admin_password=master_password,
        data_dir=Path("/var/lib/odoo-target"),
        addons_path=(Path("/usr/lib/python3/dist-packages/odoo/addons"),),
        mode=0o644,
    )
    write_odoo_config(
        runtime.config_file,
        database_host="127.0.0.1",
        database_port=runtime.topology.target_postgres_port,
        database_password=database_password,
        admin_password=master_password,
        data_dir=runtime.root / "target-data",
        http_interface="127.0.0.1",
        http_port=runtime.reservations[3].port,
    )


@pytest.mark.timeout(180, func_only=True)
def test_container_smoke_public_path(
    source_server: E2ERuntime,
    target_runtime: E2ERuntime,
    record_property: Any,
) -> None:
    """Exercise E2E-SM-01..05 through one disposable image-backed workflow."""
    runtime = target_runtime
    _align_target_master_password(runtime, source_server)
    _start_target_odoo(runtime)
    project = runtime.root / f"smoke-project-{runtime.run_id}"
    _git_project(project)
    auxiliary_http = reserve_ports(1)[0]
    auxiliary_http_port = auxiliary_http.port
    runtime.ledger.record(
        "port",
        f"{runtime.run_id}-smoke-http-{auxiliary_http_port}",
        auxiliary_http.release,
    )
    auxiliary_http.release()
    config = project / "smoke-odoo.conf"
    shutil.copy2(runtime.config_file, config)
    _replace_http_port(config, auxiliary_http_port)
    proxy = _write_target_proxy(
        project,
        f"http://127.0.0.1:{runtime.reservations[3].port}",
        auxiliary_http_port,
    )
    environment = dict(os.environ)
    environment.update(runtime.environment)
    environment.update(
        {
            "ODCLI_TEST_MASTER_PASSWORD": source_server.master_password_file.read_text(
                encoding="utf-8"
            ).strip(),
            "ODCLI_TEST_INSTANCE_ORIGIN_PINS": (
                f"http://127.0.0.1:{source_server.topology.source_odoo_port}"
            ),
        }
    )
    runner = CliRunner()

    initialized = _invoke(
        runner,
        project,
        environment,
        "init",
        "--no-input",
        "--odoo-bin",
        str(proxy),
        "--python",
        sys.executable,
        "--config",
        str(config),
        "--database",
        runtime.topology.target_sentinel_database,
        command_project=True,
    )
    manifest_path = project / ".odcli" / "project.toml"
    assert manifest_path.is_file()
    loaded = ProjectConfig.load(project)
    from odoo_instance_sdk.project import TestInstanceProjectConfig

    loaded = msgspec.structs.replace(
        loaded,
        test_instance=TestInstanceProjectConfig(
            base_url=f"http://127.0.0.1:{source_server.topology.source_odoo_port}",
            database=source_server.topology.source_database,
            git_branch=E2E_PINS.odoo_source_commit,
        ),
        default_base_ref=E2E_PINS.odoo_source_commit,
    )
    manifest_path.write_text(loaded.to_manifest(), encoding="utf-8")
    _record(
        record_property,
        "E2E-SM-01",
        {
            "manifest": manifest_path,
            "init": initialized,
            "target_compose_project": target_runtime.topology.project_name,
            "target_database_manager": (f"http://127.0.0.1:{target_runtime.reservations[3].port}"),
        },
    )

    refreshed = _invoke(
        runner,
        project,
        environment,
        "db",
        "refresh",
        "--restore",
        "--source-branch",
        E2E_PINS.odoo_source_commit,
    )
    restored_database = refreshed.get("restored_database")
    backup = refreshed.get("backup")
    assert isinstance(restored_database, str) and restored_database
    assert isinstance(backup, dict) and isinstance(backup.get("id"), str)
    restored_filestore = runtime.root / "target-data" / "filestore" / restored_database
    assert restored_filestore.is_dir()
    assert any(path.is_file() and path.stat().st_size > 0 for path in restored_filestore.rglob("*"))
    assert runtime.environment["ODCLI_E2E_CATALOG"].startswith(str(runtime.root))
    _record(record_property, "E2E-SM-02", {"database": restored_database, "backup": backup})

    validated = _invoke(runner, project, environment, "backup", "validate", str(backup["id"]))
    assert validated
    _record(record_property, "E2E-SM-03", validated)

    databases = _invoke(runner, project, environment, "db", "list")
    assert restored_database in json.dumps(databases)
    _record(record_property, "E2E-SM-04", databases)

    health = _invoke(runner, project, environment, "resource", "doctor")
    findings = health.get("findings")
    assert isinstance(findings, list)
    _record(record_property, "E2E-SM-05", health)
