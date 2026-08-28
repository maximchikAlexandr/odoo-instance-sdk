"""Private pgAdmin Docker identity and reconciliation operations."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from odoo_instance_sdk.exceptions import PgAdminUnavailableError
from odoo_instance_sdk.internal import pgadmin_readiness
from odoo_instance_sdk.internal.pgadmin_files import (
    PGADMIN_CONTAINER_NAME,
    PGADMIN_CONTAINER_PORT,
    PGADMIN_DATA_DESTINATION,
    PGADMIN_DEFAULT_EMAIL,
    PGADMIN_IMAGE,
    PGADMIN_LABEL_FINGERPRINT,
    PGADMIN_LABEL_MANAGED,
    PGADMIN_LABEL_NETWORK,
    PGADMIN_PASSWORD_DESTINATION,
    PGADMIN_PGPASS_DESTINATION,
    PGADMIN_RUNTIME_UID,
    PgAdminPreparation,
    PostgresIdentity,
)
from odoo_instance_sdk.models import PgAdminOpenResult, PgAdminOpenState

_wait_ready = pgadmin_readiness.wait_ready
_ACTIVE_PGPASS_REFRESH = (
    "set -eu; "
    "umask 077; "
    "temporary=/var/lib/pgadmin/.pgpass.odoo-instance-sdk.tmp; "
    f'cp {PGADMIN_PGPASS_DESTINATION} "$temporary"; '
    'chmod 0600 "$temporary"; '
    f'mv -f "$temporary" {PGADMIN_DATA_DESTINATION}/.pgpass'
)
_PGADMIN_EFFECTIVE_SERVER_QUERY = (
    "import json,sqlite3;"
    "db=sqlite3.connect('/var/lib/pgadmin/pgadmin4.db');"
    "rows=db.execute('SELECT host,port,username,maintenance_db,db_res FROM server ORDER BY id').fetchall();"
    "print(json.dumps([{'host':r[0],'port':r[1],'username':r[2],"
    "'maintenance_db':r[3],'db_res':r[4]} for r in rows]))"
)


class _ContainerCreateFailure(PgAdminUnavailableError):
    """A failed ``docker run`` that may still have created a container."""

    def __init__(self, created_id: str | None) -> None:
        self.created_id = created_id
        super().__init__()


def resolve_postgres_identity(cluster: object, *, deadline: float) -> PostgresIdentity:
    runner = getattr(cluster, "compose_runner", None)
    compose_file = getattr(cluster, "compose_file", None)
    project_name = getattr(cluster, "compose_project_name", None)
    if runner is None or not isinstance(compose_file, Path) or not isinstance(project_name, str):
        raise PgAdminUnavailableError()
    container_id = _resolve_postgres_container_id(
        runner, compose_file, project_name, deadline=deadline
    )
    inspected = inspect_container(runner, container_id, deadline=deadline)
    if inspected is None:
        raise PgAdminUnavailableError()
    configured_user = getattr(cluster, "_user", None)
    identity = _identity_from_inspect(
        inspected,
        project_name,
        user=configured_user if isinstance(configured_user, str) else None,
    )
    _validate_postgres_network(runner, identity.network, project_name, deadline=deadline)
    return PostgresIdentity(
        container_name=identity.container_name,
        network=identity.network,
        user=identity.user,
        host=identity.host,
        port=identity.port,
    )


def _resolve_postgres_container_id(
    runner: object, compose_file: object, project_name: str, *, deadline: float
) -> str:
    ps = run_docker(
        runner,
        [
            "docker",
            "compose",
            "--project-name",
            project_name,
            "-f",
            str(compose_file),
            "ps",
            "--format",
            "json",
        ],
        deadline=deadline,
    )
    if ps.returncode != 0:
        raise PgAdminUnavailableError()
    container_id: str | None = None
    for line in ps.stdout.splitlines():
        try:
            row = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(row, dict) and row.get("Service") == "postgres":
            candidate = row.get("ID") or row.get("Id")
            if isinstance(candidate, str) and candidate:
                container_id = candidate
                break
    if container_id is None:
        raise PgAdminUnavailableError()
    return container_id


def _identity_from_inspect(
    inspected: dict[str, object], project_name: str, *, user: str | None = None
) -> PostgresIdentity:
    labels = _string_mapping(_mapping(inspected.get("Config"), "Labels"))
    if labels.get("com.docker.compose.project") not in {None, project_name}:
        raise PgAdminUnavailableError()
    raw_name = inspected.get("Name")
    container_name = (
        raw_name[1:] if isinstance(raw_name, str) and raw_name.startswith("/") else raw_name
    )
    if not isinstance(container_name, str) or not container_name:
        raise PgAdminUnavailableError()
    networks = _mapping(inspected.get("NetworkSettings"), "Networks")
    selected = [
        name for name in networks if isinstance(name, str) and name.startswith(f"{project_name}_")
    ]
    if len(selected) != 1:
        raise PgAdminUnavailableError()
    config = _mapping(inspected, "Config")
    inspected_user = config.get("User")
    if not user:
        user = inspected_user if isinstance(inspected_user, str) and inspected_user else "odoo"
    return PostgresIdentity(
        container_name=container_name,
        network=selected[0],
        user=user,
        host=container_name,
    )


def _validate_postgres_network(
    runner: object, network: str, project_name: str, *, deadline: float
) -> None:
    network_info = inspect_network(runner, network, deadline=deadline)
    network_labels = _string_mapping(_mapping(network_info, "Labels"))
    if network_labels.get("com.docker.compose.project") != project_name:
        raise PgAdminUnavailableError()


def reconcile_container(
    preparation: PgAdminPreparation,
    *,
    runner: object,
    network: str,
    database: str,
    deadline: float,
) -> PgAdminOpenResult:
    current = inspect_container(runner, PGADMIN_CONTAINER_NAME, deadline=deadline, missing_ok=True)
    state = PgAdminOpenState.STARTED
    if current is not None:
        assert_owned_container(current)
        if container_matches(current, preparation, network=network):
            _wait_ready(preparation.port, deadline=deadline)
            verify_server(preparation, database, runner=runner, deadline=deadline)
            return PgAdminOpenResult(
                state=PgAdminOpenState.REUSED,
                url=f"http://127.0.0.1:{preparation.port}",
            )
        current_id = get_container_id(current)
        current_fingerprint = container_fingerprint(current)
        latest = inspect_container(runner, current_id, deadline=deadline, missing_ok=True)
        if latest is not None:
            if get_container_id(latest) != current_id:
                raise PgAdminUnavailableError()
            assert_owned_container(latest, fingerprint=current_fingerprint)
            remove_container(runner, current_id, deadline=deadline)
        state = PgAdminOpenState.RECONFIGURED
    created_id: str | None = None
    try:
        created_id = create_container(runner, preparation, network=network, deadline=deadline)
        _wait_ready(preparation.port, deadline=deadline)
        refresh_active_pgpass(
            runner,
            container_id=created_id,
            fingerprint=preparation.fingerprint,
            deadline=deadline,
        )
        verify_server(preparation, database, runner=runner, deadline=deadline)
    except _ContainerCreateFailure as exc:
        remove_partial_container(
            runner,
            container_id=exc.created_id,
            fingerprint=preparation.fingerprint,
            deadline=deadline,
        )
        raise PgAdminUnavailableError() from None
    except PgAdminUnavailableError:
        remove_partial_container(
            runner,
            container_id=created_id,
            fingerprint=preparation.fingerprint,
            deadline=deadline,
        )
        raise
    return PgAdminOpenResult(
        state=state,
        url=f"http://127.0.0.1:{preparation.port}",
    )


def get_container_id(inspected: dict[str, object]) -> str:
    value = inspected.get("Id") or inspected.get("ID")
    if not isinstance(value, str) or not value:
        raise PgAdminUnavailableError()
    return value


def container_fingerprint(inspected: dict[str, object]) -> str:
    labels = _string_mapping(_mapping(inspected.get("Config"), "Labels"))
    fingerprint = labels.get(PGADMIN_LABEL_FINGERPRINT)
    if not fingerprint:
        raise PgAdminUnavailableError()
    return fingerprint


def assert_owned_container(inspected: dict[str, object], *, fingerprint: str | None = None) -> None:
    labels = _string_mapping(_mapping(inspected.get("Config"), "Labels"))
    if labels.get(PGADMIN_LABEL_MANAGED) != "true" or not labels.get(PGADMIN_LABEL_FINGERPRINT):
        raise PgAdminUnavailableError()
    if fingerprint is not None and labels.get(PGADMIN_LABEL_FINGERPRINT) != fingerprint:
        raise PgAdminUnavailableError()


def remove_container(runner: object, container_id: str, *, deadline: float) -> None:
    result = run_docker(runner, ["docker", "rm", "--force", container_id], deadline=deadline)
    if result.returncode != 0:
        raise PgAdminUnavailableError()


def run_docker(
    runner: object, args: list[str], *, deadline: float
) -> subprocess.CompletedProcess[str]:
    run = getattr(runner, "run", None)
    if not callable(run):
        raise PgAdminUnavailableError()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PgAdminUnavailableError()
    try:
        result = run(args, timeout=remaining)
    except (OSError, subprocess.SubprocessError, TypeError):
        raise PgAdminUnavailableError() from None
    if not isinstance(result, subprocess.CompletedProcess):
        raise PgAdminUnavailableError()
    return result


def inspect_container(
    runner: object,
    name: str,
    *,
    deadline: float,
    missing_ok: bool = False,
) -> dict[str, object] | None:
    result = run_docker(runner, ["docker", "inspect", "--format", "json", name], deadline=deadline)
    if result.returncode != 0:
        detail = f"{result.stdout}\n{result.stderr}".lower()
        if missing_ok and any(
            marker in detail for marker in ("no such object", "no such container", "not found")
        ):
            return None
        raise PgAdminUnavailableError()
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        raise PgAdminUnavailableError() from None
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
        return payload[0]
    if isinstance(payload, dict):
        return payload
    raise PgAdminUnavailableError()


def inspect_network(runner: object, name: str, *, deadline: float) -> dict[str, object]:
    result = run_docker(
        runner, ["docker", "network", "inspect", "--format", "json", name], deadline=deadline
    )
    if result.returncode != 0:
        raise PgAdminUnavailableError()
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        raise PgAdminUnavailableError() from None
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
        return payload[0]
    if isinstance(payload, dict):
        return payload
    raise PgAdminUnavailableError()


def _mapping(value: object, key: str) -> dict[str, object]:
    if isinstance(value, dict):
        nested = value.get(key)
        return nested if isinstance(nested, dict) else {}
    return {}


def _string_mapping(value: dict[str, object]) -> dict[str, str]:
    return {key: item for key, item in value.items() if isinstance(item, str)}


def container_matches(
    inspected: dict[str, object], preparation: PgAdminPreparation, *, network: str
) -> bool:
    config = _mapping(inspected, "Config")
    if config.get("Image") != PGADMIN_IMAGE or str(config.get("User", "")) != str(
        PGADMIN_RUNTIME_UID
    ):
        return False
    labels = _string_mapping(_mapping(config, "Labels"))
    if labels.get(PGADMIN_LABEL_FINGERPRINT) != preparation.fingerprint:
        return False
    if labels.get(PGADMIN_LABEL_NETWORK) != network:
        return False
    expected_env = {
        "PGADMIN_CONFIG_SERVER_MODE=False",
        "PGADMIN_REPLACE_SERVERS_ON_STARTUP=True",
        f"PGADMIN_DEFAULT_EMAIL={PGADMIN_DEFAULT_EMAIL}",
        f"PGADMIN_DEFAULT_PASSWORD_FILE={PGADMIN_PASSWORD_DESTINATION}",
        f"PGPASS_FILE={PGADMIN_PGPASS_DESTINATION}",
    }
    env = config.get("Env")
    if not isinstance(env, list) or not all(isinstance(item, str) for item in env):
        return False
    expected_by_key = dict(item.split("=", 1) for item in expected_env)
    observed: dict[str, list[str]] = {key: [] for key in expected_by_key}
    for item in env:
        key, separator, value = item.partition("=")
        if separator and key in observed:
            observed[key].append(value)
    if any(
        not values or any(value != expected_by_key[key] for value in values)
        for key, values in observed.items()
    ):
        return False
    state = _mapping(inspected, "State")
    if state.get("Running") is not True:
        return False
    networks = _mapping(inspected.get("NetworkSettings"), "Networks")
    if set(networks) != {network}:
        return False
    return _mounts_match(inspected, preparation) and _ports_match(inspected, preparation.port)


def _mounts_match(inspected: dict[str, object], preparation: PgAdminPreparation) -> bool:
    mounts = _mapping_list(inspected.get("Mounts"))
    actual = {
        (item.get("Source"), item.get("Destination"), item.get("RW") is not True) for item in mounts
    }
    expected = {(str(m.host_path), m.container_path, m.read_only) for m in preparation.mounts}
    return actual == expected


def _ports_match(inspected: dict[str, object], port: int) -> bool:
    bindings = _mapping(inspected.get("HostConfig"), "PortBindings")
    values = bindings.get("80/tcp")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        return False
    return values[0].get("HostIp") in {"127.0.0.1", "127.0.0.1/32"} and values[0].get(
        "HostPort"
    ) == str(port)


def _mapping_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def create_container(
    runner: object,
    preparation: PgAdminPreparation,
    *,
    network: str,
    deadline: float,
) -> str:
    args = [
        "docker",
        "run",
        "--detach",
        "--name",
        PGADMIN_CONTAINER_NAME,
        "--user",
        str(PGADMIN_RUNTIME_UID),
        "--publish",
        f"127.0.0.1:{preparation.port}:{PGADMIN_CONTAINER_PORT}",
        "--network",
        network,
        "--label",
        f"{PGADMIN_LABEL_MANAGED}=true",
        "--label",
        f"{PGADMIN_LABEL_FINGERPRINT}={preparation.fingerprint}",
        "--label",
        f"{PGADMIN_LABEL_NETWORK}={network}",
        "--env",
        "PGADMIN_CONFIG_SERVER_MODE=False",
        "--env",
        "PGADMIN_REPLACE_SERVERS_ON_STARTUP=True",
        "--env",
        f"PGADMIN_DEFAULT_EMAIL={PGADMIN_DEFAULT_EMAIL}",
        "--env",
        f"PGADMIN_DEFAULT_PASSWORD_FILE={PGADMIN_PASSWORD_DESTINATION}",
        "--env",
        f"PGPASS_FILE={PGADMIN_PGPASS_DESTINATION}",
    ]
    for mount in preparation.mounts:
        args.extend(
            [
                "--mount",
                f"type=bind,source={mount.host_path},destination={mount.container_path}"
                + (",readonly" if mount.read_only else ""),
            ]
        )
    args.append(PGADMIN_IMAGE)
    result = run_docker(runner, args, deadline=deadline)
    created_id = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else None
    if result.returncode != 0:
        raise _ContainerCreateFailure(created_id)
    if not created_id:
        raise PgAdminUnavailableError()
    return created_id


def remove_partial_container(
    runner: object,
    *,
    container_id: str | None,
    fingerprint: str,
    deadline: float,
) -> None:
    if not container_id:
        return
    try:
        inspected = inspect_container(runner, container_id, deadline=deadline, missing_ok=True)
        if inspected is None:
            return
        if get_container_id(inspected) != container_id:
            return
        assert_owned_container(inspected, fingerprint=fingerprint)
        remove_container(runner, container_id, deadline=deadline)
    except PgAdminUnavailableError:
        return


def refresh_active_pgpass(
    runner: object,
    *,
    container_id: str,
    fingerprint: str,
    deadline: float,
) -> None:
    """Refresh pgAdmin's persistent passfile using an ownership-checked ID."""
    inspected = inspect_container(runner, container_id, deadline=deadline, missing_ok=True)
    if inspected is None or get_container_id(inspected) != container_id:
        raise PgAdminUnavailableError()
    assert_owned_container(inspected, fingerprint=fingerprint)
    result = run_docker(
        runner,
        [
            "docker",
            "exec",
            "--user",
            str(PGADMIN_RUNTIME_UID),
            container_id,
            "/bin/sh",
            "-c",
            _ACTIVE_PGPASS_REFRESH,
        ],
        deadline=deadline,
    )
    if result.returncode != 0:
        raise PgAdminUnavailableError()


