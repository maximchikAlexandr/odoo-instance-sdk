from __future__ import annotations

import json
import subprocess

import pytest

from odoo_instance_sdk.exceptions import PgAdminUnavailableError
from odoo_instance_sdk.internal import pgadmin_container, pgadmin_files
from odoo_instance_sdk.models import PgAdminOpenState


def test_reconcile_reuses_matching_owned_container_without_recreate(
    local_paths: pgadmin_files.PgAdminPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparation = pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json='{"Servers":{"1":{"MaintenanceDB":"demo","DBRestriction":"demo"}}}',
        pgpass="*:*:*:*:secret\n",
        fingerprint="f" * 64,
        port=5050,
    )
    network = "odcli_pg_project_default"
    inspected = {
        "Config": {
            "Image": pgadmin_files.PGADMIN_IMAGE,
            "User": str(pgadmin_files.PGADMIN_RUNTIME_UID),
            "Env": [
                "PATH=/usr/local/bin:/usr/bin",
                "PGADMIN_CONFIG_SERVER_MODE=False",
                "PGADMIN_REPLACE_SERVERS_ON_STARTUP=True",
                f"PGADMIN_DEFAULT_EMAIL={pgadmin_files.PGADMIN_DEFAULT_EMAIL}",
                f"PGADMIN_DEFAULT_PASSWORD_FILE={pgadmin_files.PGADMIN_PASSWORD_DESTINATION}",
                f"PGPASS_FILE={pgadmin_files.PGADMIN_PGPASS_DESTINATION}",
            ],
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: "f" * 64,
                pgadmin_files.PGADMIN_LABEL_NETWORK: network,
            },
        },
        "State": {"Running": True},
        "NetworkSettings": {"Networks": {network: {}}},
        "Mounts": [
            {
                "Source": str(mount.host_path),
                "Destination": mount.container_path,
                "RW": not mount.read_only,
            }
            for mount in preparation.mounts
        ],
        "HostConfig": {"PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5050"}]}},
    }
    monkeypatch.setattr(pgadmin_container, "inspect_container", lambda *args, **kwargs: inspected)
    monkeypatch.setattr(pgadmin_container, "_wait_ready", lambda port, *, deadline: None)
    monkeypatch.setattr(
        pgadmin_container, "verify_server", lambda preparation, database, **kwargs: None
    )
    monkeypatch.setattr(
        pgadmin_container, "create_container", lambda *args, **kwargs: pytest.fail("recreated")
    )
    result = pgadmin_container.reconcile_container(
        preparation,
        runner=object(),
        network=network,
        database="demo",
        deadline=1e12,
    )
    assert result.state is PgAdminOpenState.REUSED


def test_matching_container_rejects_conflicting_duplicate_sdk_environment(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    preparation = pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json='{"Servers":{"1":{}}}',
        pgpass="*:*:*:*:secret\n",
        fingerprint="f" * 64,
        port=5050,
    )
    network = "odcli_pg_project_default"
    inspected = {
        "Config": {
            "Image": pgadmin_files.PGADMIN_IMAGE,
            "User": str(pgadmin_files.PGADMIN_RUNTIME_UID),
            "Env": [
                "PGADMIN_CONFIG_SERVER_MODE=False",
                "PGADMIN_CONFIG_SERVER_MODE=True",
                "PGADMIN_REPLACE_SERVERS_ON_STARTUP=True",
                f"PGADMIN_DEFAULT_EMAIL={pgadmin_files.PGADMIN_DEFAULT_EMAIL}",
                f"PGADMIN_DEFAULT_PASSWORD_FILE={pgadmin_files.PGADMIN_PASSWORD_DESTINATION}",
                f"PGPASS_FILE={pgadmin_files.PGADMIN_PGPASS_DESTINATION}",
            ],
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: "f" * 64,
                pgadmin_files.PGADMIN_LABEL_NETWORK: network,
            },
        },
        "State": {"Running": True},
        "NetworkSettings": {"Networks": {network: {}}},
        "Mounts": [
            {
                "Source": str(mount.host_path),
                "Destination": mount.container_path,
                "RW": not mount.read_only,
            }
            for mount in preparation.mounts
        ],
        "HostConfig": {"PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5050"}]}},
    }
    assert not pgadmin_container.container_matches(inspected, preparation, network=network)


