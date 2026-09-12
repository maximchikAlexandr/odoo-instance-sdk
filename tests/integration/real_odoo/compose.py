"""Disposable Compose topology and readiness primitives for real-Odoo tests.

This module is intentionally test-only.  It describes the two PostgreSQL
services and the reference Odoo service used by the harness; the full-tier
target Odoo process remains owned by OdCLI.
"""

from __future__ import annotations

import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .pins import E2E_PINS

PG_READY_TIMEOUT: Final[float] = 90.0
ODOO_READY_TIMEOUT: Final[float] = 180.0


def new_run_id() -> str:
    """Return a cryptographically random, DNS-safe run id."""
    return secrets.token_hex(16)


def _name(prefix: str, run_id: str) -> str:
    return f"odcli-e2e-{prefix}-{run_id}"


@dataclass(frozen=True, slots=True)
class PortReservation:
    """A loopback socket held until the caller is ready to launch a service."""

    socket: socket.socket
    port: int

    def release(self) -> None:
        self.socket.close()


def reserve_loopback_port() -> PortReservation:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return PortReservation(sock, int(sock.getsockname()[1]))


def reserve_ports(count: int = 3) -> tuple[PortReservation, ...]:
    if count < 1:
        raise ValueError("at least one loopback port is required")
    reservations: list[PortReservation] = []
    try:
        for _ in range(count):
            reservations.append(reserve_loopback_port())
    except BaseException:
        for reservation in reservations:
            reservation.release()
        raise
    return tuple(reservations)


@dataclass(frozen=True, slots=True)
class ComposeTopology:
    run_id: str
    project_name: str
    network_name: str
    source_postgres_name: str
    target_postgres_name: str
    source_odoo_name: str
    source_postgres_volume: str
    target_postgres_volume: str
    source_postgres_port: int
    target_postgres_port: int
    source_odoo_port: int
    postgres_image: str = E2E_PINS.postgres_image
    odoo_image: str = E2E_PINS.odoo_image

    @classmethod
    def create(cls, run_id: str, ports: tuple[int, int, int]) -> ComposeTopology:
        if not run_id or any(char not in "0123456789abcdef" for char in run_id.lower()):
            raise ValueError("run_id must be a non-empty hexadecimal string")
        if len(set(ports)) != 3 or any(port < 1 or port > 65535 for port in ports):
            raise ValueError("three distinct valid host ports are required")
        return cls(
            run_id=run_id,
            project_name=_name("project", run_id),
            network_name=_name("network", run_id),
            source_postgres_name=_name("source-pg", run_id),
            target_postgres_name=_name("target-pg", run_id),
            source_odoo_name=_name("source-odoo", run_id),
            source_postgres_volume=_name("source-pg-volume", run_id),
            target_postgres_volume=_name("target-pg-volume", run_id),
            source_postgres_port=ports[0],
            target_postgres_port=ports[1],
            source_odoo_port=ports[2],
        )

    @property
    def label(self) -> str:
        return f"io.odoo-instance-sdk.e2e-run={self.run_id}"

    @property
    def source_database(self) -> str:
        return f"odcli_e2e_source_{self.run_id}"

    @property
    def target_sentinel_database(self) -> str:
        return f"odcli_e2e_sentinel_{self.run_id}"

    @property
    def names(self) -> tuple[str, ...]:
        return (
            self.project_name,
            self.network_name,
            self.source_postgres_name,
            self.target_postgres_name,
            self.source_odoo_name,
            self.source_postgres_volume,
            self.target_postgres_volume,
        )

    def render(
        self,
        *,
        secret_file: Path,
        addon_root: Path,
        source_root: Path,
        source_config: Path | None = None,
        target_config: Path | None = None,
        target_root: Path | None = None,
    ) -> str:
        """Render a pinned, loopback-only Compose file without embedding secrets."""
        secret = str(secret_file)
        addons = str(addon_root)
        source_data = str(source_root)
        config_mount = ""
        config_arg = ""
        if source_config is not None:
            config_mount = f"      - {source_config}:/etc/odoo/odoo.conf:ro\n"
            config_arg = ', "--config=/etc/odoo/odoo.conf"'
        target_mount = ""
        if target_config is not None and target_root is not None:
            target_mount = (
                f"      - {target_config}:/etc/odoo/target.conf:ro\n"
                f"      - {target_root}:/var/lib/odoo-target\n"
            )
        common_labels = (
            f"      io.odoo-instance-sdk.e2e-run: {self.run_id}\n"
            '      io.odoo-instance-sdk.e2e-managed: "true"\n'
        )

        def postgres(name: str, volume: str, port: int) -> str:
            container = (
                self.source_postgres_name
                if name == "source_postgres"
                else self.target_postgres_name
            )
            return (
                f"  {name}:\n"
                f"    image: {self.postgres_image}\n"
                f"    container_name: {container}\n"
                '    restart: "no"\n'
                "    environment:\n"
                "      POSTGRES_USER: odoo\n"
                "      POSTGRES_DB: postgres\n"
                "      POSTGRES_PASSWORD_FILE: /run/secrets/pg_password\n"
                "    secrets:\n"
                "      - pg_password\n"
                "    ports:\n"
                f'      - "127.0.0.1:{port}:5432"\n'
                "    volumes:\n"
                f"      - {volume}:/var/lib/postgresql/data\n"
                "    healthcheck:\n"
                '      test: ["CMD-SHELL", "pg_isready -U odoo -d postgres"]\n'
                "      interval: 2s\n"
                "      timeout: 3s\n"
                "      retries: 45\n"
                "    labels:\n"
                f"{common_labels}"
            )

        source = (
            f"  source_odoo:\n"
            f"    image: {self.odoo_image}\n"
            f"    container_name: {self.source_odoo_name}\n"
            '    restart: "no"\n'
            "    depends_on:\n"
            "      source_postgres:\n"
            "        condition: service_healthy\n"
            "    ports:\n"
            f'      - "127.0.0.1:{self.source_odoo_port}:8069"\n'
            "    volumes:\n"
            f"      - {source_data}:/var/lib/odoo\n"
            f"      - {addons}:/mnt/extra-addons:ro\n"
            f"{config_mount}"
            f"{target_mount}"
            f'    command: ["odoo"{config_arg}, "--without-demo=all"]\n'
            "    labels:\n"
            f"{common_labels}"
        )
        return (
            "services:\n"
            + postgres("source_postgres", self.source_postgres_volume, self.source_postgres_port)
            + postgres("target_postgres", self.target_postgres_volume, self.target_postgres_port)
            + source
            + "networks:\n"
            f"  default:\n    name: {self.network_name}\n    labels:\n{common_labels}"
            "volumes:\n"
            f"  {self.source_postgres_volume}:\n    name: {self.source_postgres_volume}\n    labels:\n"
            f"{common_labels}"
            f"  {self.target_postgres_volume}:\n    name: {self.target_postgres_volume}\n    labels:\n"
            f"{common_labels}"
            "secrets:\n"
            "  pg_password:\n"
            f"    file: {secret}\n"
        )


