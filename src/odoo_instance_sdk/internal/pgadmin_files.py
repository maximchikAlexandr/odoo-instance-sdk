"""Private pgAdmin paths, credential files, and ACL validation."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from odoo_instance_sdk.exceptions import PgAdminUnavailableError
from odoo_instance_sdk.internal.locks import exclusive_lock_until, pgadmin_lock_path
from odoo_instance_sdk.internal.paths import (
    get_data_root,
    get_pgadmin_data_dir,
    get_pgadmin_private_dir,
    get_pgadmin_root,
)

PGADMIN_RUNTIME_UID = 5050
PGADMIN_CONTAINER_NAME = "odoo-instance-sdk-pgadmin"
PGADMIN_CONTAINER_PORT = 80
PGADMIN_PASSWORD_DESTINATION = "/run/odoo-instance-sdk/pgadmin-admin-password"
PGADMIN_PGPASS_DESTINATION = "/run/odoo-instance-sdk/pgpass"
PGADMIN_SERVERS_DESTINATION = "/pgadmin4/servers.json"
PGADMIN_DATA_DESTINATION = "/var/lib/pgadmin"
PGADMIN_IMAGE = "docker.io/dpage/pgadmin4@sha256:cefc4cc6b7d9d71a9e81e76fb9d7e14038ab5661b539a190eea1b63fa612589a"
PGADMIN_DEFAULT_PORT = 5050
PGADMIN_DEFAULT_EMAIL = "odoo-instance-sdk@localhost.invalid"
PGADMIN_LABEL_MANAGED = "io.odoo-instance-sdk.managed"
PGADMIN_LABEL_FINGERPRINT = "io.odoo-instance-sdk.fingerprint"
PGADMIN_LABEL_NETWORK = "io.odoo-instance-sdk.network"

_PRIVATE_DIRECTORY_MODE = 0o710
_DATA_DIRECTORY_MODE = 0o770
_PRIVATE_FILE_MODE = 0o640
_FINGERPRINT_KEY_MODE = 0o600
_FINGERPRINT_KEY_NAME = ".fingerprint-key"
_FINGERPRINT_KEY_BYTES = 32
_ACL_TOOLS = ("getfacl", "setfacl")


@dataclass(frozen=True, slots=True)
class PgAdminPaths:
    """Deterministic user-global pgAdmin host paths."""

    root: Path
    private_dir: Path
    data_dir: Path
    admin_password: Path
    pgpass: Path
    servers_json: Path
    metadata: Path
    lock: Path

    @classmethod
    def from_defaults(cls) -> PgAdminPaths:
        root = get_pgadmin_root()
        private_dir = get_pgadmin_private_dir()
        return cls(
            root=root,
            private_dir=private_dir,
            data_dir=get_pgadmin_data_dir(),
            admin_password=private_dir / "admin-password",
            pgpass=private_dir / ".pgpass",
            servers_json=private_dir / "servers.json",
            metadata=private_dir / "metadata.json",
            lock=pgadmin_lock_path(),
        )


@dataclass(frozen=True, slots=True)
class PgAdminMount:
    """A validated host-to-container mount contract."""

    host_path: Path
    container_path: str
    read_only: bool


@dataclass(frozen=True, slots=True)
class PgAdminPreparation:
    """Prepared files and exact mount metadata for the later Docker task."""

    paths: PgAdminPaths
    fingerprint: str
    port: int
    container_name: str
    mounts: tuple[PgAdminMount, ...]


@dataclass(frozen=True, slots=True)
class PostgresIdentity:
    container_name: str
    network: str
    user: str
    host: str
    port: int = 5432


@contextlib.contextmanager
def pgadmin_lock(*, timeout: float = 30.0, path: Path | None = None) -> Iterator[int]:
    """Serialize all user-global pgAdmin file and lifecycle operations."""
    deadline = time.monotonic() + max(0.1, timeout)
    with exclusive_lock_until(path or pgadmin_lock_path(), deadline) as fd:
        yield fd


def server_json(identity: PostgresIdentity, database: str) -> bytes:
    payload = {
        "Servers": {
            "1": {
                "Name": "Odoo",
                "Group": "Odoo",
                "Host": identity.host,
                "Port": identity.port,
                "Username": identity.user,
                "MaintenanceDB": database,
                "DBRestriction": database,
            }
        }
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def server_fingerprint(
    paths: PgAdminPaths, identity: PostgresIdentity, database: str, password: str
) -> str:
    """Return an HMAC identity keyed by the private per-user fingerprint key."""
    _prepare_directories(paths)
    key = _read_fingerprint_key(paths)
    identity_bytes = json.dumps(
        {
            "database": database,
            "host": identity.host,
            "network": identity.network,
            "password": password,
            "port": identity.port,
            "user": identity.user,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hmac.new(key, identity_bytes, hashlib.sha256).hexdigest()


def escape_pgpass_field(value: object) -> str:
    """Escape a libpq passfile field without exposing its value elsewhere."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def pgpass_line(identity: PostgresIdentity, password: str) -> str:
    return (
        ":".join(
            escape_pgpass_field(value)
            for value in (identity.host, identity.port, "*", identity.user, password)
        )
        + "\n"
    )


