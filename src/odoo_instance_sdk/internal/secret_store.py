"""Small SQLCipher-backed store for administrator-managed credentials.

This module intentionally owns the DBAPI boundary and the two record shapes.
It is not an ORM, repository, or service layer: Admin and credential
resolvers call the methods on :class:`SecretStore` directly.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import importlib
import json
import os
import secrets
import stat
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, cast

from odoo_instance_sdk.exceptions import (
    GitLabCredentialError,
    InvalidBaseUrlError,
    SecretStoreBusyError,
    SecretStoreConflictError,
    SecretStoreCorruptError,
    SecretStoreError,
    SecretStoreIdentityConflictError,
    SecretStoreRecordError,
    SecretStoreUnavailableError,
)
from odoo_instance_sdk.internal.gitlab_credentials import (
    normalize_gitlab_host,
    normalize_member_id,
)
from odoo_instance_sdk.internal.paths import (
    get_secret_key_path,
    get_secret_store_path,
    get_user_root,
)
from odoo_instance_sdk.internal.urls import normalize_base_url

_PRIVATE_MODE = 0o600
_PRIVATE_DIRECTORY_MODE = 0o700
_KEY_BYTES = 32
_BUSY_TIMEOUT_MS = 5000
_SCHEMA_VERSION = 1
_TOKEN_KEY_PREFIX = "ODCLI_GITLAB_TOKEN_"
_PRIVATE_BITS = stat.S_IRWXG | stat.S_IRWXO
_ADMIN_ID_PREFIX = "odcli1."
_ADMIN_ID_MAX_LENGTH = 2048

type _SqlValue = str | int | float | bytes | None
type _SqlRow = tuple[_SqlValue, ...]


class _Cursor(Protocol):
    def fetchone(self) -> _SqlRow | None: ...

    def fetchall(self) -> list[_SqlRow]: ...


class _Connection(Protocol):
    def execute(self, sql: str, parameters: Sequence[_SqlValue] = ()) -> _Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


class _Driver(Protocol):
    def connect(self, filename: str, *, timeout: float = ...) -> _Connection: ...


@dataclass(frozen=True, slots=True)
class AdminSecretIdentity:
    kind: Literal["remote-odoo", "gitlab-member"]
    components: tuple[str, ...]
    revision: int


def _encode_admin_id(
    kind: Literal["remote-odoo", "gitlab-member"], components: tuple[str, ...], revision: int
) -> str:
    if revision < 1:
        raise ValueError("Admin identity revision must be positive")
    raw = json.dumps([kind, components, revision], separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{_ADMIN_ID_PREFIX}{encoded}"


def decode_admin_id(value: str) -> AdminSecretIdentity | None:
    """Decode one canonical, bounded Admin identity without consulting secrets."""
    if (
        not isinstance(value, str)
        or len(value) > _ADMIN_ID_MAX_LENGTH
        or not value.startswith(_ADMIN_ID_PREFIX)
    ):
        return None
    encoded = value[len(_ADMIN_ID_PREFIX) :]
    if not encoded or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for char in encoded
    ):
        return None
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list) or len(payload) != 3:
        return None
    kind, raw_components, revision = payload
    if not isinstance(kind, str) or kind not in {"remote-odoo", "gitlab-member"}:
        return None
    kind = cast("Literal['remote-odoo', 'gitlab-member']", kind)
    expected_count = {"remote-odoo": 1, "gitlab-member": 2}[kind]
    if (
        expected_count is None
        or not isinstance(raw_components, list)
        or len(raw_components) != expected_count
        or any(not isinstance(component, str) or not component for component in raw_components)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
    ):
        return None
    components = tuple(raw_components)
    try:
        normalized = (
            (normalize_base_url(components[0]),)
            if kind == "remote-odoo"
            else (normalize_member_id(components[0]), normalize_gitlab_host(components[1]))
        )
    except (GitLabCredentialError, InvalidBaseUrlError, TypeError, ValueError):
        return None
    identity = AdminSecretIdentity(kind, components, revision)
    if normalized != components or _encode_admin_id(kind, components, revision) != value:
        return None
    return identity


@dataclass(frozen=True, slots=True, repr=False)
class RemoteOdooSecret:
    """Safe Admin projection of one remote Odoo record."""

    base_url: str
    revision: int
    created_at: str
    updated_at: str

    @property
    def admin_id(self) -> str:
        return _encode_admin_id("remote-odoo", (self.base_url,), self.revision)

    def __repr__(self) -> str:
        return (
            f"RemoteOdooSecret(base_url={self.base_url!r}, revision={self.revision!r}, "
            f"created_at={self.created_at!r}, updated_at={self.updated_at!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class GitLabMemberSecret:
    """Safe Admin projection of one GitLab record."""

    member_id: str
    host: str
    login: str
    revision: int
    created_at: str
    updated_at: str

    @property
    def admin_id(self) -> str:
        return _encode_admin_id("gitlab-member", (self.member_id, self.host), self.revision)

    def __repr__(self) -> str:
        return (
            f"GitLabMemberSecret(member_id={self.member_id!r}, host={self.host!r}, "
            f"login={self.login!r}, "
            f"revision={self.revision!r}, created_at={self.created_at!r}, "
            f"updated_at={self.updated_at!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RemoteOdooCredential:
    """Private resolver result; the password never appears in ``repr``."""

    base_url: str
    master_password: str = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return f"RemoteOdooCredential(base_url={self.base_url!r})"


@dataclass(frozen=True, slots=True, repr=False)
class GitLabMemberCredential:
    """Private resolver result; the token never appears in ``repr``."""

    member_id: str
    host: str
    login: str
    token: str = field(repr=False, compare=False)
    token_key: str

    def __repr__(self) -> str:
        return (
            f"GitLabMemberCredential(member_id={self.member_id!r}, host={self.host!r}, "
            f"login={self.login!r}, token_key={self.token_key!r})"
        )


def _load_driver() -> _Driver:
    try:
        module = importlib.import_module("sqlcipher3.dbapi2")
    except (ImportError, ModuleNotFoundError) as exc:
        raise SecretStoreUnavailableError(
            "SQLCipher support is unavailable; install the dashboard extra"
        ) from exc
    return cast("_Driver", module)


def _private_directory(path: Path) -> None:
    try:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if nofollow == 0 or not hasattr(os, "getuid"):
            raise SecretStoreUnavailableError("owner-only secret-store reads are unavailable")
        path.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SecretStoreUnavailableError("secret-store directory is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise SecretStoreUnavailableError("secret-store directory is not owner-only")
        if stat.S_IMODE(metadata.st_mode) & _PRIVATE_BITS:
            raise SecretStoreUnavailableError("secret-store directory is not owner-only")
    finally:
        os.close(descriptor)


def _private_file(path: Path, *, label: str) -> None:
    try:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if nofollow == 0 or not hasattr(os, "getuid"):
            raise SecretStoreUnavailableError("owner-only secret-store reads are unavailable")
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise SecretStoreCorruptError(f"{label} is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise SecretStoreCorruptError(f"{label} is unsafe")
        if stat.S_IMODE(metadata.st_mode) & _PRIVATE_BITS:
            raise SecretStoreCorruptError(f"{label} is unsafe")
    finally:
        os.close(descriptor)


def _atomic_key(path: Path) -> bytes:
    _private_directory(path.parent)
    key = secrets.token_bytes(_KEY_BYTES)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            _PRIVATE_MODE,
        )
        os.write(descriptor, key)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(temporary, path)
        except FileExistsError:
            return _read_key(path)
        os.unlink(temporary)
        return key  # noqa: TRY300 -- the temporary inode is removed in the finally block.
    except OSError as exc:
        raise SecretStoreUnavailableError("secret-store key cannot be initialized") from exc
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary.unlink()


def _read_key(path: Path) -> bytes:
    _private_file(path, label="secret-store key")
    try:
        key = path.read_bytes()
    except OSError as exc:
        raise SecretStoreCorruptError("secret-store key is unreadable") from exc
    if len(key) != _KEY_BYTES:
        raise SecretStoreCorruptError("secret-store key is invalid")
    return key


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds")


def _required(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise SecretStoreRecordError(f"{label} must be nonblank")
    return value


def _revision(value: _SqlValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SecretStoreRecordError("secret-store revision is invalid")
    return value


def _text(value: _SqlValue, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SecretStoreRecordError(f"secret-store {label} is invalid")
    return value


class SecretStore:
    """Direct encrypted store for the two supported administrator identities."""

    def __init__(
        self,
        *,
        user_root: Path | str | None = None,
        key_path: Path | str | None = None,
        store_path: Path | str | None = None,
        busy_timeout_ms: int = _BUSY_TIMEOUT_MS,
    ) -> None:
        root = (
            get_user_root(ensure_exists=False)
            if user_root is None
            else Path(user_root).expanduser().absolute()
        )
        self.key_path = (
            get_secret_key_path(ensure_exists=False)
            if key_path is None and user_root is None
            else Path(key_path).expanduser()
            if key_path is not None
            else root / "config" / "secrets.key"
        )
        self.store_path = (
            get_secret_store_path(ensure_exists=False)
            if store_path is None and user_root is None
            else Path(store_path).expanduser()
            if store_path is not None
            else root / "secrets.sqlite3"
        )
        if busy_timeout_ms < 1 or busy_timeout_ms > 60_000:
            raise ValueError("busy_timeout_ms must be between 1 and 60000")
        self._busy_timeout_ms = busy_timeout_ms

    def _connect(self, *, write: bool) -> _Connection | None:
        store_exists = self.store_path.exists() or self.store_path.is_symlink()
        key_exists = self.key_path.exists() or self.key_path.is_symlink()
        if not store_exists and not key_exists:
            if not write:
                return None
            driver = _load_driver()
            _private_directory(self.store_path.parent)
            key = _atomic_key(self.key_path)
            try:
                descriptor = os.open(
                    self.store_path,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    _PRIVATE_MODE,
                )
                os.close(descriptor)
            except FileExistsError:
                pass
            except OSError as exc:
                raise SecretStoreUnavailableError(
                    "secret-store database cannot be initialized"
                ) from exc
            connection = self._open(driver, key)
            self._initialize_schema(connection)
            return connection
        if not store_exists or not key_exists:
            raise SecretStoreCorruptError("secret-store key and database must exist together")
        _private_directory(self.store_path.parent)
        key = _read_key(self.key_path)
        return self._open(_load_driver(), key)

    def _open(self, driver: _Driver, key: bytes) -> _Connection:
        _private_file(self.store_path, label="secret-store database")
        connection: _Connection | None = None
        try:
            connection = driver.connect(str(self.store_path), timeout=self._busy_timeout_ms / 1000)
            connection.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            version = connection.execute("PRAGMA cipher_version").fetchone()
            if version is None or not isinstance(version[0], str) or not version[0]:
                raise SecretStoreCorruptError("SQLCipher support is not active")  # noqa: TRY301
            return connection  # noqa: TRY300
        except SecretStoreError:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()
            raise
        except Exception as exc:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()
            raise SecretStoreCorruptError("secret-store database cannot be opened") from exc

    @staticmethod
    def _initialize_schema(connection: _Connection) -> None:
        try:
            connection.execute(
                "CREATE TABLE secret_metadata (schema_version INTEGER NOT NULL, "
                "singleton INTEGER PRIMARY KEY CHECK (singleton = 1))"
            )
            connection.execute(
                "CREATE TABLE remote_odoo_secret ("
                "base_url TEXT PRIMARY KEY NOT NULL, master_password TEXT NOT NULL, "
                "revision INTEGER NOT NULL CHECK (revision > 0), created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE gitlab_member_secret ("
                "member_id TEXT NOT NULL, host TEXT NOT NULL, login TEXT NOT NULL, "
                "token TEXT NOT NULL, token_key TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL "
                "CHECK (revision > 0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "PRIMARY KEY (member_id, host))"
            )
            connection.execute(
                "INSERT INTO secret_metadata (schema_version, singleton) VALUES (?, 1)",
                (_SCHEMA_VERSION,),
            )
            connection.commit()
        except SecretStoreError:
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                connection.rollback()
            connection.close()
            raise SecretStoreCorruptError("secret-store schema cannot be initialized") from exc

    @staticmethod
    def _validate_schema(connection: _Connection) -> None:
        try:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            names = tuple(_text(row[0], label="table name") for row in tables)
            expected = ("gitlab_member_secret", "remote_odoo_secret", "secret_metadata")
            if names != expected:
                raise SecretStoreCorruptError("secret-store schema is invalid")  # noqa: TRY301
            metadata = connection.execute(
                "SELECT schema_version, singleton FROM secret_metadata"
            ).fetchall()
            if len(metadata) != 1 or metadata[0] != (_SCHEMA_VERSION, 1):
                raise SecretStoreCorruptError("secret-store schema is invalid")  # noqa: TRY301
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreCorruptError("secret-store schema is unreadable") from exc

    def _ready(self, *, write: bool) -> _Connection | None:
        connection = self._connect(write=write)
        if connection is None:
            return None
        try:
            self._validate_schema(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    @staticmethod
    def _translate(exc: Exception) -> SecretStoreError:
        message = str(exc).casefold()
        if "locked" in message or "busy" in message:
            return SecretStoreBusyError("secret-store database is busy; retry")
        if "constraint" in message or "unique" in message:
            return SecretStoreIdentityConflictError("secret-store identity already exists")
        return SecretStoreCorruptError("secret-store operation failed")

    def get_remote_odoo(self, base_url: str) -> RemoteOdooSecret | None:
        normalized = normalize_base_url(base_url)
        connection = self._ready(write=False)
        if connection is None:
            return None
        try:
            rows = connection.execute(
                "SELECT base_url, revision, created_at, updated_at, master_password "
                "FROM remote_odoo_secret WHERE base_url = ?",
                (normalized,),
            ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise SecretStoreCorruptError("secret-store identity is duplicated")
            row = rows[0]
            _validate_remote_row(row)
            return RemoteOdooSecret(
                base_url=_text(row[0], label="remote URL"),
                revision=_revision(row[1]),
                created_at=_text(row[2], label="created timestamp"),
                updated_at=_text(row[3], label="updated timestamp"),
            )
        finally:
            connection.close()

    def list_remote_odoo(self) -> tuple[RemoteOdooSecret, ...]:
        """Return safe remote Odoo projections without decrypting fields."""
        connection = self._ready(write=False)
        if connection is None:
            return ()
        try:
            rows = connection.execute(
                "SELECT base_url, revision, created_at, updated_at, master_password "
                "FROM remote_odoo_secret ORDER BY base_url ASC"
            ).fetchall()
            result: list[RemoteOdooSecret] = []
            for row in rows:
                _validate_remote_row(row)
                result.append(
                    RemoteOdooSecret(
                        base_url=_text(row[0], label="remote URL"),
                        revision=_revision(row[1]),
                        created_at=_text(row[2], label="created timestamp"),
                        updated_at=_text(row[3], label="updated timestamp"),
                    )
                )
            return tuple(result)
        finally:
            connection.close()

    def lookup_remote_odoo(self, base_url: str) -> RemoteOdooCredential | None:
        normalized = normalize_base_url(base_url)
        connection = self._ready(write=False)
        if connection is None:
            return None
        try:
            rows = connection.execute(
                "SELECT base_url, revision, created_at, updated_at, master_password "
                "FROM remote_odoo_secret WHERE base_url = ?",
                (normalized,),
            ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise SecretStoreCorruptError("secret-store identity is duplicated")
            row = rows[0]
            _validate_remote_row(row)
            return RemoteOdooCredential(
                base_url=_text(row[0], label="remote URL"),
                master_password=_required(
                    _text(row[4], label="master password"), label="master password"
                ),
            )
        finally:
            connection.close()

    def create_remote_odoo(self, base_url: str, master_password: str) -> RemoteOdooSecret:
        normalized = normalize_base_url(base_url)
        password = _required(master_password, label="master password")
        connection = self._ready(write=True)
        assert connection is not None
        timestamp = _now()
        try:
            connection.execute(
                "INSERT INTO remote_odoo_secret "
                "(base_url, master_password, revision, created_at, updated_at) "
                "VALUES (?, ?, 1, ?, ?)",
                (normalized, password, timestamp, timestamp),
            )
            connection.commit()
        except Exception as exc:
            with contextlib.suppress(Exception):
                connection.rollback()
            raise self._translate(exc) from exc
        finally:
            connection.close()
        return RemoteOdooSecret(normalized, 1, timestamp, timestamp)

    def update_remote_odoo(
        self,
        base_url: str,
        *,
        revision: int,
        new_base_url: str | None = None,
        master_password: str = "",
    ) -> RemoteOdooSecret:
        normalized = normalize_base_url(base_url)
        proposed = normalize_base_url(new_base_url) if new_base_url is not None else normalized
        if revision < 1:
            raise SecretStoreConflictError("secret-store revision is stale")
        connection = self._ready(write=True)
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision, created_at, master_password FROM remote_odoo_secret "
                "WHERE base_url = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                raise SecretStoreRecordError("remote Odoo secret is not present")  # noqa: TRY301
            current = _revision(row[0])
            if current != revision:
                raise SecretStoreConflictError(  # noqa: TRY301
                    "secret-store revision is stale; reload and retry"
                )
            timestamp = _now()
            password = (
                _required(master_password, label="master password")
                if master_password.strip()
                else _text(row[2], label="master password")
            )
            connection.execute(
                "UPDATE remote_odoo_secret SET base_url = ?, master_password = ?, "
                "revision = ?, updated_at = ? WHERE base_url = ? AND revision = ?",
                (proposed, password, current + 1, timestamp, normalized, current),
            )
            connection.commit()
            return RemoteOdooSecret(
                proposed,
                current + 1,
                _text(row[1], label="created timestamp"),
                timestamp,
            )
        except SecretStoreError:
            with contextlib.suppress(Exception):
                connection.rollback()
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                connection.rollback()
            raise self._translate(exc) from exc
        finally:
            connection.close()

    def delete_remote_odoo(self, base_url: str, *, revision: int) -> None:
        normalized = normalize_base_url(base_url)
        self._delete("remote_odoo_secret", (normalized,), revision, "base_url = ?")

    def get_gitlab_member(self, member_id: str, host: str) -> GitLabMemberSecret | None:
        normalized_member = normalize_member_id(member_id)
        normalized_host = normalize_gitlab_host(host)
        connection = self._ready(write=False)
        if connection is None:
            return None
        try:
            rows = connection.execute(
                "SELECT member_id, host, login, token_key, revision, created_at, updated_at, token "
                "FROM gitlab_member_secret WHERE member_id = ? AND host = ?",
                (normalized_member, normalized_host),
            ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise SecretStoreCorruptError("secret-store identity is duplicated")
            row = rows[0]
            _validate_gitlab_row(row)
            return _gitlab_projection(row)
        finally:
            connection.close()

    def list_gitlab_members(self) -> tuple[GitLabMemberSecret, ...]:
        """Return safe GitLab projections without token or token-key fields."""
        connection = self._ready(write=False)
        if connection is None:
            return ()
        try:
            rows = connection.execute(
                "SELECT member_id, host, login, token_key, revision, created_at, "
                "updated_at, token FROM gitlab_member_secret ORDER BY host ASC, member_id ASC"
            ).fetchall()
            result: list[GitLabMemberSecret] = []
            for row in rows:
                _validate_gitlab_row(row)
                result.append(_gitlab_projection(row))
            return tuple(result)
        finally:
            connection.close()

    def lookup_gitlab_member(self, member_id: str, host: str) -> GitLabMemberCredential | None:
        normalized_member = normalize_member_id(member_id)
        normalized_host = normalize_gitlab_host(host)
        connection = self._ready(write=False)
        if connection is None:
            return None
        try:
            rows = connection.execute(
                "SELECT member_id, host, login, token_key, revision, created_at, updated_at, token "
                "FROM gitlab_member_secret WHERE member_id = ? AND host = ?",
                (normalized_member, normalized_host),
            ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise SecretStoreCorruptError("secret-store identity is duplicated")
            row = rows[0]
            _validate_gitlab_row(row)
            return GitLabMemberCredential(
                member_id=_text(row[0], label="member ID"),
                host=_text(row[1], label="host"),
                login=_text(row[2], label="login"),
                token=_required(_text(row[7], label="token"), label="token"),
                token_key=_text(row[3], label="token key"),
            )
        finally:
            connection.close()

    def create_gitlab_member(
        self, member_id: str, host: str, login: str, token: str
    ) -> GitLabMemberSecret:
        normalized_member = normalize_member_id(member_id)
        normalized_host = normalize_gitlab_host(host)
        safe_login = _required(login, label="GitLab login")
        safe_token = _required(token, label="GitLab token")
        token_key = f"{_TOKEN_KEY_PREFIX}{secrets.token_hex(16).upper()}"
        timestamp = _now()
        connection = self._ready(write=True)
        assert connection is not None
        try:
            connection.execute(
                "INSERT INTO gitlab_member_secret "
                "(member_id, host, login, token, token_key, revision, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    normalized_member,
                    normalized_host,
                    safe_login,
                    safe_token,
                    token_key,
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
        except Exception as exc:
            with contextlib.suppress(Exception):
                connection.rollback()
            raise self._translate(exc) from exc
        finally:
            connection.close()
        return GitLabMemberSecret(
            member_id=normalized_member,
            host=normalized_host,
            login=safe_login,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def update_gitlab_member(
        self,
        member_id: str,
        host: str,
        *,
        revision: int,
        new_member_id: str | None = None,
        new_host: str | None = None,
        login: str,
        token: str = "",
    ) -> GitLabMemberSecret:
        normalized_member = normalize_member_id(member_id)
        normalized_host = normalize_gitlab_host(host)
        proposed_member = (
            normalize_member_id(new_member_id) if new_member_id is not None else normalized_member
        )
        proposed_host = normalize_gitlab_host(new_host) if new_host is not None else normalized_host
        safe_login = _required(login, label="GitLab login")
        if revision < 1:
            raise SecretStoreConflictError("secret-store revision is stale")
        connection = self._ready(write=True)
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT login, token_key, revision, created_at, token FROM gitlab_member_secret "
                "WHERE member_id = ? AND host = ?",
                (normalized_member, normalized_host),
            ).fetchone()
            if row is None:
                raise SecretStoreRecordError("GitLab secret is not present")  # noqa: TRY301
            current = _revision(row[2])
            if current != revision:
                raise SecretStoreConflictError(  # noqa: TRY301
                    "secret-store revision is stale; reload and retry"
                )
            timestamp = _now()
            safe_token = (
                _required(token, label="GitLab token")
                if token.strip()
                else _text(row[4], label="GitLab token")
            )
            connection.execute(
                "UPDATE gitlab_member_secret SET member_id = ?, host = ?, login = ?, token = ?, "
                "revision = ?, updated_at = ? WHERE member_id = ? AND host = ? AND revision = ?",
                (
                    proposed_member,
                    proposed_host,
                    safe_login,
                    safe_token,
                    current + 1,
                    timestamp,
                    normalized_member,
                    normalized_host,
                    current,
                ),
            )
            connection.commit()
            return GitLabMemberSecret(
                member_id=proposed_member,
                host=proposed_host,
                login=safe_login,
                revision=current + 1,
                created_at=_text(row[3], label="created timestamp"),
                updated_at=timestamp,
            )
        except SecretStoreError:
            with contextlib.suppress(Exception):
                connection.rollback()
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                connection.rollback()
            raise self._translate(exc) from exc
        finally:
            connection.close()

    def delete_gitlab_member(self, member_id: str, host: str, *, revision: int) -> None:
        normalized_member = normalize_member_id(member_id)
        normalized_host = normalize_gitlab_host(host)
        self._delete(
            "gitlab_member_secret",
            (normalized_member, normalized_host),
            revision,
            "member_id = ? AND host = ?",
        )

    def _delete(
        self,
        table: str,
        identity: tuple[str, ...],
        revision: int,
        where: str,
    ) -> None:
        if revision < 1:
            raise SecretStoreConflictError("secret-store revision is stale")
        connection = self._ready(write=True)
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT revision FROM {table} WHERE {where}", identity
            ).fetchone()
            if row is None:
                raise SecretStoreRecordError("secret is not present")  # noqa: TRY301
            if _revision(row[0]) != revision:
                raise SecretStoreConflictError(  # noqa: TRY301
                    "secret-store revision is stale; reload and retry"
                )
            connection.execute(
                f"DELETE FROM {table} WHERE {where} AND revision = ?", (*identity, revision)
            )
            connection.commit()
        except SecretStoreError:
            with contextlib.suppress(Exception):
                connection.rollback()
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                connection.rollback()
            raise self._translate(exc) from exc
        finally:
            connection.close()


def _validate_remote_row(row: _SqlRow) -> None:
    if len(row) != 5:
        raise SecretStoreRecordError("remote Odoo secret record is invalid")
    try:
        normalize_base_url(_text(row[0], label="remote URL"))
    except SecretStoreError:
        raise
    except Exception as exc:
        raise SecretStoreRecordError("remote Odoo secret record is invalid") from exc
    _revision(row[1])
    _text(row[2], label="created timestamp")
    _text(row[3], label="updated timestamp")
    _required(_text(row[4], label="master password"), label="master password")


def _validate_gitlab_row(row: _SqlRow) -> None:
    if len(row) != 8:
        raise SecretStoreRecordError("GitLab secret record is invalid")
    try:
        normalize_member_id(_text(row[0], label="member ID"))
        normalize_gitlab_host(_text(row[1], label="host"))
    except SecretStoreError:
        raise
    except Exception as exc:
        raise SecretStoreRecordError("GitLab secret record is invalid") from exc
    _required(_text(row[2], label="login"), label="GitLab login")
    token_key = _text(row[3], label="token key")
    if not token_key.startswith(_TOKEN_KEY_PREFIX) or len(token_key) != len(_TOKEN_KEY_PREFIX) + 32:
        raise SecretStoreRecordError("GitLab token key is invalid")
    _revision(row[4])
    _text(row[5], label="created timestamp")
    _text(row[6], label="updated timestamp")
    _required(_text(row[7], label="token"), label="GitLab token")


def _gitlab_projection(row: _SqlRow) -> GitLabMemberSecret:
    return GitLabMemberSecret(
        member_id=_text(row[0], label="member ID"),
        host=_text(row[1], label="host"),
        login=_text(row[2], label="login"),
        revision=_revision(row[4]),
        created_at=_text(row[5], label="created timestamp"),
        updated_at=_text(row[6], label="updated timestamp"),
    )


__all__ = [
    "GitLabMemberCredential",
    "GitLabMemberSecret",
    "RemoteOdooCredential",
    "RemoteOdooSecret",
    "SecretStore",
]
