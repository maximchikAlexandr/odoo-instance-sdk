from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from odoo_instance_sdk.exceptions import PgAdminUnavailableError
from odoo_instance_sdk.internal import pgadmin_files

from .pgadmin_test_support import _paths


def test_prepare_files_is_deterministic_and_exposes_exact_mount_contract(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    prepared = pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json=b'{"Servers": {}}',
        pgpass="127.0.0.1:5432:odoo:odoo:postgres-secret\n",
        fingerprint="a" * 64,
        port=5050,
    )

    assert prepared.container_name == pgadmin_files.PGADMIN_CONTAINER_NAME
    assert prepared.port == 5050
    assert prepared.paths == local_paths
    assert [(mount.container_path, mount.read_only) for mount in prepared.mounts] == [
        (pgadmin_files.PGADMIN_PASSWORD_DESTINATION, True),
        (pgadmin_files.PGADMIN_PGPASS_DESTINATION, True),
        (pgadmin_files.PGADMIN_SERVERS_DESTINATION, True),
        (pgadmin_files.PGADMIN_DATA_DESTINATION, False),
    ]
    assert local_paths.admin_password.read_bytes().strip()
    assert local_paths.pgpass.read_text() == "127.0.0.1:5432:odoo:odoo:postgres-secret\n"
    assert json.loads(local_paths.metadata.read_text()) == {
        "fingerprint": "a" * 64,
        "port": 5050,
    }
    assert {path.name for path in local_paths.private_dir.iterdir()} == {
        "admin-password",
        ".pgpass",
        ".fingerprint-key",
        "servers.json",
        "metadata.json",
    }
    assert (local_paths.root.stat().st_mode & 0o777) == 0o710
    assert (local_paths.private_dir.stat().st_mode & 0o777) == 0o710
    assert (local_paths.data_dir.stat().st_mode & 0o777) == 0o770
    assert (local_paths.admin_password.stat().st_mode & 0o777) == 0o640
    assert (local_paths.private_dir / ".fingerprint-key").stat().st_mode & 0o777 == 0o600
    assert len((local_paths.private_dir / ".fingerprint-key").read_bytes()) == 32


def test_bootstrap_secret_is_reused_but_target_pgpass_is_replaced_atomically(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json="{}",
        pgpass="*:*:*:*:first-secret\n",
        fingerprint="first",
        port=5050,
    )
    admin_password = local_paths.admin_password.read_bytes()

    pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json='{"Servers": {}}',
        pgpass="*:*:*:*:second-secret\n",
        fingerprint="second",
        port=5051,
    )

    assert local_paths.admin_password.read_bytes() == admin_password
    assert local_paths.pgpass.read_text() == "*:*:*:*:second-secret\n"
    assert local_paths.servers_json.read_text() == '{"Servers": {}}'
    assert json.loads(local_paths.metadata.read_text()) == {
        "fingerprint": "second",
        "port": 5051,
    }


def test_pgpass_target_fields_are_escaped(local_paths: pgadmin_files.PgAdminPaths) -> None:
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres",
        network="project_default",
        host="host:name",
        user="user\\name",
    )
    pgpass = pgadmin_files.pgpass_line(identity, "pa:ss\\word\n")
    pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json="{}",
        pgpass=pgpass,
        fingerprint="first",
        port=5050,
    )
    assert local_paths.pgpass.read_text() == r"host\:name:5432:*:user\\name:pa\:ss\\word\n" + "\n"