def select_port(paths: PgAdminPaths) -> int:
    try:
        metadata = json.loads(paths.metadata.read_text(encoding="utf-8"))
        port = metadata.get("port")
        if isinstance(port, int) and 1 <= port <= 65535:
            return port
    except (OSError, ValueError, AttributeError):
        pass
    return PGADMIN_DEFAULT_PORT


def prepare_files(
    *,
    servers_json: str | bytes,
    pgpass: str | bytes,
    fingerprint: str,
    port: int,
    paths: PgAdminPaths,
) -> PgAdminPreparation:
    _validate_port(port)
    _validate_non_secret_text(fingerprint)
    _prepare_directories(paths)
    _ensure_fingerprint_key(paths)

    _ensure_secret_file(paths.admin_password, _new_admin_password)
    desired_pgpass = _as_bytes(pgpass)
    servers = _as_bytes(servers_json)
    _ensure_target_secret_file(paths.pgpass, desired_pgpass)
    if paths.servers_json.exists() or paths.servers_json.is_symlink():
        _validate_file(paths.servers_json, mode=_PRIVATE_FILE_MODE, root=paths.private_dir)
    _atomic_write(paths.servers_json, servers, mode=_PRIVATE_FILE_MODE)
    metadata = json.dumps(
        {"fingerprint": fingerprint, "port": port},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if paths.metadata.exists() or paths.metadata.is_symlink():
        _validate_file(paths.metadata, mode=_PRIVATE_FILE_MODE, root=paths.private_dir)
    _atomic_write(paths.metadata, metadata, mode=_PRIVATE_FILE_MODE)

    _validate_file(paths.admin_password, mode=_PRIVATE_FILE_MODE, root=paths.private_dir)
    _validate_file(paths.pgpass, mode=_PRIVATE_FILE_MODE, root=paths.private_dir)
    _validate_file(paths.servers_json, mode=_PRIVATE_FILE_MODE, root=paths.private_dir)
    _validate_file(paths.metadata, mode=_PRIVATE_FILE_MODE, root=paths.private_dir)
    mounts = (
        PgAdminMount(paths.admin_password, PGADMIN_PASSWORD_DESTINATION, True),
        PgAdminMount(paths.pgpass, PGADMIN_PGPASS_DESTINATION, True),
        PgAdminMount(paths.servers_json, PGADMIN_SERVERS_DESTINATION, True),
        PgAdminMount(paths.data_dir, PGADMIN_DATA_DESTINATION, False),
    )
    _validate_mounts(mounts, paths)
    return PgAdminPreparation(
        paths=paths,
        fingerprint=fingerprint,
        port=port,
        container_name=PGADMIN_CONTAINER_NAME,
        mounts=mounts,
    )


def _fingerprint_key_path(paths: PgAdminPaths) -> Path:
    return paths.private_dir / _FINGERPRINT_KEY_NAME


def _read_fingerprint_key(paths: PgAdminPaths) -> bytes:
    key_path = _fingerprint_key_path(paths)
    _ensure_fingerprint_key(paths)
    try:
        key = key_path.read_bytes()
    except OSError:
        raise PgAdminUnavailableError() from None
    if len(key) != _FINGERPRINT_KEY_BYTES:
        raise PgAdminUnavailableError()
    return key


def _ensure_fingerprint_key(paths: PgAdminPaths) -> None:
    path = _fingerprint_key_path(paths)
    if path.exists() or path.is_symlink():
        _validate_file(
            path,
            mode=_FINGERPRINT_KEY_MODE,
            root=paths.private_dir,
            validate_acl=False,
        )
        try:
            if len(path.read_bytes()) != _FINGERPRINT_KEY_BYTES:
                raise PgAdminUnavailableError()
        except OSError:
            raise PgAdminUnavailableError() from None
        return
    _atomic_write(
        path,
        secrets.token_bytes(_FINGERPRINT_KEY_BYTES),
        mode=_FINGERPRINT_KEY_MODE,
        validate_acl=False,
    )


def _prepare_directories(paths: PgAdminPaths) -> None:
    data_root = get_data_root(ensure_exists=True)
    _assert_contained(paths.root, data_root)
    _ensure_directory(paths.root, mode=_PRIVATE_DIRECTORY_MODE, root=data_root, default_acl=False)
    _ensure_directory(
        paths.private_dir, mode=_PRIVATE_DIRECTORY_MODE, root=paths.root, default_acl=False
    )
    _ensure_directory(paths.data_dir, mode=_DATA_DIRECTORY_MODE, root=paths.root, default_acl=True)


def _ensure_directory(path: Path, *, mode: int, root: Path, default_acl: bool) -> None:
    _assert_contained(path, root)
    if path.exists() and path.is_symlink():
        raise PgAdminUnavailableError()
    existed = path.exists()
    path.mkdir(mode=mode, parents=False, exist_ok=True)
    if not existed:
        os.chmod(path, mode)
        if _linux():
            _set_acl(path, _directory_acl(mode))
            if default_acl:
                _set_acl(path, _default_directory_acl(), default=True)
    _validate_directory(path, mode=mode, root=root, default_acl=default_acl)


def _validate_directory(path: Path, *, mode: int, root: Path, default_acl: bool) -> None:
    _validate_path(path, root=root, expected_type="directory", mode=mode)
    if _linux():
        _validate_acl(path, _directory_acl(mode))
        if default_acl:
            _validate_acl(path, _default_directory_acl(), default=True)


def _ensure_secret_file(path: Path, producer: object) -> None:
    if path.exists() or path.is_symlink():
        _validate_file(path, mode=_PRIVATE_FILE_MODE, root=path.parent)
        return
    if not callable(producer):
        raise PgAdminUnavailableError()
    _atomic_write(path, _as_bytes(producer()), mode=_PRIVATE_FILE_MODE)


def _ensure_target_secret_file(path: Path, desired: bytes) -> None:
    """Reuse or atomically replace a target-specific credential file."""
    if path.exists() or path.is_symlink():
        _validate_file(path, mode=_PRIVATE_FILE_MODE, root=path.parent)
        try:
            current = path.read_bytes()
        except OSError:
            raise PgAdminUnavailableError() from None
        if current == desired:
            return
    _atomic_write(path, desired, mode=_PRIVATE_FILE_MODE)


def _atomic_write(
    path: Path,
    content: bytes,
    *,
    mode: int,
    validate_acl: bool = True,
) -> None:
    path.parent.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        if _linux() and validate_acl:
            _set_acl(temporary_path, _file_acl())
        _validate_file(
            temporary_path,
            mode=mode,
            root=path.parent,
            validate_acl=validate_acl,
        )
        os.replace(temporary_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            temporary_path.unlink()
        raise


def _validate_mounts(mounts: tuple[PgAdminMount, ...], paths: PgAdminPaths) -> None:
    expected = {
        paths.admin_password: (PGADMIN_PASSWORD_DESTINATION, True),
        paths.pgpass: (PGADMIN_PGPASS_DESTINATION, True),
        paths.servers_json: (PGADMIN_SERVERS_DESTINATION, True),
        paths.data_dir: (PGADMIN_DATA_DESTINATION, False),
    }
    if len(mounts) != len(expected):
        raise PgAdminUnavailableError()
    for mount in mounts:
        if expected.get(mount.host_path) != (mount.container_path, mount.read_only):
            raise PgAdminUnavailableError()


def _validate_file(
    path: Path,
    *,
    mode: int,
    root: Path,
    validate_acl: bool = True,
) -> None:
    _validate_path(path, root=root, expected_type="file", mode=mode)
    if _linux() and validate_acl:
        _validate_acl(path, _file_acl())


def _validate_path(path: Path, *, root: Path, expected_type: str, mode: int) -> None:
    _assert_contained(path, root)
    try:
        info = os.lstat(path)
    except OSError:
        raise PgAdminUnavailableError() from None
    if stat.S_ISLNK(info.st_mode):
        raise PgAdminUnavailableError()
    if expected_type == "file" and not stat.S_ISREG(info.st_mode):
        raise PgAdminUnavailableError()
    if expected_type == "directory" and not stat.S_ISDIR(info.st_mode):
        raise PgAdminUnavailableError()
    if stat.S_IMODE(info.st_mode) != mode:
        raise PgAdminUnavailableError()
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise PgAdminUnavailableError()


def _assert_contained(path: Path, root: Path) -> None:
    try:
        path.absolute().relative_to(root.absolute())
    except ValueError:
        raise PgAdminUnavailableError() from None
    current = root
    try:
        relative = path.absolute().relative_to(root.absolute())
        for component in relative.parts:
            current /= component
            if current.is_symlink():
                raise PgAdminUnavailableError()
    except OSError:
        raise PgAdminUnavailableError() from None


def _validate_acl(path: Path, expected: frozenset[str], *, default: bool = False) -> None:
    if not _linux():
        return
    try:
        from odoo_instance_sdk.internal.proc import ProcessExecutionError, run_captured

        result = run_captured(["getfacl", "-cp", str(path)], text=True)
        if result.returncode != 0:
            raise PgAdminUnavailableError()
    except (OSError, ProcessExecutionError, subprocess.SubprocessError):
        raise PgAdminUnavailableError() from None
    prefix = "default:" if default else ""
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    actual = frozenset(
        line.removeprefix(prefix)
        for line in stdout.splitlines()
        if line
        and not line.startswith("#")
        and (line.startswith(prefix) if default else not line.startswith("default:"))
    )
    if actual != expected:
        raise PgAdminUnavailableError()


def _set_acl(path: Path, entries: frozenset[str], *, default: bool = False) -> None:
    if not _linux():
        return
    if not all(shutil.which(tool) for tool in _ACL_TOOLS):
        raise PgAdminUnavailableError()
    try:
        from odoo_instance_sdk.internal.proc import ProcessExecutionError, run_captured

        command = ["setfacl"]
        if default:
            command.append("--default")
        command.extend(["--set", ",".join(sorted(entries)), str(path)])
        result = run_captured(command, text=True)
        if result.returncode != 0:
            raise PgAdminUnavailableError()
    except (OSError, ProcessExecutionError, subprocess.SubprocessError):
        raise PgAdminUnavailableError() from None


def _directory_acl(mode: int) -> frozenset[str]:
    uid = "rwx" if mode == _DATA_DIRECTORY_MODE else "--x"
    mask = "rwx" if mode == _DATA_DIRECTORY_MODE else "--x"
    return frozenset(
        {
            "user::rwx",
            f"user:{PGADMIN_RUNTIME_UID}:{uid}",
            "group::---",
            f"mask::{mask}",
            "other::---",
        }
    )


def _default_directory_acl() -> frozenset[str]:
    return frozenset(
        {
            "user::rwx",
            f"user:{PGADMIN_RUNTIME_UID}:rwx",
            "group::---",
            "mask::rwx",
            "other::---",
        }
    )


def _file_acl() -> frozenset[str]:
    return frozenset(
        {
            "user::rw-",
            f"user:{PGADMIN_RUNTIME_UID}:r--",
            "group::---",
            "mask::r--",
            "other::---",
        }
    )


def _new_admin_password() -> bytes:
    return f"{secrets.token_urlsafe(32)}\n".encode("ascii")


def _as_bytes(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def _validate_non_secret_text(value: str) -> None:
    if not value or len(value) > 128 or any(char in value for char in "\r\n"):
        raise PgAdminUnavailableError()


def _validate_port(port: int) -> None:
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise PgAdminUnavailableError()


def _linux() -> bool:
    return sys.platform.startswith("linux")
