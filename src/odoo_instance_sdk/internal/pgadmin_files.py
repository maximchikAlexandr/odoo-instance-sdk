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
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import PgAdminUnavailableError
from odoo_instance_sdk.internal.locks import exclusive_lock_until, pgadmin_lock_path
from odoo_instance_sdk.internal.paths import (
    get_data_root,
    get_pgadmin_data_dir,
    get_pgadmin_private_dir,
    get_pgadmin_root,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult

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


def _skip_acl_step(step_id: str) -> None:
    """Account an ACL branch that is intentionally absent on this path."""
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    if context is not None and context.planned(step_id) and not context.consumed(step_id):
        context.skip(step_id)


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
    # These are private integrity witnesses.  They are never part of the
    # public phase handle or any command projection, but let the continuation
    # reject a changed credential/config inode during a phase gap.
    file_digests: tuple[tuple[str, str], ...] = ()


def pgadmin_mounts(paths: PgAdminPaths) -> tuple[PgAdminMount, ...]:
    """Build the canonical pgAdmin host-to-container mount contract."""
    return (
        PgAdminMount(paths.admin_password, PGADMIN_PASSWORD_DESTINATION, True),
        PgAdminMount(paths.pgpass, PGADMIN_PGPASS_DESTINATION, True),
        PgAdminMount(paths.servers_json, PGADMIN_SERVERS_DESTINATION, True),
        PgAdminMount(paths.data_dir, PGADMIN_DATA_DESTINATION, False),
    )


@dataclass(frozen=True, slots=True)
class PgAdminFingerprintInputs:
    """Private HMAC inputs selected by the locked provisioning phase."""

    fingerprint: str
    key: bytes


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


def _server_identity_bytes(identity: PostgresIdentity, database: str, password: str) -> bytes:
    return json.dumps(
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


def execution_fingerprint_inputs(
    paths: PgAdminPaths, identity: PostgresIdentity, database: str, password: str
) -> PgAdminFingerprintInputs:
    """Select one HMAC key while the caller holds the lifecycle lock.

    This execution-only helper never writes state itself; ``prepare_files``
    persists the selected key atomically before Docker reconciliation begins.
    """
    key = _existing_fingerprint_key(paths) or secrets.token_bytes(_FINGERPRINT_KEY_BYTES)
    fingerprint = hmac.new(
        key, _server_identity_bytes(identity, database, password), hashlib.sha256
    ).hexdigest()
    return PgAdminFingerprintInputs(fingerprint=fingerprint, key=key)


def escape_pgpass_field(value: str | int) -> str:
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
    fingerprint_key: bytes | None = None,
) -> PgAdminPreparation:
    _validate_port(port)
    _validate_non_secret_text(fingerprint)
    _prepare_directories(paths)
    _ensure_fingerprint_key(paths, expected_key=fingerprint_key)

    _ensure_secret_file(paths.admin_password, _new_admin_password)
    desired_pgpass = _as_bytes(pgpass)
    servers = _as_bytes(servers_json)
    _ensure_target_secret_file(paths.pgpass, desired_pgpass)
    if paths.servers_json.exists() or paths.servers_json.is_symlink():
        _validate_file(
            paths.servers_json,
            mode=_PRIVATE_FILE_MODE,
            root=paths.private_dir,
            acl_step_id="pgadmin.acl.servers.existing",
        )
    else:
        _skip_acl_step("pgadmin.acl.servers.existing")
    _atomic_write(
        paths.servers_json,
        servers,
        mode=_PRIVATE_FILE_MODE,
        final_acl_step_id="pgadmin.acl.servers.final.set",
    )
    metadata = json.dumps(
        {"fingerprint": fingerprint, "port": port},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if paths.metadata.exists() or paths.metadata.is_symlink():
        _validate_file(
            paths.metadata,
            mode=_PRIVATE_FILE_MODE,
            root=paths.private_dir,
            acl_step_id="pgadmin.acl.metadata.existing",
        )
    else:
        _skip_acl_step("pgadmin.acl.metadata.existing")
    _atomic_write(
        paths.metadata,
        metadata,
        mode=_PRIVATE_FILE_MODE,
        final_acl_step_id="pgadmin.acl.metadata.final.set",
    )

    _validate_file(
        paths.admin_password,
        mode=_PRIVATE_FILE_MODE,
        root=paths.private_dir,
        acl_step_id="pgadmin.acl.admin.final",
    )
    _validate_file(
        paths.pgpass,
        mode=_PRIVATE_FILE_MODE,
        root=paths.private_dir,
        acl_step_id="pgadmin.acl.pgpass.final",
    )
    _validate_file(
        paths.servers_json,
        mode=_PRIVATE_FILE_MODE,
        root=paths.private_dir,
        acl_step_id="pgadmin.acl.servers.final",
    )
    _validate_file(
        paths.metadata,
        mode=_PRIVATE_FILE_MODE,
        root=paths.private_dir,
        acl_step_id="pgadmin.acl.metadata.final",
    )
    mounts = pgadmin_mounts(paths)
    _validate_mounts(mounts, paths)
    return PgAdminPreparation(
        paths=paths,
        fingerprint=fingerprint,
        port=port,
        container_name=PGADMIN_CONTAINER_NAME,
        mounts=mounts,
        file_digests=_preparation_file_digests(paths),
    )


def _fingerprint_key_path(paths: PgAdminPaths) -> Path:
    return paths.private_dir / _FINGERPRINT_KEY_NAME


def _existing_fingerprint_key(paths: PgAdminPaths) -> bytes | None:
    try:
        key = _fingerprint_key_path(paths).read_bytes()
    except OSError:
        return None
    return key if len(key) == _FINGERPRINT_KEY_BYTES else None


def preparation_revalidation_steps(paths: PgAdminPaths) -> tuple[PreparedStep, ...]:
    """Capture the read-only filesystem checks before reconciliation Docker IO."""
    if not _linux():
        return ()
    from odoo_instance_sdk.internal.proc import PreparedStep

    checks = (
        ("pgadmin.reconciliation.preparation.acl.root", paths.root),
        ("pgadmin.reconciliation.preparation.acl.private", paths.private_dir),
        ("pgadmin.reconciliation.preparation.acl.data", paths.data_dir),
        ("pgadmin.reconciliation.preparation.acl.data.default", paths.data_dir),
        ("pgadmin.reconciliation.preparation.acl.fingerprint-key", _fingerprint_key_path(paths)),
        ("pgadmin.reconciliation.preparation.acl.admin-password", paths.admin_password),
        ("pgadmin.reconciliation.preparation.acl.pgpass", paths.pgpass),
        ("pgadmin.reconciliation.preparation.acl.servers", paths.servers_json),
        ("pgadmin.reconciliation.preparation.acl.metadata", paths.metadata),
    )
    return tuple(
        PreparedStep(
            step_id=step_id,
            argv=("getfacl", "-cp", str(path)),
            read_only=True,
        )
        for step_id, path in checks
    )


def revalidate_preparation(
    preparation: PgAdminPreparation, *, expected_fingerprint_key: bytes
) -> bool:
    """Revalidate captured private state without creating or mutating anything.

    ``False`` denotes a changed key.  Other filesystem changes are reported as
    the same fail-closed ``PgAdminUnavailableError`` boundary by the caller.
    The key bytes are compared before ACL subprocesses so a direct key drift
    has no process side effect at all.
    """
    paths = preparation.paths
    # Compare the captured key before touching any other saved-state probe.
    # This keeps the common phase-gap drift path entirely in-memory after the
    # one required lstat/read and, importantly, before ACL or Docker work.
    key_path = _fingerprint_key_path(paths)
    _validate_path(
        key_path, root=paths.private_dir, expected_type="file", mode=_FINGERPRINT_KEY_MODE
    )
    try:
        persisted_key = key_path.read_bytes()
    except OSError:
        raise PgAdminUnavailableError() from None
    if persisted_key != expected_fingerprint_key:
        return False

    _validate_port(preparation.port)
    _validate_non_secret_text(preparation.fingerprint)
    if preparation.container_name != PGADMIN_CONTAINER_NAME:
        raise PgAdminUnavailableError()
    _validate_mounts(preparation.mounts, paths)

    data_root = get_data_root(ensure_exists=False)
    _validate_path(
        paths.root, root=data_root, expected_type="directory", mode=_PRIVATE_DIRECTORY_MODE
    )
    _validate_path(
        paths.private_dir,
        root=paths.root,
        expected_type="directory",
        mode=_PRIVATE_DIRECTORY_MODE,
    )
    _validate_path(
        paths.data_dir, root=paths.root, expected_type="directory", mode=_DATA_DIRECTORY_MODE
    )

    if _linux():
        _validate_acl(
            paths.root,
            _directory_acl(_PRIVATE_DIRECTORY_MODE),
            step_id="pgadmin.reconciliation.preparation.acl.root",
        )
        _validate_acl(
            paths.private_dir,
            _directory_acl(_PRIVATE_DIRECTORY_MODE),
            step_id="pgadmin.reconciliation.preparation.acl.private",
        )
        _validate_acl(
            paths.data_dir,
            _directory_acl(_DATA_DIRECTORY_MODE),
            step_id="pgadmin.reconciliation.preparation.acl.data",
        )
        _validate_acl(
            paths.data_dir,
            _default_directory_acl(),
            default=True,
            step_id="pgadmin.reconciliation.preparation.acl.data.default",
        )
        _validate_acl(
            key_path,
            _fingerprint_key_acl(),
            step_id="pgadmin.reconciliation.preparation.acl.fingerprint-key",
        )

    _validate_file(
        paths.admin_password,
        mode=_PRIVATE_FILE_MODE,
        root=paths.private_dir,
        acl_step_id="pgadmin.reconciliation.preparation.acl.admin-password",
    )
    _validate_file(
        paths.pgpass,
        mode=_PRIVATE_FILE_MODE,
        root=paths.private_dir,
        acl_step_id="pgadmin.reconciliation.preparation.acl.pgpass",
    )
    _validate_file(
        paths.servers_json,
        mode=_PRIVATE_FILE_MODE,
        root=paths.private_dir,
        acl_step_id="pgadmin.reconciliation.preparation.acl.servers",
    )
    _validate_file(
        paths.metadata,
        mode=_PRIVATE_FILE_MODE,
        root=paths.private_dir,
        acl_step_id="pgadmin.reconciliation.preparation.acl.metadata",
    )
    if (
        preparation.file_digests
        and tuple(_preparation_file_digests(paths)) != preparation.file_digests
    ):
        raise PgAdminUnavailableError()
    return True


def _preparation_file_digests(paths: PgAdminPaths) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(path), _sha256_file(path))
        for path in (paths.admin_password, paths.pgpass, paths.servers_json, paths.metadata)
    )


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise PgAdminUnavailableError() from None


