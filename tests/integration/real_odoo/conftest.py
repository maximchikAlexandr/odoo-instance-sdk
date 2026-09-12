"""Self-provisioning fixtures shared by real-Odoo E2E scenarios."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pytest

from .archive import ArchiveIdentity, SourceBackupPlan, source_database_name
from .cleanup import (
    FailureEvidence,
    ResourceLedger,
    audit_no_leaks,
    compose_down,
    remove_owned_root,
    write_odoo_config,
    write_owner_only_secret,
)
from .compose import (
    ComposeLifecycle,
    ComposeTopology,
    PortReservation,
    new_run_id,
    reserve_ports,
    wait_for_compose_pg_isready,
    wait_for_http,
)
from .pins import E2E_PINS


@dataclass(slots=True)
class E2ERuntime:
    run_id: str
    root: Path
    topology: ComposeTopology
    ledger: ResourceLedger
    secret_file: Path
    environment: dict[str, str]
    reservations: tuple[PortReservation, ...]
    compose_file: Path
    config_file: Path
    container_config_file: Path
    artifact_root: Path
    source_config_file: Path
    master_password_file: Path
    scope: str
    failed: bool = False


@pytest.fixture(scope="session")
def e2e_pins() -> object:
    """Expose the immutable pin manifest without importing production code."""
    return E2E_PINS


@pytest.fixture(scope="session")
def source_backup(source_server: E2ERuntime) -> ArchiveIdentity:
    """Generate and validate one fresh backup after source readiness."""
    plan = SourceBackupPlan(
        f"http://127.0.0.1:{source_server.topology.source_odoo_port}",
        source_database_name(source_server.run_id),
        source_server.artifact_root / f"source-{source_server.run_id}.zip",
    )
    password = source_server.master_password_file.read_text(encoding="utf-8").strip()
    return plan.fetch(password)


@pytest.fixture(scope="session")
def source_backup_plan(source_server: E2ERuntime) -> SourceBackupPlan:
    return SourceBackupPlan(
        f"http://127.0.0.1:{source_server.topology.source_odoo_port}",
        source_database_name(source_server.run_id),
        source_server.artifact_root / f"source-{source_server.run_id}.zip",
    )


def docker_visible_root(base: Path) -> Path:
    """Put temporary fixture roots below a path shared with Docker Desktop."""
    absolute = Path(os.path.abspath(base))
    if sys.platform == "darwin":
        temporary = str(absolute).startswith(("/tmp/", "/private/tmp/"))
        if temporary:
            digest = sha256(str(absolute).encode("utf-8")).hexdigest()[:16]
            shared_root = Path(
                os.environ.get(
                    "ODCLI_E2E_DOCKER_ROOT", str(Path(__file__).parents[3] / ".odcli-e2e")
                )
            )
            return shared_root / digest
    return absolute


def _make_runtime(base: Path, run_id: str, *, scope: str = "target") -> E2ERuntime:
    if scope not in {"source", "target"}:
        raise ValueError("scope must be source or target")
    base = docker_visible_root(base)
    root = base / f"odcli-e2e-{run_id}"
    artifact_root = base / f"odcli-e2e-artifacts-{run_id}"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ("xdg-config", "xdg-data", "xdg-cache", "xdg-state", "source-data", "target-data"):
        (root / name).mkdir(mode=0o700)
    secret_file = root / "secrets" / "pg-password"
    master_password_file = root / "secrets" / "master-password"
    write_owner_only_secret(secret_file, secrets.token_urlsafe(32))
    write_owner_only_secret(master_password_file, secrets.token_urlsafe(32))
    if not secret_file.is_file() or not secret_file.parent.is_dir():
        raise RuntimeError(f"Docker cannot bind the fixture secret path: {secret_file}")
    reservations = reserve_ports(4)
    topology = ComposeTopology.create(
        run_id, (reservations[0].port, reservations[1].port, reservations[2].port)
    )
    password = secret_file.read_text(encoding="utf-8").strip()
    master_password = master_password_file.read_text(encoding="utf-8").strip()
    addon_root = Path(__file__).parents[2] / "fixtures" / "addons"
    source_config_file = root / "source-etc" / "odoo.conf"
    write_odoo_config(
        source_config_file,
        database_host="source_postgres",
        database_port=5432,
        database_password=password,
        admin_password=master_password,
        data_dir=Path("/var/lib/odoo"),
        addons_path=(Path("/usr/lib/python3/dist-packages/odoo/addons"), Path("/mnt/extra-addons")),
    )
    container_config_file = root / "target-etc" / "odoo-container.conf"
    write_odoo_config(
        container_config_file,
        database_host="target_postgres",
        database_port=5432,
        database_password=password,
        admin_password=master_password,
        data_dir=Path("/var/lib/odoo-target"),
        addons_path=(Path("/usr/lib/python3/dist-packages/odoo/addons"),),
    )
    config_file = root / "target-etc" / "odoo-host.conf"
    write_odoo_config(
        config_file,
        database_host="127.0.0.1",
        database_port=topology.target_postgres_port,
        database_password=password,
        admin_password=master_password,
        data_dir=root / "target-data",
        http_interface="127.0.0.1",
        http_port=reservations[3].port,
    )
    compose_file = root / "compose.yaml"
    compose_file.write_text(
        topology.render(
            secret_file=secret_file,
            addon_root=addon_root,
            source_root=root / "source-data",
            source_config=source_config_file if scope == "source" else None,
            target_config=container_config_file if scope == "target" else None,
            target_root=root / "target-data" if scope == "target" else None,
            services=(
                ("source_postgres", "source_odoo")
                if scope == "source"
                else ("target_postgres", "target_init")
            ),
        ),
        encoding="utf-8",
    )
    compose_file.chmod(0o600)
    environment = {
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
        "ODCLI_E2E_RUN_ID": run_id,
        "ODCLI_E2E_CATALOG": str(root / "catalog.sqlite3"),
    }
    return E2ERuntime(
        run_id,
        root,
        topology,
        ResourceLedger(run_id),
        secret_file,
        environment,
        reservations,
        compose_file,
        config_file,
        container_config_file,
        artifact_root,
        source_config_file,
        master_password_file,
        scope,
    )


def _compose_runner(
    lifecycle: ComposeLifecycle,
) -> Callable[[tuple[str, ...], float], subprocess.CompletedProcess[str]]:
    def run(args: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        return lifecycle.run(*args, timeout=timeout)

    return run


def _provision(runtime: E2ERuntime, *, scope: str | None = None) -> None:
    scope = scope or runtime.scope
    if scope not in {"source", "target"}:
        raise ValueError("scope must be source or target")
    services = (
        ("source_postgres", "source_odoo")
        if scope == "source"
        else ("target_postgres", "target_init")
    )
    up_services = services if scope == "source" else ("target_postgres",)
    lifecycle = ComposeLifecycle(runtime.compose_file, runtime.topology.project_name)
    runtime.ledger.record(
        "runtime-root",
        runtime.root.name,
        lambda: _remove_runtime_files(runtime),
    )
    runtime.ledger.record(
        "compose-project",
        runtime.topology.project_name,
        lambda: compose_down(
            runtime.compose_file,
            runtime.topology.project_name,
            ports=(
                runtime.topology.source_postgres_port,
                runtime.topology.target_postgres_port,
                runtime.topology.source_odoo_port,
                runtime.reservations[3].port,
            ),
        ),
    )
    for reservation in runtime.reservations:
        runtime.ledger.record(
            "port",
            f"{runtime.run_id}-port-{reservation.port}",
            reservation.release,
        )
    for name in runtime.topology.names_for(services):
        runtime.ledger.record("docker-resource", name, lambda: None)
    records = (
        (("database", "source-db"), ("filestore", "source-filestore"))
        if scope == "source"
        else (("database", "sentinel-db"), ("filestore", "target-filestore"))
    ) + (("catalog", "catalog"),)
    for kind, suffix in records:
        runtime.ledger.record(kind, f"{runtime.run_id}-{suffix}", lambda: None)
    for reservation in runtime.reservations[:3]:
        reservation.release()
    lifecycle.run("up", "--detach", "--wait", *up_services, timeout=180.0)
    runner = _compose_runner(lifecycle)
    if scope == "source":
        wait_for_compose_pg_isready(runner, "source_postgres")
        lifecycle.run(
            "run",
            "--rm",
            "--no-deps",
            "source_odoo",
            "odoo",
            "--config=/etc/odoo/odoo.conf",
            f"--database={runtime.topology.source_database}",
            "--init=odcli_e2e_probe",
            "--without-demo=all",
            "--stop-after-init",
            timeout=300.0,
        )
        wait_for_http(
            f"http://127.0.0.1:{runtime.topology.source_odoo_port}/web/health",
            timeout=180.0,
        )
        wait_for_http(
            f"http://127.0.0.1:{runtime.topology.source_odoo_port}/web/database/selector",
            timeout=180.0,
        )
    else:
        wait_for_compose_pg_isready(runner, "target_postgres")
        lifecycle.run(
            "run",
            "--rm",
            "--no-deps",
            "target_init",
            "odoo",
            "--config=/etc/odoo/odoo.conf",
            f"--database={runtime.topology.target_sentinel_database}",
            "--init=base",
            "--without-demo=all",
            "--stop-after-init",
            timeout=300.0,
        )


def _remove_runtime_files(runtime: E2ERuntime) -> None:
    remove_owned_root(runtime.root, run_id=runtime.run_id)
    if not runtime.failed or os.environ.get("ODCLI_E2E_KEEP_FAILED") != "1":
        remove_owned_root(runtime.artifact_root, run_id=runtime.run_id)


def _publish_failure_logs(runtime: E2ERuntime, lifecycle: ComposeLifecycle) -> None:
    """Publish bounded Compose logs before teardown removes the run resources."""
    secrets_to_redact = tuple(
        value
        for path in (runtime.secret_file, runtime.master_password_file)
        for value in (path.read_text(encoding="utf-8").strip(),)
        if value
    )
    evidence = FailureEvidence(
        runtime.run_id,
        secrets.token_urlsafe(24),
        runtime.artifact_root,
        secrets_to_redact,
    )
    services = (
        ("source_postgres", "source_odoo")
        if runtime.scope == "source"
        else ("target_postgres", "target_init")
    )
    for name, service in zip(("postgres", "odoo"), services, strict=True):
        result = lifecycle.run("logs", "--no-color", "--tail", "200", service, timeout=30.0)
        evidence.add_log(name, result.stdout + result.stderr)
    evidence.write()


def _finalize(runtime: E2ERuntime, primary_failure: BaseException | None = None) -> None:
    runtime.failed = primary_failure is not None
    errors: list[BaseException] = []
    if primary_failure is not None:
        try:
            _publish_failure_logs(
                runtime,
                ComposeLifecycle(runtime.compose_file, runtime.topology.project_name),
            )
        except BaseException as error:
            errors.append(error)
    try:
        runtime.ledger.unwind(primary_failure=primary_failure)
    except BaseException as error:
        errors.append(error)
    try:
        audit_no_leaks(
            runtime.run_id,
            compose_project=runtime.topology.project_name,
            runtime_root=runtime.root,
            ports=(
                runtime.topology.source_postgres_port,
                runtime.topology.target_postgres_port,
                runtime.topology.source_odoo_port,
                runtime.reservations[3].port,
            ),
            catalog_path=runtime.root / "catalog.sqlite3",
            filestore_paths=(runtime.root / "source-data", runtime.root / "target-data"),
        )
    except BaseException as error:
        errors.append(error)
    if not errors:
        return
    if len(errors) == 1:
        raise errors[0]
    raise BaseExceptionGroup("real-Odoo fixture finalization failures", errors)


@pytest.fixture(scope="session")
def source_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[E2ERuntime]:
    runtime = _make_runtime(
        Path(tmp_path_factory.mktemp("odcli-e2e")), new_run_id(), scope="source"
    )
    try:
        _provision(runtime)
        yield runtime
    except BaseException as error:
        _finalize(runtime, error)
    else:
        _finalize(runtime)


@pytest.fixture()
def target_runtime(source_server: E2ERuntime, tmp_path: Path) -> Iterator[E2ERuntime]:
    """Create a fresh function-scoped target root and target resource ledger."""
    runtime = _make_runtime(tmp_path, new_run_id(), scope="target")
    try:
        _provision(runtime)
        yield runtime
    except BaseException as error:
        _finalize(runtime, error)
    else:
        _finalize(runtime)


@pytest.fixture()
def resource_ledger(target_runtime: E2ERuntime) -> ResourceLedger:
    return target_runtime.ledger


@pytest.fixture()
def failure_evidence(target_runtime: E2ERuntime) -> FailureEvidence:
    canary = secrets.token_urlsafe(24)
    return FailureEvidence(target_runtime.run_id, canary, target_runtime.artifact_root)