def wait_for_tcp(host: str, port: int, *, timeout: float = PG_READY_TIMEOUT) -> None:
    _wait_until(
        lambda: _tcp_ready(host, port),
        timeout=timeout,
        description=f"TCP {host}:{port}",
    )


def wait_for_pg_isready(check: Callable[[], bool], *, timeout: float = PG_READY_TIMEOUT) -> None:
    """Wait for a caller-provided ``pg_isready`` probe, not just a socket."""
    _wait_until(check, timeout=timeout, description="PostgreSQL pg_isready")


def wait_for_compose_pg_isready(
    runner: Callable[[tuple[str, ...], float], subprocess.CompletedProcess[str]],
    service: str,
    *,
    timeout: float = PG_READY_TIMEOUT,
) -> None:
    """Poll the container's actual ``pg_isready`` result."""

    def check() -> bool:
        result = runner(
            ("exec", "-T", service, "pg_isready", "-U", "odoo", "-d", "postgres"),
            5.0,
        )
        return result.returncode == 0

    wait_for_pg_isready(check, timeout=timeout)


@dataclass(slots=True)
class ComposeLifecycle:
    """Small test-only Compose boundary with injectable command execution."""

    compose_file: Path
    project_name: str
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run

    def run(self, *args: str, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
        command = (
            "docker",
            "compose",
            "--project-name",
            self.project_name,
            "--file",
            str(self.compose_file),
            *args,
        )
        try:
            result = self.command_runner(
                list(command),
                cwd=self.compose_file.parent,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as error:
            raise RuntimeError("Docker Compose is required for real-Odoo E2E") from error
        if result.returncode and args[0] in {"up", "run"}:
            detail = (result.stdout + result.stderr)[-4000:]
            raise RuntimeError(f"Compose {args[0]} failed: {detail}")
        return result

    @staticmethod
    def available() -> bool:
        return shutil.which("docker") is not None


def wait_for_http(url: str, *, timeout: float = ODOO_READY_TIMEOUT) -> None:
    def ready() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                status = response.status
                return isinstance(status, int) and 200 <= status < 300
        except (OSError, urllib.error.URLError):
            return False

    _wait_until(ready, timeout=timeout, description=f"HTTP {url}")


def _tcp_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _wait_until(check: Callable[[], bool], *, timeout: float, description: str) -> None:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise TimeoutError(f"{description} was not ready within {timeout:g}s")


def iter_loopback_bindings(topology: ComposeTopology) -> Iterator[tuple[str, int]]:
    yield "source_postgres", topology.source_postgres_port
    yield "target_postgres", topology.target_postgres_port
    yield "source_odoo", topology.source_odoo_port