def verify_server(
    preparation: PgAdminPreparation,
    database: str,
    *,
    runner: object,
    deadline: float,
) -> None:
    try:
        payload = json.loads(preparation.paths.servers_json.read_text(encoding="utf-8"))
        servers = payload["Servers"]
        server = servers["1"]
    except (OSError, KeyError, TypeError, ValueError):
        raise PgAdminUnavailableError() from None
    if not isinstance(server, dict):
        raise PgAdminUnavailableError()
    result = run_docker(
        runner,
        [
            "docker",
            "exec",
            preparation.container_name,
            "/venv/bin/python3",
            "-c",
            _PGADMIN_EFFECTIVE_SERVER_QUERY,
        ],
        deadline=deadline,
    )
    if result.returncode != 0:
        raise PgAdminUnavailableError()
    try:
        effective = json.loads(result.stdout)
    except (TypeError, ValueError):
        raise PgAdminUnavailableError() from None
    if not isinstance(effective, list) or len(effective) != 1:
        raise PgAdminUnavailableError()
    imported = effective[0]
    if not isinstance(imported, dict):
        raise PgAdminUnavailableError()
    expected = {
        "host": server.get("Host"),
        "port": server.get("Port"),
        "username": server.get("Username"),
        "maintenance_db": database,
        "db_res": database,
    }
    if any(imported.get(key) != value for key, value in expected.items()):
        raise PgAdminUnavailableError()
