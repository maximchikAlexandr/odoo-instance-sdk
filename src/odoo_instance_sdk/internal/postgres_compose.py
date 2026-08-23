from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from odoo_instance_sdk.exceptions import (
    PostgresClusterStartError,
    PostgresClusterStopError,
    PostgresClusterTimeoutError,
    PostgresComposeInvalidError,
    PostgresComposeUnavailableError,
    PostgresPortCollisionError,
)
from odoo_instance_sdk.models import PostgresClusterState

_PROJECT_NAME_PREFIX = "odcli_pg_"

# Limited charset to keep generated YAML text safe without a YAML emitter.
_IMAGE_RE = re.compile(r"^[A-Za-z0-9._/:@+-]+$")
_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ComposeResult:
    """Result of a single compose CLI invocation (no msgspec dependency here)."""

    __slots__ = ("returncode", "stderr", "stdout")

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ComposeRunner:
    """Protocol-like seam: tests inject a fake; production uses ``SubprocessComposeRunner``.

    We keep this a regular class (not ``typing.Protocol``) so tests can subclass
    and so that ``PostgresCluster`` can accept it without runtime ``isinstance``
    gymnastics against a Protocol.
    """

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> ComposeResult:
        raise NotImplementedError


class SubprocessComposeRunner(ComposeRunner):
    """Default compose runner backed by ``subprocess.run``.

    The first argument is expected to be the executable (``docker``). We never
    pass secrets through the command line — secrets are file-backed.
    """

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> ComposeResult:
        proc = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return ComposeResult(proc.returncode, proc.stdout, proc.stderr)


def docker_available() -> bool:
    return shutil.which("docker") is not None


def compose_project_name(project_id: str) -> str:
    return f"{_PROJECT_NAME_PREFIX}{project_id}"


def compose_volume_name(project_id: str) -> str:
    return f"pgdata_{project_id}"


def assert_image_safe(image: str) -> None:
    if not image or not _IMAGE_RE.fullmatch(image):
        raise PostgresComposeInvalidError(f"invalid postgres image: {image!r}")


def assert_user_safe(user: str) -> None:
    if not user or not _USER_RE.fullmatch(user):
        raise PostgresComposeInvalidError(f"invalid postgres user: {user!r}")


def render_compose_yaml(
    *,
    image: str,
    port: int,
    user: str,
    project_id: str,
    password_file: str,
) -> str:
    assert_image_safe(image)
    assert_user_safe(user)
    volume = compose_volume_name(project_id)
    # Compose secret path inside container is fixed; the host file is mounted
    # by Docker via the secrets section.
    return (
        "services:\n"
        "  postgres:\n"
        f"    image: {image}\n"
        "    ports:\n"
        f'      - "127.0.0.1:{port}:5432"\n'
        "    environment:\n"
        f"      POSTGRES_USER: {user}\n"
        "      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password\n"
        "      PGDATA: /var/lib/postgresql/data\n"
        "    volumes:\n"
        "      - pgdata:/var/lib/postgresql/data\n"
        "    secrets:\n"
        "      - postgres_password\n"
        "    healthcheck:\n"
        f'      test: ["CMD-SHELL", "pg_isready -U {user} -d postgres"]\n'
        "      interval: 2s\n"
        "      timeout: 3s\n"
        "      retries: 30\n"
        "      start_period: 5s\n"
        "secrets:\n"
        "  postgres_password:\n"
        f"    file: {password_file}\n"
        "volumes:\n"
        f"  pgdata:\n"
        f"    name: {volume}\n"
    )