def _ensure_fingerprint_key(paths: PgAdminPaths, *, expected_key: bytes | None = None) -> None:
    path = _fingerprint_key_path(paths)
    if expected_key is not None and len(expected_key) != _FINGERPRINT_KEY_BYTES:
        raise PgAdminUnavailableError()
    if path.exists() or path.is_symlink():
        _validate_file(
            path,
            mode=_FINGERPRINT_KEY_MODE,
            root=paths.private_dir,
            validate_acl=False,
        )
        try:
            existing_key = path.read_bytes()
        except OSError:
            raise PgAdminUnavailableError() from None
        if len(existing_key) != _FINGERPRINT_KEY_BYTES:
            raise PgAdminUnavailableError()
        if expected_key is not None and existing_key != expected_key:
            raise PgAdminUnavailableError()
        return
    key = expected_key or secrets.token_bytes(_FINGERPRINT_KEY_BYTES)
    _atomic_write(
        path,
        key,
        mode=_FINGERPRINT_KEY_MODE,
        validate_acl=False,
    )


def _prepare_directories(paths: PgAdminPaths) -> None:
    data_root = get_data_root(ensure_exists=True)
    _assert_contained(paths.root, data_root)
    _ensure_directory(
        paths.root, mode=_PRIVATE_DIRECTORY_MODE, root=data_root, default_acl=False, label="root"
    )
    _ensure_directory(
        paths.private_dir,
        mode=_PRIVATE_DIRECTORY_MODE,
        root=paths.root,
        default_acl=False,
        label="private",
    )
    _ensure_directory(
        paths.data_dir, mode=_DATA_DIRECTORY_MODE, root=paths.root, default_acl=True, label="data"
    )


