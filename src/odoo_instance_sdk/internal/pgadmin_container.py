"""Private pgAdmin Docker identity and reconciliation operations."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

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
    PgAdminPaths,
    PgAdminPreparation,
    PostgresIdentity,
    pgadmin_mounts,
)
from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
from odoo_instance_sdk.models import PgAdminOpenResult, PgAdminOpenState

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.proc import PreparedStep


class PostgresIdentityCluster(Protocol):
    @property
    def compose_runner(self) -> ComposeRunner: ...

    @property
    def compose_file(self) -> Path: ...

    @property
    def compose_project_name(self) -> str: ...

    @property
    def _user(self) -> str | None: ...


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


def reconciliation_steps(
    *,
    paths: PgAdminPaths,
    port: int,
    network: str,
    fingerprint: str,
    secret_values: tuple[str, ...] = (),
) -> tuple[PreparedStep, ...]:
    """Capture the post-preparation Docker phase for one exact command.

    The phase creates this second immutable instance with the real HMAC only
    after it has acquired the user-global lock and prepared the private key.
    Keeping this manifest in one place prevents the phase boundary from
    drifting between preview and execution.
    """
    from odoo_instance_sdk.internal.proc import PreparedStep

    return (
        PreparedStep(
            step_id="pgadmin.container.inspect.1",
            argv=("docker", "inspect", "--format", "json", PGADMIN_CONTAINER_NAME),
            read_only=True,
        ),
        PreparedStep(
            step_id="pgadmin.container.remove",
            argv=("docker", "rm", "--force", PGADMIN_CONTAINER_NAME),
            mutating=True,
        ),
        PreparedStep(
            step_id="pgadmin.container.run",
            argv=_docker_run_argv(
                PgAdminPreparation(
                    paths=paths,
                    fingerprint=fingerprint,
                    port=port,
                    container_name=PGADMIN_CONTAINER_NAME,
                    mounts=pgadmin_mounts(paths),
                ),
                network=network,
            ),
            mutating=True,
            secret_values=secret_values,
        ),
        PreparedStep(
            step_id="pgadmin.container.refresh.inspect",
            argv=("docker", "inspect", "--format", "json", PGADMIN_CONTAINER_NAME),
            read_only=True,
        ),
        PreparedStep(
            step_id="pgadmin.container.refresh",
            argv=_docker_refresh_argv(PGADMIN_CONTAINER_NAME),
            mutating=True,
        ),
        PreparedStep(
            step_id="pgadmin.container.verify",
            argv=_docker_verify_argv(PGADMIN_CONTAINER_NAME),
            read_only=True,
        ),
        PreparedStep(
            step_id="pgadmin.container.inspect.2",
            argv=("docker", "inspect", "--format", "json", PGADMIN_CONTAINER_NAME),
            read_only=True,
        ),
        PreparedStep(
            step_id="pgadmin.container.cleanup.remove",
            argv=("docker", "rm", "--force", PGADMIN_CONTAINER_NAME),
            mutating=True,
        ),
    )


def reconciliation_inspect_step() -> PreparedStep:
    """Capture the lock-adjacent inspection that starts reconciliation."""
    from odoo_instance_sdk.internal.proc import PreparedStep

    return PreparedStep(
        step_id="pgadmin.reconciliation.inspect.0",
        argv=("docker", "inspect", "--format", "json", PGADMIN_CONTAINER_NAME),
        read_only=True,
    )


def _docker_run_argv(preparation: PgAdminPreparation, *, network: str) -> tuple[str, ...]:
    args: list[str] = [
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
            (
                "--mount",
                f"type=bind,source={mount.host_path},destination={mount.container_path}"
                + (",readonly" if mount.read_only else ""),
            )
        )
    args.append(PGADMIN_IMAGE)
    return tuple(args)


def _docker_refresh_argv(container: str) -> tuple[str, ...]:
    return (
        "docker",
        "exec",
        "--user",
        str(PGADMIN_RUNTIME_UID),
        container,
        "/bin/sh",
        "-c",
        _ACTIVE_PGPASS_REFRESH,
    )


def _docker_verify_argv(container: str) -> tuple[str, ...]:
    return (
        "docker",
        "exec",
        container,
        "/venv/bin/python3",
        "-c",
        _PGADMIN_EFFECTIVE_SERVER_QUERY,
    )


def _skip_reconciliation_steps(*step_ids: str) -> None:
    """Record an explicit branch omission in the active phase ledger."""
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    if context is None:
        return
    for step_id in step_ids:
        if context.planned(step_id) and not context.consumed(step_id):
            context.skip(step_id)


class _ContainerCreateFailure(PgAdminUnavailableError):
    """A failed ``docker run`` that may still have created a container."""

    def __init__(self, created_id: str | None) -> None:
        self.created_id = created_id
        super().__init__()


class _NoCapturedContainer:
    __slots__ = ()


_UNSET = _NoCapturedContainer()


def resolve_postgres_identity(
    cluster: PostgresIdentityCluster,
    *,
    deadline: float,
    planned: bool = False,
) -> PostgresIdentity:
    runner = cluster.compose_runner
    compose_file = cluster.compose_file
    project_name = cluster.compose_project_name
    if runner is None:
        raise PgAdminUnavailableError()
    container_id = _resolve_postgres_container_id(
        runner,
        compose_file,
        project_name,
        deadline=deadline,
        planned=planned,
        step_id="pgadmin.identity.ps",
    )
    inspected = inspect_container(
        runner,
        container_id,
        deadline=deadline,
        step_id="pgadmin.identity.inspect",
    )
    if inspected is None:
        raise PgAdminUnavailableError()
    configured_user = cluster._user
    identity = _identity_from_inspect(
        inspected,
        project_name,
        user=configured_user if isinstance(configured_user, str) else None,
    )
    _validate_postgres_network(
        runner,
        identity.network,
        project_name,
        deadline=deadline,
        step_id="pgadmin.identity.network",
    )
    return PostgresIdentity(
        container_name=identity.container_name,
        network=identity.network,
        user=identity.user,
        host=identity.host,
        port=identity.port,
    )


def _resolve_postgres_container_id(
    runner: ComposeRunner,
    compose_file: Path,
    project_name: str,
    *,
    deadline: float,
    planned: bool = False,
    step_id: str | None = None,
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
        step_id=step_id,
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
    if planned:
        # Compose's stable service container name avoids putting the runtime
        # ID returned by ``ps`` into the frozen command.  The ID remains in
        # the captured result for diagnostics, while inspect is still bound
        # to the selected project/service.
        return f"{project_name}-postgres-1"
    return container_id


def _identity_from_inspect(
    inspected: dict[str, JsonValue], project_name: str, *, user: str | None = None
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
    runner: ComposeRunner,
    network: str,
    project_name: str,
    *,
    deadline: float,
    step_id: str | None = None,
) -> None:
    network_info = inspect_network(runner, network, deadline=deadline, step_id=step_id)
    network_labels = _string_mapping(_mapping(network_info, "Labels"))
    if network_labels.get("com.docker.compose.project") != project_name:
        raise PgAdminUnavailableError()


def reconcile_container(
    preparation: PgAdminPreparation,
    *,
    runner: ComposeRunner,
    network: str,
    database: str,
    deadline: float,
    planned: bool = False,
    current_container: dict[str, JsonValue] | None | _NoCapturedContainer = _UNSET,
) -> PgAdminOpenResult:
    if current_container is _UNSET:
        current = inspect_container(
            runner,
            PGADMIN_CONTAINER_NAME,
            deadline=deadline,
            missing_ok=True,
            step_id="pgadmin.container.inspect.0",
        )
    else:
        current = cast("dict[str, JsonValue] | None", current_container)
    state = PgAdminOpenState.STARTED
    if current is not None:
        assert_owned_container(current)
        if container_matches(current, preparation, network=network):
            _skip_reconciliation_steps(
                "pgadmin.container.inspect.1",
                "pgadmin.container.remove",
                "pgadmin.container.run",
                "pgadmin.container.refresh.inspect",
                "pgadmin.container.refresh",
            )
            _wait_ready(preparation.port, deadline=deadline)
            verify_server(
                preparation,
                database,
                runner=runner,
                deadline=deadline,
                planned=planned,
                step_id="pgadmin.container.verify",
            )
            _skip_reconciliation_steps(
                "pgadmin.container.inspect.2",
                "pgadmin.container.cleanup.remove",
            )
            return PgAdminOpenResult(
                state=PgAdminOpenState.REUSED,
                url=f"http://127.0.0.1:{preparation.port}",
            )
        current_id = get_container_id(current)
        current_fingerprint = container_fingerprint(current)
        inspect_target = PGADMIN_CONTAINER_NAME if planned else current_id
        latest = inspect_container(
            runner,
            inspect_target,
            deadline=deadline,
            missing_ok=True,
            step_id="pgadmin.container.inspect.1",
        )
        if latest is not None:
            if not planned and get_container_id(latest) != current_id:
                raise PgAdminUnavailableError()
            assert_owned_container(latest, fingerprint=current_fingerprint)
            remove_container(
                runner,
                PGADMIN_CONTAINER_NAME if planned else current_id,
                deadline=deadline,
                step_id="pgadmin.container.remove",
            )
        else:
            _skip_reconciliation_steps("pgadmin.container.remove")
        state = PgAdminOpenState.RECONFIGURED
    else:
        _skip_reconciliation_steps(
            "pgadmin.container.inspect.1",
            "pgadmin.container.remove",
        )
    created_id: str | None = None
    try:
        created_id = create_container(
            runner,
            preparation,
            network=network,
            deadline=deadline,
            step_id="pgadmin.container.run",
        )
        _wait_ready(preparation.port, deadline=deadline)
        refresh_active_pgpass(
            runner,
            container_id=created_id,
            fingerprint=preparation.fingerprint,
            deadline=deadline,
            planned=planned,
            step_id="pgadmin.container.refresh",
            inspect_step_id="pgadmin.container.refresh.inspect",
        )
        verify_server(
            preparation,
            database,
            runner=runner,
            deadline=deadline,
            planned=planned,
            step_id="pgadmin.container.verify",
        )
        _skip_reconciliation_steps(
            "pgadmin.container.inspect.2", "pgadmin.container.cleanup.remove"
        )
    except _ContainerCreateFailure as exc:
        remove_partial_container(
            runner,
            container_id=exc.created_id,
            fingerprint=preparation.fingerprint,
            deadline=deadline,
            planned=planned,
            inspect_step_id="pgadmin.container.inspect.2",
            remove_step_id="pgadmin.container.cleanup.remove",
        )
        _skip_reconciliation_steps(
            "pgadmin.container.inspect.2", "pgadmin.container.cleanup.remove"
        )
        raise PgAdminUnavailableError() from None
    except PgAdminUnavailableError:
        remove_partial_container(
            runner,
            container_id=created_id,
            fingerprint=preparation.fingerprint,
            deadline=deadline,
            planned=planned,
            inspect_step_id="pgadmin.container.inspect.2",
            remove_step_id="pgadmin.container.cleanup.remove",
        )
        _skip_reconciliation_steps(
            "pgadmin.container.inspect.2", "pgadmin.container.cleanup.remove"
        )
        raise
    return PgAdminOpenResult(
        state=state,
        url=f"http://127.0.0.1:{preparation.port}",
    )


def get_container_id(inspected: dict[str, JsonValue]) -> str:
    value = inspected.get("Id") or inspected.get("ID")
    if not isinstance(value, str) or not value:
        raise PgAdminUnavailableError()
    return value


def container_fingerprint(inspected: dict[str, JsonValue]) -> str:
    labels = _string_mapping(_mapping(inspected.get("Config"), "Labels"))
    fingerprint = labels.get(PGADMIN_LABEL_FINGERPRINT)
    if not fingerprint:
        raise PgAdminUnavailableError()
    return fingerprint


def assert_owned_container(
    inspected: dict[str, JsonValue], *, fingerprint: str | None = None
) -> None:
    labels = _string_mapping(_mapping(inspected.get("Config"), "Labels"))
    if labels.get(PGADMIN_LABEL_MANAGED) != "true" or not labels.get(PGADMIN_LABEL_FINGERPRINT):
        raise PgAdminUnavailableError()
    if fingerprint is not None and labels.get(PGADMIN_LABEL_FINGERPRINT) != fingerprint:
        raise PgAdminUnavailableError()


def owned_container_uses_port(inspected: dict[str, JsonValue], port: int) -> bool:
    """Return whether an occupied port belongs to an SDK-owned pgAdmin container."""
    try:
        assert_owned_container(inspected)
    except PgAdminUnavailableError:
        return False
    return _ports_match(inspected, port)


def remove_container(
    runner: ComposeRunner,
    container_id: str,
    *,
    deadline: float,
    step_id: str | None = None,
) -> None:
    result = run_docker(
        runner,
        ["docker", "rm", "--force", container_id],
        deadline=deadline,
        step_id=step_id,
    )
    if result.returncode != 0:
        raise PgAdminUnavailableError()


def run_docker(
    runner: ComposeRunner,
    args: list[str],
    *,
    deadline: float,
    step_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PgAdminUnavailableError()
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    if context is not None and step_id is not None:
        from odoo_instance_sdk.exceptions import UnplannedStepError
        from odoo_instance_sdk.internal.proc import ProcessResult

        captured = context.prepared(step_id)
        if captured.argv != tuple(args):
            raise UnplannedStepError(step_id)
        result = context.process_prepared(captured)
        if not isinstance(result, ProcessResult):
            raise PgAdminUnavailableError()
        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        return subprocess.CompletedProcess(list(args), result.returncode, stdout, stderr)
    try:
        result = runner.run(args, timeout=remaining)
    except (OSError, subprocess.SubprocessError, TypeError):
        raise PgAdminUnavailableError() from None
    if not isinstance(result, subprocess.CompletedProcess):
        raise PgAdminUnavailableError()
    return result


def inspect_container(
    runner: ComposeRunner,
    name: str,
    *,
    deadline: float,
    missing_ok: bool = False,
    step_id: str | None = None,
) -> dict[str, JsonValue] | None:
    result = run_docker(
        runner,
        ["docker", "inspect", "--format", "json", name],
        deadline=deadline,
        step_id=step_id,
    )
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


def inspect_network(
    runner: ComposeRunner,
    name: str,
    *,
    deadline: float,
    step_id: str | None = None,
) -> dict[str, JsonValue]:
    result = run_docker(
        runner,
        ["docker", "network", "inspect", "--format", "json", name],
        deadline=deadline,
        step_id=step_id,
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


def _mapping(value: JsonValue | Mapping[str, JsonValue] | None, key: str) -> dict[str, JsonValue]:
    if isinstance(value, Mapping):
        nested = value.get(key)
        return nested if isinstance(nested, dict) else {}
    return {}


def _string_mapping(value: dict[str, JsonValue]) -> dict[str, str]:
    return {key: item for key, item in value.items() if isinstance(item, str)}


def container_matches(
    inspected: Mapping[str, JsonValue], preparation: PgAdminPreparation, *, network: str
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
        key, separator, value = str(item).partition("=")
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
    return _mounts_match(
        cast("dict[str, JsonValue]", dict(inspected)), preparation
    ) and _ports_match(cast("dict[str, JsonValue]", dict(inspected)), preparation.port)


def _mounts_match(inspected: dict[str, JsonValue], preparation: PgAdminPreparation) -> bool:
    mounts = _mapping_list(inspected.get("Mounts"))
    actual = {
        (item.get("Source"), item.get("Destination"), item.get("RW") is not True) for item in mounts
    }
    expected = {(str(m.host_path), m.container_path, m.read_only) for m in preparation.mounts}
    return actual == expected


def _ports_match(inspected: dict[str, JsonValue], port: int) -> bool:
    bindings = _mapping(inspected.get("HostConfig"), "PortBindings")
    values = bindings.get("80/tcp")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        return False
    return values[0].get("HostIp") in {"127.0.0.1", "127.0.0.1/32"} and values[0].get(
        "HostPort"
    ) == str(port)


def _mapping_list(value: JsonValue | None) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def create_container(
    runner: ComposeRunner,
    preparation: PgAdminPreparation,
    *,
    network: str,
    deadline: float,
    step_id: str | None = None,
) -> str:
    result = run_docker(
        runner,
        list(_docker_run_argv(preparation, network=network)),
        deadline=deadline,
        step_id=step_id,
    )
    created_id = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else None
    if result.returncode != 0:
        raise _ContainerCreateFailure(created_id)
    if not created_id:
        raise PgAdminUnavailableError()
    return created_id


def remove_partial_container(
    runner: ComposeRunner,
    *,
    container_id: str | None,
    fingerprint: str,
    deadline: float,
    planned: bool = False,
    inspect_step_id: str | None = None,
    remove_step_id: str | None = None,
) -> None:
    if not container_id:
        return
    try:
        inspect_target = PGADMIN_CONTAINER_NAME if planned else container_id
        inspected = inspect_container(
            runner,
            inspect_target,
            deadline=deadline,
            missing_ok=True,
            step_id=inspect_step_id,
        )
        if inspected is None:
            return
        if not planned and get_container_id(inspected) != container_id:
            return
        assert_owned_container(inspected, fingerprint=fingerprint)
        remove_container(
            runner,
            PGADMIN_CONTAINER_NAME if planned else container_id,
            deadline=deadline,
            step_id=remove_step_id,
        )
    except PgAdminUnavailableError:
        return


def refresh_active_pgpass(
    runner: ComposeRunner,
    *,
    container_id: str,
    fingerprint: str,
    deadline: float,
    planned: bool = False,
    step_id: str | None = None,
    inspect_step_id: str | None = None,
) -> None:
    """Refresh pgAdmin's persistent passfile using an ownership-checked ID."""
    inspect_target = PGADMIN_CONTAINER_NAME if planned else container_id
    inspected = inspect_container(
        runner,
        inspect_target,
        deadline=deadline,
        missing_ok=True,
        step_id=inspect_step_id,
    )
    if inspected is None or (not planned and get_container_id(inspected) != container_id):
        raise PgAdminUnavailableError()
    assert_owned_container(inspected, fingerprint=fingerprint)
    result = run_docker(
        runner,
        list(_docker_refresh_argv(PGADMIN_CONTAINER_NAME if planned else container_id)),
        deadline=deadline,
        step_id=step_id,
    )
    if result.returncode != 0:
        raise PgAdminUnavailableError()


def verify_server(
    preparation: PgAdminPreparation,
    database: str,
    *,
    runner: ComposeRunner,
    deadline: float,
    planned: bool = False,
    step_id: str | None = None,
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
        list(
            _docker_verify_argv(PGADMIN_CONTAINER_NAME if planned else preparation.container_name)
        ),
        deadline=deadline,
        step_id=step_id,
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