def ensure_password_file(path: Path) -> str:
    """Create the password file with mode 0600 if missing; never overwrite existing."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(32)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".pgpw-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(password)
            f.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    os.chmod(path, 0o600)
    return password


def write_compose_file_atomic(
    compose_path: Path,
    content: str,
    *,
    runner: ComposeRunner,
    project_name: str,
) -> None:
    """Validate then atomically publish ``compose.yaml`` with mode 0600."""
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(compose_path.parent), prefix=".compose-", suffix=".yaml.tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_path, 0o600)
        compose_config(runner, tmp_path, project_name)
        os.replace(tmp_path, compose_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    os.chmod(compose_path, 0o600)


def _compose_base_args(
    compose_file: Path,
    project_name: str,
) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "-f",
        str(compose_file),
    ]


def compose_config(
    runner: ComposeRunner,
    compose_file: Path,
    project_name: str,
) -> None:
    """Validate the generated compose file via ``docker compose config --quiet``."""
    args = [*_compose_base_args(compose_file, project_name), "config", "--quiet"]
    res = runner.run(args, cwd=compose_file.parent)
    if res.returncode != 0:
        raise PostgresComposeInvalidError(
            f"docker compose config failed: {res.stderr.strip() or res.stdout.strip()}"
        )


def compose_up(
    runner: ComposeRunner,
    compose_file: Path,
    project_name: str,
    *,
    timeout: float | None = None,
) -> None:
    args = [*_compose_base_args(compose_file, project_name), "up", "--detach", "--wait"]
    try:
        res = runner.run(args, cwd=compose_file.parent, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise PostgresClusterTimeoutError(timeout or 0.0) from exc
    if res.returncode != 0:
        if _looks_like_port_collision(res.stderr):
            raise PostgresPortCollisionError(
                "configured port is not free at compose up; rerun init with --postgres-port"
            )
        raise PostgresClusterStartError(
            f"docker compose up failed: {res.stderr.strip() or res.stdout.strip()}",
            returncode=res.returncode,
        )


def _looks_like_port_collision(stderr: str) -> bool:
    lowered = stderr.lower()
    return "bind: address already in use" in lowered or "port is already allocated" in lowered


def compose_stop(
    runner: ComposeRunner,
    compose_file: Path,
    project_name: str,
    *,
    timeout: float | None = None,
) -> None:
    """Stop the compose project; preserves the named volume (never ``down -v``)."""
    args = [
        *_compose_base_args(compose_file, project_name),
        "stop",
        "--timeout",
        str(int(max(1, timeout)) if timeout is not None else 30),
    ]
    res = runner.run(args, cwd=compose_file.parent, timeout=None)
    if res.returncode != 0:
        raise PostgresClusterStopError(
            f"docker compose stop failed: {res.stderr.strip() or res.stdout.strip()}",
            returncode=res.returncode,
        )


def compose_ps(
    runner: ComposeRunner,
    compose_file: Path,
    project_name: str,
) -> list[dict[str, object]] | None:
    """Return parsed ``docker compose ps --format json`` rows, or None on CLI failure."""
    args = [*_compose_base_args(compose_file, project_name), "ps", "--format", "json"]
    res = runner.run(args, cwd=compose_file.parent)
    if res.returncode != 0:
        return None
    rows: list[dict[str, object]] = []
    for raw in res.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def compose_health(
    runner: ComposeRunner,
    compose_file: Path,
    project_name: str,
    *,
    user: str,
) -> tuple[int, str]:
    """Run ``pg_isready`` inside the postgres service container."""
    args = [
        *_compose_base_args(compose_file, project_name),
        "exec",
        "-T",
        "postgres",
        "pg_isready",
        "-U",
        user,
        "-d",
        "postgres",
    ]
    res = runner.run(args, cwd=compose_file.parent)
    return res.returncode, (res.stdout + res.stderr).strip()


def derive_state(
    runner: ComposeRunner,
    compose_file: Path,
    project_name: str,
    *,
    user: str,
) -> PostgresClusterState:
    rows = compose_ps(runner, compose_file, project_name)
    if rows is None:
        return PostgresClusterState.UNKNOWN
    if not rows:
        return PostgresClusterState.STOPPED
    rc, _output = compose_health(runner, compose_file, project_name, user=user)
    if rc == 0:
        return PostgresClusterState.HEALTHY
    if any(str(row.get("Health", "")).lower() == "unhealthy" for row in rows):
        return PostgresClusterState.UNHEALTHY
    return PostgresClusterState.STARTING


def ensure_docker_or_raise() -> None:
    if not docker_available():
        raise PostgresComposeUnavailableError("docker CLI not found on PATH")