def _ensure_directory(path: Path, *, mode: int, root: Path, default_acl: bool, label: str) -> None:
    _assert_contained(path, root)
    if path.exists() and path.is_symlink():
        raise PgAdminUnavailableError()
    existed = path.exists()
    path.mkdir(mode=mode, parents=False, exist_ok=True)
    if not existed:
        os.chmod(path, mode)
        if _linux():
            _set_acl(path, _directory_acl(mode), step_id=f"pgadmin.acl.{label}.set")
            if default_acl:
                _set_acl(
                    path,
                    _default_directory_acl(),
                    default=True,
                    step_id="pgadmin.acl.data.default.set",
                )
    elif _linux():
        _skip_acl_step(f"pgadmin.acl.{label}.set")
        if default_acl:
            _skip_acl_step("pgadmin.acl.data.default.set")
    _validate_directory(path, mode=mode, root=root, default_acl=default_acl, label=label)


def _validate_directory(
    path: Path, *, mode: int, root: Path, default_acl: bool, label: str
) -> None:
    _validate_path(path, root=root, expected_type="directory", mode=mode)
    if _linux():
        _validate_acl(path, _directory_acl(mode), step_id=f"pgadmin.acl.{label}.validate")
        if default_acl:
            _validate_acl(
                path,
                _default_directory_acl(),
                default=True,
                step_id=f"pgadmin.acl.{label}.default.validate",
            )