def test_reconcile_reconfigures_owned_container_when_fingerprint_changes(
    local_paths: pgadmin_files.PgAdminPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparation = pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json=(
            '{"Servers":{"1":{"Host":"postgres-1","Port":5432,'
            '"Username":"odoo","MaintenanceDB":"new-db","DBRestriction":"new-db"}}}'
        ),
        pgpass="*:*:*:*:secret\n",
        fingerprint="f" * 64,
        port=5050,
    )
    network = "odcli_pg_project_default"
    inspected = {
        "Id": "old-pgadmin-id",
        "Config": {
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: "old-fingerprint",
            }
        },
    }
    recreated = {
        "Id": "new-pgadmin-id",
        "Config": {
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: preparation.fingerprint,
            }
        },
    }
    inspections = iter((inspected, inspected, recreated))
    monkeypatch.setattr(
        pgadmin_container, "inspect_container", lambda *args, **kwargs: next(inspections)
    )
    monkeypatch.setattr(
        pgadmin_container, "create_container", lambda *args, **kwargs: "new-pgadmin-id"
    )
    monkeypatch.setattr(pgadmin_container, "_wait_ready", lambda port, *, deadline: None)

    class Runner:
        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            assert args[1:2] in (["rm"], ["exec"])
            if args[1:2] == ["exec"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    json.dumps(
                        [
                            {
                                "host": "postgres-1",
                                "port": 5432,
                                "username": "odoo",
                                "maintenance_db": "new-db",
                                "db_res": "new-db",
                            }
                        ]
                    ),
                    "",
                )
            return subprocess.CompletedProcess(args, 0, "", "")

    result = pgadmin_container.reconcile_container(
        preparation,
        runner=Runner(),
        network=network,
        database="new-db",
        deadline=1e12,
    )

    assert result.state is PgAdminOpenState.RECONFIGURED


def test_inspect_missing_ok_does_not_hide_daemon_errors() -> None:
    class Runner:
        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, 1, "", "permission denied")

    with pytest.raises(PgAdminUnavailableError):
        pgadmin_container.inspect_container(Runner(), "pgadmin", deadline=1e12, missing_ok=True)