def test_atomic_write_secures_temporary_inode_before_replace(
    local_paths: pgadmin_files.PgAdminPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_paths.private_dir.mkdir(parents=True)
    target = local_paths.admin_password
    events: list[tuple[str, Path]] = []
    real_replace = os.replace

    def record_acl(path: Path, _: tuple[str, ...], *, default: bool = False) -> None:
        assert not default
        events.append(("acl", path))

    def record_validation(path: Path, **_: object) -> None:
        events.append(("validate", path))

    def record_replace(source: Path, destination: Path) -> None:
        events.append(("replace", source))
        real_replace(source, destination)

    monkeypatch.setattr(pgadmin_files, "_linux", lambda: True)
    monkeypatch.setattr(pgadmin_files, "_set_acl", record_acl)
    monkeypatch.setattr(pgadmin_files, "_validate_file", record_validation)
    monkeypatch.setattr(os, "replace", record_replace)

    pgadmin_files._atomic_write(target, b"secret", mode=0o640)

    replace_index = next(index for index, event in enumerate(events) if event[0] == "replace")
    assert replace_index == 2
    assert [event[0] for event in events] == ["acl", "validate", "replace"]
    assert events[0][1] == events[1][1] == events[2][1]
    assert events[0][1] != target
    assert events[0][1].parent == target.parent
    assert target.read_bytes() == b"secret"


def test_generated_server_and_metadata_omit_known_password(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres",
        network="project_default",
        host="host:name",
        user="odoo",
    )
    password = "known-password-not-in-declarative-data"
    servers = pgadmin_files.server_json(identity, "demo")
    pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json=servers,
        pgpass=pgadmin_files.pgpass_line(identity, password),
        fingerprint="f" * 64,
        port=5050,
    )
    assert password.encode() not in local_paths.servers_json.read_bytes()
    assert password.encode() not in local_paths.metadata.read_bytes()


def test_fingerprint_uses_private_hmac_key_for_password_rotation(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres",
        network="project_default",
        host="postgres",
        user="odoo",
    )
    old_inputs = pgadmin_files.execution_fingerprint_inputs(local_paths, identity, "demo", "old")
    pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json=pgadmin_files.server_json(identity, "demo"),
        pgpass=pgadmin_files.pgpass_line(identity, "old"),
        fingerprint=old_inputs.fingerprint,
        fingerprint_key=old_inputs.key,
        port=5050,
    )
    old = pgadmin_files.execution_fingerprint_inputs(
        local_paths, identity, "demo", "old"
    ).fingerprint
    new = pgadmin_files.execution_fingerprint_inputs(
        local_paths, identity, "demo", "new"
    ).fingerprint
    key = (local_paths.private_dir / ".fingerprint-key").read_bytes()
    material = json.dumps(
        {
            "database": "demo",
            "host": "postgres",
            "network": "project_default",
            "password": "old",
            "port": 5432,
            "user": "odoo",
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert old == hmac.new(key, material, hashlib.sha256).hexdigest()
    assert old != new
    candidate_key = hashlib.sha256(b"postgres|project_default|5432|odoo|demo|old").digest()
    assert old != hmac.new(candidate_key, material, hashlib.sha256).hexdigest()
    assert hashlib.sha256(b"old").hexdigest() not in old


def test_command_fingerprint_is_keyed_and_non_mutating_before_preparation(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres",
        network="project_default",
        host="postgres",
        user="odoo",
    )

    first = pgadmin_files.execution_fingerprint_inputs(
        local_paths, identity, "demo", "first"
    ).fingerprint
    identical = pgadmin_files.execution_fingerprint_inputs(
        local_paths, identity, "demo", "first"
    ).fingerprint
    second = pgadmin_files.execution_fingerprint_inputs(
        local_paths, identity, "demo", "second"
    ).fingerprint

    # Preview capture is pure and intentionally does not reserve a shared
    # first-run key.  Execution selects the persistent key under the lifecycle
    # lock; independent previews therefore need not be equal.
    assert identical != first
    assert first != second
    assert len(first) == len(second) == 64
    assert all(character in "0123456789abcdef" for character in first + second)
    assert not local_paths.root.exists()
    assert first != hashlib.sha256(b"first").hexdigest()
    assert second != hashlib.sha256(b"second").hexdigest()


def test_first_command_fingerprint_persists_the_captured_private_key(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres",
        network="project_default",
        host="postgres",
        user="odoo",
    )
    captured = pgadmin_files.execution_fingerprint_inputs(local_paths, identity, "demo", "first")

    pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json=pgadmin_files.server_json(identity, "demo"),
        pgpass=pgadmin_files.pgpass_line(identity, "first"),
        fingerprint=captured.fingerprint,
        fingerprint_key=captured.key,
        port=5050,
    )

    assert (local_paths.private_dir / ".fingerprint-key").read_bytes() == captured.key
    assert (
        pgadmin_files.execution_fingerprint_inputs(
            local_paths, identity, "demo", "first"
        ).fingerprint
        == captured.fingerprint
    )


def test_command_fingerprint_uses_existing_private_hmac_key_for_rotation(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres",
        network="project_default",
        host="postgres",
        user="odoo",
    )
    pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json=pgadmin_files.server_json(identity, "demo"),
        pgpass=pgadmin_files.pgpass_line(identity, "first"),
        fingerprint="opaque-first",
        port=5050,
    )

    old = pgadmin_files.execution_fingerprint_inputs(
        local_paths, identity, "demo", "first"
    ).fingerprint
    new = pgadmin_files.execution_fingerprint_inputs(
        local_paths, identity, "demo", "second"
    ).fingerprint

    assert (
        old
        == pgadmin_files.execution_fingerprint_inputs(
            local_paths, identity, "demo", "first"
        ).fingerprint
    )
    assert old != new
    assert "first" not in old
    assert "second" not in new