def _ensure_secret_file(path: Path, producer: Callable[[], bytes]) -> None:
    if path.exists() or path.is_symlink():
        _validate_file(
            path,
            mode=_PRIVATE_FILE_MODE,
            root=path.parent,
            acl_step_id="pgadmin.acl.admin.existing",
        )
        _skip_acl_step("pgadmin.acl.admin.final.set")
        return
    _skip_acl_step("pgadmin.acl.admin.existing")
    if not callable(producer):
        raise PgAdminUnavailableError()
    _atomic_write(
        path,
        _as_bytes(producer()),
        mode=_PRIVATE_FILE_MODE,
        final_acl_step_id="pgadmin.acl.admin.final.set",
    )


def _ensure_target_secret_file(path: Path, desired: bytes) -> None:
    """Reuse or atomically replace a target-specific credential file."""
    if path.exists() or path.is_symlink():
        _validate_file(
            path,
            mode=_PRIVATE_FILE_MODE,
            root=path.parent,
            acl_step_id="pgadmin.acl.pgpass.existing",
        )
        try:
            current = path.read_bytes()
        except OSError:
            raise PgAdminUnavailableError() from None
        if current == desired:
            _skip_acl_step("pgadmin.acl.pgpass.final.set")
            return
    else:
        _skip_acl_step("pgadmin.acl.pgpass.existing")
    _atomic_write(
        path,
        desired,
        mode=_PRIVATE_FILE_MODE,
        final_acl_step_id="pgadmin.acl.pgpass.final.set",
    )


def _atomic_write(
    path: Path,
    content: bytes,
    *,
    mode: int,
    validate_acl: bool = True,
    final_acl_step_id: str | None = None,
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
        from odoo_instance_sdk.internal.proc import active_context

        context = active_context()
        if _linux() and validate_acl and context is None:
            _set_acl(temporary_path, _file_acl())
            _validate_file(temporary_path, mode=mode, root=path.parent)
        else:
            # Temporary inode names are intentionally not part of a command's
            # immutable snapshot.  The final path is ACL-validated below when
            # this operation is running under a captured command.
            _validate_file(temporary_path, mode=mode, root=path.parent, validate_acl=False)
        os.replace(temporary_path, path)
        if _linux() and validate_acl and context is not None:
            if final_acl_step_id is None:
                raise PgAdminUnavailableError()  # noqa: TRY301
            _set_acl(path, _file_acl(), step_id=final_acl_step_id)
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
    acl_step_id: str | None = None,
) -> None:
    _validate_path(path, root=root, expected_type="file", mode=mode)
    if _linux() and validate_acl:
        _validate_acl(path, _file_acl(), step_id=acl_step_id)


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


def _validate_acl(
    path: Path,
    expected: frozenset[str],
    *,
    default: bool = False,
    step_id: str | None = None,
) -> None:
    if not _linux():
        return
    try:
        from odoo_instance_sdk.internal.proc import ProcessExecutionError

        result = _run_acl_process(
            ["getfacl", "-cp", str(path)],
            step_id=step_id,
            read_only=True,
        )
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


def _set_acl(
    path: Path,
    entries: frozenset[str],
    *,
    default: bool = False,
    step_id: str | None = None,
) -> None:
    if not _linux():
        return
    if not all(shutil.which(tool) for tool in _ACL_TOOLS):
        raise PgAdminUnavailableError()
    try:
        from odoo_instance_sdk.internal.proc import ProcessExecutionError

        command = ["setfacl"]
        if default:
            command.append("--default")
        command.extend(["--set", ",".join(sorted(entries)), str(path)])
        result = _run_acl_process(
            command,
            step_id=step_id,
            mutating=True,
        )
        if result.returncode != 0:
            raise PgAdminUnavailableError()
    except (OSError, ProcessExecutionError, subprocess.SubprocessError):
        raise PgAdminUnavailableError() from None


def _run_acl_process(
    command: list[str],
    *,
    step_id: str | None,
    read_only: bool = False,
    mutating: bool = False,
) -> ProcessResult:
    """Consume a captured ACL step instead of rebuilding ambient inputs."""
    from odoo_instance_sdk.internal.proc import ProcessResult, active_context, run_captured

    context = active_context()
    if context is not None:
        if step_id is None:
            from odoo_instance_sdk.exceptions import UnplannedStepError

            raise UnplannedStepError("pgadmin ACL process requires captured step_id")
        captured = context.prepared(step_id)
        if captured.argv != tuple(command):
            from odoo_instance_sdk.exceptions import UnplannedStepError

            raise UnplannedStepError(step_id)
        result = context.process_prepared(captured)
    else:
        result = run_captured(
            command,
            text=True,
            step_id=step_id or "pgadmin.acl.process",
            read_only=read_only,
            mutating=mutating,
        )
    if not isinstance(result, ProcessResult):
        raise PgAdminUnavailableError()
    return result


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


def _fingerprint_key_acl() -> frozenset[str]:
    return frozenset({"user::rw-", "group::---", "other::---"})


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