def test_failed_create_cleanup_reinspects_created_id_and_never_removes_by_name(
    local_paths: pgadmin_files.PgAdminPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparation = pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json="{}",
        pgpass="*:*:*:*:secret\n",
        fingerprint="f" * 64,
        port=5050,
    )
    calls: list[list[str]] = []
    inspected = {
        "Id": "created-id",
        "Name": f"/{pgadmin_files.PGADMIN_CONTAINER_NAME}",
        "Config": {
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: preparation.fingerprint,
            }
        },
    }

    class Runner:
        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            if args[1:3] == ["inspect", "--format"]:
                if args[-1] == pgadmin_files.PGADMIN_CONTAINER_NAME:
                    return subprocess.CompletedProcess(args, 1, "", "No such object")
                assert args[-1] == "created-id"
                return subprocess.CompletedProcess(args, 0, json.dumps([inspected]), "")
            if args[1:2] == ["run"]:
                return subprocess.CompletedProcess(args, 1, "created-id\n", "startup failed")
            if args[1:2] == ["rm"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            pytest.fail(f"unexpected docker argv: {args}")

    monkeypatch.setattr(pgadmin_container, "_wait_ready", lambda *_args, **_kwargs: None)
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_container.reconcile_container(
            preparation,
            runner=Runner(),
            network="network",
            database="demo",
            deadline=1e12,
        )
    assert [call[-1] for call in calls if call[1:2] == ["rm"]] == ["created-id"]


def test_reconfiguration_race_leaves_replaced_foreign_container_untouched(
    local_paths: pgadmin_files.PgAdminPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparation = pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json="{}",
        pgpass="*:*:*:*:secret\n",
        fingerprint="new" * 16,
        port=5050,
    )
    own: dict[str, object] = {
        "Id": "old-id",
        "Config": {
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: "old-fingerprint",
            }
        },
    }
    foreign: dict[str, object] = {"Id": "foreign-id", "Config": {"Labels": {}}}
    inspect_count = 0
    calls: list[list[str]] = []

    def inspect(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal inspect_count
        inspect_count += 1
        return own if inspect_count == 1 else foreign

    monkeypatch.setattr(pgadmin_container, "inspect_container", inspect)

    class Runner:
        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            pytest.fail(f"unexpected docker argv: {args}")

    with pytest.raises(PgAdminUnavailableError):
        pgadmin_container.reconcile_container(
            preparation,
            runner=Runner(),
            network="network",
            database="demo",
            deadline=1e12,
        )
    assert not any(call[1:2] == ["rm"] for call in calls)


def test_same_backend_password_rotation_recreates_and_refreshes_active_passfile(
    local_paths: pgadmin_files.PgAdminPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres",
        network="project_default",
        host="postgres",
        user="odoo",
    )
    old_password = "old-password"
    new_password = "new-password"
    old_preparation = pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json=pgadmin_files.server_json(identity, "demo"),
        pgpass=pgadmin_files.pgpass_line(identity, old_password),
        fingerprint=pgadmin_files.server_fingerprint(local_paths, identity, "demo", old_password),
        port=5050,
    )
    new_preparation = pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json=pgadmin_files.server_json(identity, "demo"),
        pgpass=pgadmin_files.pgpass_line(identity, new_password),
        fingerprint=pgadmin_files.server_fingerprint(local_paths, identity, "demo", new_password),
        port=5050,
    )
    assert old_preparation.fingerprint != new_preparation.fingerprint

    old_container = {
        "Id": "old-pgadmin-id",
        "Config": {
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: old_preparation.fingerprint,
            }
        },
    }
    new_container = {
        "Id": "new-pgadmin-id",
        "Config": {
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: new_preparation.fingerprint,
            }
        },
    }
    inspections = iter((old_container, old_container, new_container))
    monkeypatch.setattr(
        pgadmin_container, "inspect_container", lambda *args, **kwargs: next(inspections)
    )
    monkeypatch.setattr(
        pgadmin_container, "create_container", lambda *args, **kwargs: "new-pgadmin-id"
    )
    monkeypatch.setattr(pgadmin_container, "_wait_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pgadmin_container, "verify_server", lambda *_args, **_kwargs: None)
    calls: list[list[str]] = []

    class Runner:
        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

    result = pgadmin_container.reconcile_container(
        new_preparation,
        runner=Runner(),
        network=identity.network,
        database="demo",
        deadline=1e12,
    )

    assert result.state is PgAdminOpenState.RECONFIGURED
    assert [call[-1] for call in calls if call[1:2] == ["rm"]] == ["old-pgadmin-id"]
    refresh = next(call for call in calls if call[1:2] == ["exec"])
    assert refresh[3] == "5050"
    assert refresh[4] == "new-pgadmin-id"
    assert old_password not in " ".join(refresh)
    assert new_password not in " ".join(refresh)


def test_refresh_active_pgpass_is_id_scoped_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprint = "f" * 64
    inspected = {
        "Id": "created-id",
        "Config": {
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: fingerprint,
            }
        },
    }
    monkeypatch.setattr(pgadmin_container, "inspect_container", lambda *args, **kwargs: inspected)
    calls: list[list[str]] = []

    class Runner:
        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

    pgadmin_container.refresh_active_pgpass(
        Runner(), container_id="created-id", fingerprint=fingerprint, deadline=1e12
    )

    refresh = next(call for call in calls if call[1:2] == ["exec"])
    rendered = " ".join(refresh)
    assert refresh[3:5] == [str(pgadmin_files.PGADMIN_RUNTIME_UID), "created-id"]
    assert pgadmin_files.PGADMIN_PGPASS_DESTINATION in rendered
    assert f"{pgadmin_files.PGADMIN_DATA_DESTINATION}/.pgpass" in rendered
    assert "chmod 0600" in rendered
    assert "password" not in rendered.lower()


def test_verify_server_requires_effective_persistent_import(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    preparation = pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json=(
            '{"Servers":{"1":{"Host":"postgres-1","Port":5432,'
            '"Username":"odoo","MaintenanceDB":"demo","DBRestriction":"demo"}}}'
        ),
        pgpass="*:*:*:*:secret\n",
        fingerprint="f" * 64,
        port=5050,
    )

    class Runner:
        def __init__(self, state: str) -> None:
            self.state = state

        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            assert args[1:2] == ["exec"]
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    [
                        {
                            "host": "postgres-1",
                            "port": 5432,
                            "username": "odoo",
                            "maintenance_db": self.state,
                            "db_res": self.state,
                        }
                    ]
                ),
                "",
            )

    pgadmin_container.verify_server(preparation, "demo", runner=Runner("demo"), deadline=1e12)
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_container.verify_server(
            preparation, "demo", runner=Runner("old-database"), deadline=1e12
        )
