"""Self-provisioning fixtures shared by real-Odoo E2E scenarios."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from .archive import SourceBackupPlan, source_database_name
from .cleanup import FailureEvidence, ResourceLedger, write_odoo_config, write_owner_only_secret
from .compose import ComposeTopology, PortReservation, new_run_id, reserve_ports
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


@pytest.fixture(scope="session")
def e2e_pins() -> object:
    """Expose the immutable pin manifest without importing production code."""
    return E2E_PINS


@pytest.fixture(scope="session")
def source_backup(source_server: E2ERuntime) -> SourceBackupPlan:
    """Describe a fresh source backup; fetching remains an explicit test step."""
    return SourceBackupPlan(
        f"http://127.0.0.1:{source_server.topology.source_odoo_port}",
        source_database_name(source_server.run_id),
        source_server.root / "artifacts" / f"source-{source_server.run_id}.zip",
    )


@pytest.fixture(scope="session")
def source_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[E2ERuntime]:
    """Own session-scoped reference services and isolated Docker-visible roots."""
    run_id = new_run_id()
    raw_root = tmp_path_factory.mktemp(f"odcli-e2e-{run_id}")
    root = Path(raw_root)
    (root / "xdg-config").mkdir(mode=0o700)
    (root / "xdg-data").mkdir(mode=0o700)
    (root / "xdg-cache").mkdir(mode=0o700)
    (root / "xdg-state").mkdir(mode=0o700)
    (root / "artifacts").mkdir(mode=0o700)
    (root / "source-data").mkdir(mode=0o700)
    secret_file = root / "secrets" / "pg-password"
    write_owner_only_secret(secret_file, secrets.token_urlsafe(32))
    reservations = reserve_ports()
    topology = ComposeTopology.create(
        run_id, (reservations[0].port, reservations[1].port, reservations[2].port)
    )
    addon_root = Path(__file__).parents[2] / "fixtures" / "addons"
    config_file = root / "source-etc" / "odoo.conf"
    write_odoo_config(
        config_file,
        database_host="source_postgres",
        database_port=5432,
        database_password=secret_file.read_text(encoding="utf-8").strip(),
        data_dir=root / "source-data",
        addons_path=(Path("/usr/lib/python3/dist-packages/odoo/addons"), Path("/mnt/extra-addons")),
    )
    compose_file = root / "compose.yaml"
    compose_file.write_text(
        topology.render(
            secret_file=secret_file,
            addon_root=addon_root,
            source_root=root / "source-data",
            source_config=config_file,
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
    }
    ledger = ResourceLedger(run_id)
    runtime = E2ERuntime(
        run_id,
        root,
        topology,
        ledger,
        secret_file,
        environment,
        reservations,
        compose_file,
        config_file,
    )
    try:
        yield runtime
    finally:
        for reservation in reservations:
            reservation.release()


@pytest.fixture()
def target_runtime(source_server: E2ERuntime, tmp_path: Path) -> Iterator[E2ERuntime]:
    """Create a fresh function-scoped target root and target resource ledger."""
    run_id = f"{source_server.run_id}-{secrets.token_hex(8)}"
    root = tmp_path / f"odcli-e2e-target-{run_id}"
    root.mkdir(mode=0o700)
    (root / "source-data").mkdir(mode=0o700)
    (root / "target-data").mkdir(mode=0o700)
    ledger = ResourceLedger(run_id)
    reservations = reserve_ports()
    topology = ComposeTopology.create(
        run_id, (reservations[0].port, reservations[1].port, reservations[2].port)
    )
    secret_file = root / "secrets" / "pg-password"
    write_owner_only_secret(
        secret_file, source_server.secret_file.read_text(encoding="utf-8").strip()
    )
    environment = {
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
        "ODCLI_E2E_RUN_ID": run_id,
        "ODCLI_E2E_CATALOG": str(root / "catalog.sqlite3"),
    }
    for name in ("xdg-config", "xdg-data", "xdg-cache", "xdg-state"):
        (root / name).mkdir(mode=0o700)
    compose_file = root / "compose.yaml"
    config_file = root / "target-etc" / "odoo.conf"
    write_odoo_config(
        config_file,
        database_host="target_postgres",
        database_port=5432,
        database_password=secret_file.read_text(encoding="utf-8").strip(),
        data_dir=root / "target-data",
    )
    compose_file.write_text(
        topology.render(
            secret_file=secret_file,
            addon_root=Path(__file__).parents[2] / "fixtures" / "addons",
            source_root=root / "source-data",
            source_config=config_file,
        ),
        encoding="utf-8",
    )
    compose_file.chmod(0o600)
    runtime = E2ERuntime(
        run_id,
        root,
        topology,
        ledger,
        secret_file,
        environment,
        reservations,
        compose_file,
        config_file,
    )
    try:
        yield runtime
    except BaseException as error:
        try:
            ledger.unwind(primary_failure=error)
        finally:
            for reservation in reservations:
                reservation.release()
    else:
        try:
            ledger.unwind()
        finally:
            for reservation in reservations:
                reservation.release()


@pytest.fixture()
def resource_ledger(target_runtime: E2ERuntime) -> ResourceLedger:
    return target_runtime.ledger


@pytest.fixture()
def failure_evidence(target_runtime: E2ERuntime) -> FailureEvidence:
    canary = secrets.token_urlsafe(24)
    return FailureEvidence(target_runtime.run_id, canary, target_runtime.root / "artifacts")