@pytest.mark.parametrize("password", ["a", "ab"])
def test_valid_short_passwords_are_accepted(
    local_paths: pgadmin_files.PgAdminPaths, password: str
) -> None:
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres",
        network="project_default",
        host="postgres",
        user="odoo",
    )
    pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json=pgadmin_files.server_json(identity, "demo"),
        pgpass=pgadmin_files.pgpass_line(identity, password),
        fingerprint="f" * 64,
        port=5050,
    )


def test_existing_symlink_fails_closed_before_replacement(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    local_paths.private_dir.mkdir(parents=True)
    local_paths.root.mkdir(exist_ok=True)
    local_paths.servers_json.symlink_to(local_paths.private_dir / "outside")

    with pytest.raises(PgAdminUnavailableError):
        pgadmin_files.prepare_files(
            paths=local_paths,
            servers_json="{}",
            pgpass="*:*:*:*:secret\n",
            fingerprint="first",
            port=5050,
        )

    assert local_paths.servers_json.is_symlink()


def test_existing_secret_directory_fails_closed_without_replacement(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    local_paths.private_dir.mkdir(parents=True)
    local_paths.root.mkdir(exist_ok=True)
    local_paths.data_dir.mkdir()
    local_paths.admin_password.mkdir()

    with pytest.raises(PgAdminUnavailableError):
        pgadmin_files.prepare_files(
            paths=local_paths,
            servers_json="{}",
            pgpass="*:*:*:*:secret\n",
            fingerprint="first",
            port=5050,
        )

    assert local_paths.admin_password.is_dir()


def test_unsafe_existing_directory_mode_fails_closed_without_repair(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    local_paths.root.mkdir(parents=True, mode=0o755)
    os.chmod(local_paths.root, 0o755)

    with pytest.raises(PgAdminUnavailableError):
        pgadmin_files.prepare_files(
            paths=local_paths,
            servers_json="{}",
            pgpass="*:*:*:*:secret\n",
            fingerprint="first",
            port=5050,
        )

    assert (local_paths.root.stat().st_mode & 0o777) == 0o755


def test_containment_and_port_validation_fail_closed(
    local_paths: pgadmin_files.PgAdminPaths,
) -> None:
    outside = _paths(local_paths.root.parent.parent / "outside")
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_files.prepare_files(
            paths=outside,
            servers_json="{}",
            pgpass="*:*:*:*:secret\n",
            fingerprint="first",
            port=5050,
        )
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_files.prepare_files(
            paths=local_paths,
            servers_json="{}",
            pgpass="*:*:*:*:secret\n",
            fingerprint="first",
            port=0,
        )


def test_missing_acl_support_fails_closed_before_file_preparation(
    local_paths: pgadmin_files.PgAdminPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: True)
    monkeypatch.setattr(shutil, "which", lambda _: None)

    with pytest.raises(PgAdminUnavailableError):
        pgadmin_files.prepare_files(
            paths=local_paths,
            servers_json="{}",
            pgpass="*:*:*:*:secret\n",
            fingerprint="first",
            port=5050,
        )

    assert not local_paths.private_dir.exists()


def test_linux_acl_validation_rejects_extra_grants(
    local_paths: pgadmin_files.PgAdminPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: True)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/tool")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> SimpleResult:
        calls.append(command)
        if command[0] == "getfacl":
            return SimpleResult(
                stdout=(
                    "user::rwx\nuser:5050:--x\ngroup::---\ngroup:staff:r-x\nmask::r-x\nother::---"
                )
            )
        return SimpleResult(stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_files.prepare_files(
            paths=local_paths,
            servers_json="{}",
            pgpass="*:*:*:*:secret\n",
            fingerprint="first",
            port=5050,
        )
    assert any(command[0] == "setfacl" for command in calls)


def test_linux_acl_layout_and_data_default_acl_are_exact(
    local_paths: pgadmin_files.PgAdminPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: True)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/tool")
    acl_state: dict[tuple[Path, bool], frozenset[str]] = {}
    last_file_acl: frozenset[str] | None = None
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> SimpleResult:
        nonlocal last_file_acl
        calls.append(command)
        if command[0] == "setfacl":
            assert command[1] == "--set" or command[1:3] == ["--default", "--set"]
            set_index = command.index("--set")
            path = Path(command[-1])
            entries = frozenset(command[set_index + 1].split(","))
            assert path in {
                local_paths.root,
                local_paths.private_dir,
                local_paths.data_dir,
            } or path.is_relative_to(local_paths.private_dir)
            assert entries in {
                pgadmin_files._directory_acl(0o710),
                pgadmin_files._directory_acl(0o770),
                pgadmin_files._default_directory_acl(),
                pgadmin_files._file_acl(),
            }
            acl_state[(path, "--default" in command)] = entries
            if "--default" not in command and path not in {
                local_paths.root,
                local_paths.private_dir,
                local_paths.data_dir,
            }:
                last_file_acl = entries
            return SimpleResult(stdout="")
        assert command[0] == "getfacl"
        path = Path(command[-1])
        access = acl_state.get((path, False), last_file_acl)
        assert access is not None
        output = sorted(access)
        default = acl_state.get((path, True))
        if default is not None:
            output.extend(f"default:{entry}" for entry in sorted(default))
        return SimpleResult(stdout="\n".join(output))

    monkeypatch.setattr(subprocess, "run", fake_run)
    prepared = pgadmin_files.prepare_files(
        paths=local_paths,
        servers_json="{}",
        pgpass="*:*:*:*:secret\n",
        fingerprint="first",
        port=5050,
    )

    assert prepared.mounts[-1] == pgadmin_files.PgAdminMount(
        local_paths.data_dir,
        pgadmin_files.PGADMIN_DATA_DESTINATION,
        False,
    )
    setfacl_calls = [command for command in calls if command[0] == "setfacl"]
    assert setfacl_calls[:4] == [
        [
            "setfacl",
            "--set",
            "group::---,mask::--x,other::---,user:5050:--x,user::rwx",
            str(local_paths.root),
        ],
        [
            "setfacl",
            "--set",
            "group::---,mask::--x,other::---,user:5050:--x,user::rwx",
            str(local_paths.private_dir),
        ],
        [
            "setfacl",
            "--set",
            "group::---,mask::rwx,other::---,user:5050:rwx,user::rwx",
            str(local_paths.data_dir),
        ],
        [
            "setfacl",
            "--default",
            "--set",
            "group::---,mask::rwx,other::---,user:5050:rwx,user::rwx",
            str(local_paths.data_dir),
        ],
    ]
    assert len(setfacl_calls) == 8
    for command, target in zip(
        setfacl_calls[4:],
        (
            local_paths.admin_password,
            local_paths.pgpass,
            local_paths.servers_json,
            local_paths.metadata,
        ),
        strict=True,
    ):
        assert command[0:2] == ["setfacl", "--set"]
        assert command[2] == "group::---,mask::r--,other::---,user:5050:r--,user::rw-"
        temporary = Path(command[3])
        assert temporary.parent == target.parent
        assert temporary.name.startswith(f".{target.name}.")
        assert temporary.name.endswith(".tmp")
    assert all(
        command[1:-1]
        in (
            ["--set", "group::---,mask::--x,other::---,user:5050:--x,user::rwx"],
            ["--set", "group::---,mask::rwx,other::---,user:5050:rwx,user::rwx"],
            [
                "--default",
                "--set",
                "group::---,mask::rwx,other::---,user:5050:rwx,user::rwx",
            ],
            ["--set", "group::---,mask::r--,other::---,user:5050:r--,user::rw-"],
        )
        for command in setfacl_calls
    )


class SimpleResult:
    def __init__(self, *, stdout: str) -> None:
        self.stdout = stdout
