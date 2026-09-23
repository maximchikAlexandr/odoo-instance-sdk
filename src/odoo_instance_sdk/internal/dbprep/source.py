from __future__ import annotations

import contextlib
import hashlib
import math
import os
import re
import shutil
import stat
import tempfile
import time
import unicodedata
import uuid
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar, cast, runtime_checkable

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    InstanceConfigurationError,
    MasterPasswordRequiredError,
    PlanJsonValue,
    RemoteDatabaseAmbiguousError,
    RemoteDatabaseListUnavailableError,
    RemoteDatabaseNoneError,
)
from odoo_instance_sdk.internal.db_name import validate_db_name
from odoo_instance_sdk.internal.generated_config import project_generated_config_path
from odoo_instance_sdk.internal.git_worktree import (
    rev_parse_git_common_dir,
    rev_parse_toplevel,
)
from odoo_instance_sdk.internal.locks import (
    database_preparation_lock_path,
    exclusive_lock,
    exclusive_lock_until,
)
from odoo_instance_sdk.internal.project_runtime import (
    is_uv_python_selector,
    resolve_project_runtime,
    resolve_uv_executable,
    uv_run_prefix,
)
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.internal.urls import normalize_base_url
from odoo_instance_sdk.models import (
    Backup,
    BackupBranchOrigin,
    BackupFormat,
    BackupFreshness,
    BackupProvenanceComparison,
    BackupProvenanceStatus,
    DatabasePreparationResult,
    DatabaseRefreshOptions,
    NoBackup,
)
from odoo_instance_sdk.project import ProjectConfig, TestInstanceProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
    )
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster
T = TypeVar("T")

_BRANCH_PREFIX = "refs/heads/"
_MAX_TARGET_BYTES = 63
_TARGET_ATTEMPTS = 100
_TARGET_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_PREPARATION_FIELDS = (
    "test_instance",
    "default_base_ref",
    "refresh_after_hours",
    "source_config",
    "postgres",
    "default_source_database",
)


@runtime_checkable
class DatabaseNameProvider(Protocol):
    """Provides remote database names for optional-database resolution."""

    def names(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class TestSourceResolution:
    config: TestInstanceProjectConfig
    branch: str | None
    origin: BackupBranchOrigin


@dataclass(frozen=True, slots=True)
class _RemoteRestoreSource:
    """Select the existing remote backup source."""


@dataclass(frozen=True, slots=True)
class _CatalogueRestoreSource:
    """Select one exact, already published catalogue backup."""

    backup_id: uuid.UUID


_RestoreSource = _RemoteRestoreSource | _CatalogueRestoreSource


@dataclass(frozen=True, slots=True)
class ProjectRuntimeBinding:
    python_executable: str | None
    odoo_bin: str
    runtime_cwd: Path
    python_selector: str | None = None
    uv_executable: str | None = None

    @property
    def command_prefix(self) -> tuple[str, ...]:
        if self.python_selector is not None:
            return uv_run_prefix(
                self.python_selector,
                uv_executable=self.uv_executable,
                command=("python", self.odoo_bin),
            )
        if self.python_executable is None:
            raise InstanceConfigurationError("project runtime has no Python executable")
        return (self.python_executable, self.odoo_bin)


class DatabasePreparationFailureContext(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
    omit_defaults=True,
):
    """Secret-free identifiers retained after a preparation failure."""

    retained_backup_id: uuid.UUID | None = None
    retained_database: str | None = None
    backup_id: uuid.UUID | None = None
    database_confirmed: bool | None = None
    default_switch_confirmed: bool | None = None
    restore_stage_id: str | None = None
    restore_stage_elapsed: float | None = None


@dataclass(frozen=True, slots=True)
class SelectedBackupRestorePayload:
    """Validated retained-backup inputs for the stopped selected environment.

    This is deliberately owned by the preparation module: replacement reuses
    the same backup validation and process construction boundary as checkout.
    The payload is only materialized while executing the captured restore
    operation, never as part of the public command projection.
    """

    archive_path: Path
    dump_path: Path
    verified_snapshot_path: Path
    filestore_members: tuple[str, ...]
    database_name: str
    file_identity: tuple[int, int, int, int]
    verified_size: int
    verified_sha256: str
    zip_entry_sizes: tuple[tuple[str, int], ...] = ()
    zip_uncompressed_bytes: int = 0
    format: BackupFormat = BackupFormat.ZIP


def _verified_file(path: Path, backup: Backup) -> tuple[tuple[int, int, int, int], str]:
    try:
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError("selected backup is not a regular file")
        if info.st_size != backup.size_bytes:
            raise ConfigError("selected backup size does not match catalogue evidence")
        digest = _sha256_no_follow(path)
    except (OSError, ValueError) as exc:
        raise ConfigError("selected backup file is unavailable") from exc
    actual_digest = digest
    if backup.sha256 and actual_digest != backup.sha256:
        raise ConfigError("selected backup content does not match catalogue evidence")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns), actual_digest


def _sha256_no_follow(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        raise


def _materialize_verified_snapshot(  # noqa: C901
    payload: SelectedBackupRestorePayload,
) -> SelectedBackupRestorePayload:
    """Copy the catalogued source through one no-follow descriptor.

    The descriptor is the immutable boundary between source revalidation and
    restore.  Consumers never reopen the user-controlled catalogue path.
    """
    snapshot = payload.verified_snapshot_path
    if snapshot.exists() or snapshot.is_symlink():
        _assert_verified_snapshot_unchanged(payload)
        return payload
    try:
        if shutil.disk_usage(snapshot.parent).free < payload.verified_size:
            raise ConfigError(  # noqa: TRY301
                "selected backup snapshot exceeds available space"
            )
        source_fd = os.open(payload.archive_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        target_fd = -1
        try:
            source_info = os.fstat(source_fd)
            if not stat.S_ISREG(source_info.st_mode):
                raise ConfigError("selected backup is not a regular file")
            source_identity = (
                source_info.st_dev,
                source_info.st_ino,
                source_info.st_size,
                source_info.st_mtime_ns,
            )
            if source_identity != payload.file_identity:
                raise ConfigError("selected backup file identity changed before restore")
            target_fd = os.open(
                snapshot,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            digest = hashlib.sha256()
            copied = 0
            with os.fdopen(source_fd, "rb") as source:
                source_fd = -1
                with os.fdopen(target_fd, "wb") as target:
                    target_fd = -1
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > payload.verified_size:
                            raise ConfigError("selected backup exceeded catalogue size")
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
            if copied != payload.verified_size:
                raise ConfigError("selected backup size changed before restore")
            if digest.hexdigest() != payload.verified_sha256:
                raise ConfigError("selected backup content changed before restore")
        finally:
            if source_fd != -1:
                with contextlib.suppress(OSError):
                    os.close(source_fd)
            if target_fd != -1:
                with contextlib.suppress(OSError):
                    os.close(target_fd)
    except ConfigError:
        with contextlib.suppress(OSError):
            snapshot.unlink()
        raise
    except OSError as exc:
        with contextlib.suppress(OSError):
            snapshot.unlink()
        raise ConfigError("selected backup snapshot is unavailable") from exc
    return payload


def _assert_verified_snapshot_unchanged(payload: SelectedBackupRestorePayload) -> None:
    """Fail closed if the private verified snapshot was tampered with."""
    try:
        info = os.stat(payload.verified_snapshot_path, follow_symlinks=False)
    except OSError as exc:
        raise ConfigError("selected backup snapshot disappeared before restore") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size != payload.verified_size:
        raise ConfigError("selected backup snapshot identity changed before restore")
    try:
        digest = _sha256_no_follow(payload.verified_snapshot_path)
    except OSError as exc:
        raise ConfigError("selected backup snapshot disappeared before restore") from exc
    if digest != payload.verified_sha256:
        raise ConfigError("selected backup snapshot content changed before restore")


def _open_verified_zip(path: Path) -> zipfile.ZipFile:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigError("selected backup archive is unavailable") from exc
    try:
        return zipfile.ZipFile(os.fdopen(descriptor, "rb"))
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        raise


def capture_selected_backup_restore(  # noqa: C901
    backup: Backup,
    *,
    data_dir: Path | None = None,
) -> SelectedBackupRestorePayload:
    """Read and validate selected-backup inputs at the restore boundary."""
    from odoo_instance_sdk.exceptions import BackupValidationUnavailableError
    from odoo_instance_sdk.internal.backup_validation import (
        raise_restore_preflight_errors,
        raise_zip_validation_error,
        validate_dump,
        validate_zip,
    )

    path = Path(backup.path)
    file_identity, verified_sha256 = _verified_file(path, backup)
    snapshot_path = path.parent / f".odcli-verified-{backup.id.hex}-{uuid.uuid4().hex}.backup"
    if backup.format == BackupFormat.DUMP:
        try:
            dump_validation = validate_dump(path, timeout=30.0, raise_if_unavailable=True)
        except BackupValidationUnavailableError as exc:
            raise ConfigError("selected native dump validation is unavailable") from exc
        if not dump_validation.valid:
            raise ConfigError("selected native dump is unavailable or invalid")
        try:
            return SelectedBackupRestorePayload(
                archive_path=path,
                dump_path=snapshot_path,
                verified_snapshot_path=snapshot_path,
                filestore_members=(),
                database_name=backup.database_name,
                file_identity=file_identity,
                verified_size=backup.size_bytes,
                verified_sha256=verified_sha256,
                format=BackupFormat.DUMP,
            )
        except OSError as exc:
            raise ConfigError("selected native dump is unavailable or invalid") from exc
    zip_validation = validate_zip(path)
    if not zip_validation.valid:
        raise_zip_validation_error(zip_validation)
        raise ConfigError("selected backup archive is unavailable or invalid")  # pragma: no cover
    if zip_validation.db_name != backup.database_name:
        raise ConfigError("selected backup database name does not match catalog metadata")
    raise_restore_preflight_errors(zip_validation.uncompressed_bytes, data_dir)
    try:
        with _open_verified_zip(path) as archive:
            archive.getinfo("dump.sql")
            files: list[str] = []
            prefix = "filestore/"
            for info in archive.infolist():
                name = info.filename
                if not name.startswith(prefix) or info.is_dir():
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ConfigError("selected backup contains a symlinked filestore path")
                parts = tuple(part for part in name.removeprefix(prefix).split("/") if part)
                if not parts or any(part in {".", ".."} for part in parts):
                    raise ConfigError("selected backup contains an unsafe filestore path")
                relative = parts[1:] if parts[0] == backup.database_name else parts
                if relative:
                    files.append("/".join(relative))
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ConfigError("selected backup archive is unavailable or invalid") from exc
    if not files:
        raise ConfigError("selected backup does not contain a usable filestore")
    return SelectedBackupRestorePayload(
        archive_path=path,
        dump_path=path.parent / f".odcli-restore-{backup.id.hex}.dump",
        verified_snapshot_path=snapshot_path,
        filestore_members=tuple(sorted(files)),
        database_name=backup.database_name,
        file_identity=file_identity,
        verified_size=backup.size_bytes,
        verified_sha256=verified_sha256,
        zip_entry_sizes=zip_validation.entry_sizes,
        zip_uncompressed_bytes=zip_validation.uncompressed_bytes,
        format=BackupFormat.ZIP,
    )


def build_selected_backup_restore_steps(
    instance: OdooInstance,
    *,
    target_database: str,
    dump_path: Path,
    backup_format: BackupFormat = BackupFormat.ZIP,
) -> tuple[PreparedStep, PreparedStep | PreparedAction, PreparedStep]:
    """Build the preparation PostgreSQL steps for a stopped COPY.

    Native dumps use the existing ``pg_restore`` validation/restore boundary.
    Odoo ZIPs contain plain SQL and therefore use the bounded ``psql``
    transport; ZIP members are streamed to the captured temporary dump path
    only during execution.
    """
    from odoo_instance_sdk.internal.pg.builder import build_psql_specification
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep

    cluster = instance._postgres_cluster
    if cluster is None:
        raise ConfigError("selected restore requires a bound PostgreSQL cluster")
    user = instance.config.db_user or getattr(cluster, "_user", None)
    if user is None:
        raise ConfigError("selected restore requires a PostgreSQL user")
    create = build_psql_specification(
        step_id="database.replace.restore.create",
        host=cluster.endpoint_host,
        port=cluster.endpoint_port,
        user=user,
        password=instance.config.db_password,
        database="postgres",
        args=("-c", f'CREATE DATABASE "{target_database.replace(chr(34), chr(34) + chr(34))}";'),
        _trusted_args=("-t", "-A"),
        timeout=30.0,
        _read_only=False,
        _mutating=True,
    ).prepared_step
    if backup_format == BackupFormat.DUMP:
        executable = shutil.which("pg_restore")
        if executable is None:
            raise ConfigError("selected native dump restore requires pg_restore")
        validate: PreparedStep | PreparedAction = PreparedStep(
            step_id="database.replace.restore.validate",
            argv=(executable, "--list", str(dump_path)),
            cwd=str(instance.config.default_cwd),
            timeout=30.0,
            read_only=True,
            text=True,
        )
        restore = PreparedStep(
            step_id="database.replace.restore.pg-restore",
            argv=(
                executable,
                "--exit-on-error",
                "--single-transaction",
                "--host",
                cluster.endpoint_host,
                "--port",
                str(cluster.endpoint_port),
                "--username",
                user,
                "--dbname",
                target_database,
                str(dump_path),
            ),
            cwd=str(instance.config.default_cwd),
            environment=(
                ()
                if instance.config.db_password is None
                else (("PGPASSWORD", instance.config.db_password),)
            ),
            secret_values=(
                () if instance.config.db_password is None else (instance.config.db_password,)
            ),
            timeout=30.0,
            read_only=False,
            mutating=True,
            text=False,
        )
    else:
        validate = PreparedAction(
            step_id="database.replace.restore.validate",
            action="validate-odoo-zip-dump",
            description="Use the validated Odoo ZIP SQL transport",
            read_only=True,
        )
        restore = build_psql_specification(
            step_id="database.replace.restore.psql",
            host=cluster.endpoint_host,
            port=cluster.endpoint_port,
            user=user,
            password=instance.config.db_password,
            database=target_database,
            args=("--single-transaction", "--set", "ON_ERROR_STOP=1", "--file", str(dump_path)),
            timeout=30.0,
            _read_only=False,
            _mutating=True,
        ).prepared_step
    return create, validate, restore


def materialize_selected_backup_dump(payload: SelectedBackupRestorePayload) -> None:
    """Stream the selected dump into its captured temporary execution path."""
    if payload.format == BackupFormat.DUMP:
        _materialize_verified_snapshot(payload)
        _assert_verified_snapshot_unchanged(payload)
        return
    if payload.dump_path.exists() or payload.dump_path.is_symlink():
        raise ConfigError("selected restore dump target is not absent")
    _materialize_verified_snapshot(payload)
    _assert_verified_snapshot_unchanged(payload)
    try:
        with (
            _open_verified_zip(payload.verified_snapshot_path) as archive,
            archive.open("dump.sql") as source,
            payload.dump_path.open("wb") as target,
        ):
            dump_size = dict(payload.zip_entry_sizes).get("dump.sql")
            if dump_size is None:
                raise ConfigError("selected backup dump metadata is missing")  # noqa: TRY301
            copied = 0
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > dump_size:
                    raise ConfigError("selected backup dump exceeds its validated limit")  # noqa: TRY301
                target.write(chunk)
            if copied != dump_size:
                raise ConfigError("selected backup dump size changed during restore")  # noqa: TRY301
    except ConfigError:
        with contextlib.suppress(OSError):
            payload.dump_path.unlink()
        raise
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        with contextlib.suppress(OSError):
            payload.dump_path.unlink()
        raise ConfigError("selected backup dump is unavailable or invalid") from exc


def materialize_selected_backup_filestore(  # noqa: C901
    destination: Path, payload: SelectedBackupRestorePayload
) -> None:
    """Stream validated selected-backup files under a contained root."""
    if destination.exists() or destination.is_symlink():
        raise ConfigError("selected restore filestore target is not absent")
    _materialize_verified_snapshot(payload)
    _assert_verified_snapshot_unchanged(payload)
    try:
        if shutil.disk_usage(destination.parent).free < payload.zip_uncompressed_bytes:
            raise ConfigError("selected backup filestore exceeds available space")
    except OSError as exc:
        raise ConfigError("selected backup filestore space is unavailable") from exc
    destination.mkdir(parents=True)
    root = destination.resolve()
    try:
        with _open_verified_zip(payload.verified_snapshot_path) as archive:
            copied_total = 0
            for relative in payload.filestore_members:
                source_name = f"filestore/{payload.database_name}/{relative}"
                if source_name not in archive.namelist():
                    source_name = f"filestore/{relative}"
                candidate = (destination / relative).resolve()
                if not candidate.is_relative_to(root):
                    raise ConfigError("selected backup filestore escapes its target")  # noqa: TRY301
                candidate.parent.mkdir(parents=True, exist_ok=True)
                info = archive.getinfo(source_name)
                expected_size = dict(payload.zip_entry_sizes).get(source_name)
                if expected_size is None or info.file_size != expected_size:
                    raise ConfigError("selected backup filestore metadata changed")  # noqa: TRY301
                copied = 0
                with archive.open(info) as source, candidate.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        copied_total += len(chunk)
                        if copied > expected_size or copied_total > payload.zip_uncompressed_bytes:
                            raise ConfigError(  # noqa: TRY301
                                "selected backup filestore exceeds its validated limit"
                            )
                        target.write(chunk)
                if copied != expected_size:
                    raise ConfigError("selected backup filestore size changed during restore")  # noqa: TRY301
    except BaseException:
        with contextlib.suppress(OSError):
            shutil.rmtree(destination)
        raise
    if not destination.is_dir() or destination.is_symlink():
        raise ConfigError("selected restore filestore verification failed")


@dataclass(frozen=True, slots=True)
class RestorePreflight:
    project: ProjectConfig
    project_id: str
    source: TestSourceResolution | None
    source_config: Path
    local_instance: OdooInstance
    runtime: ProjectRuntimeBinding
    postgres_cluster: PostgresCluster
    target_database: str
    restore_source: _RestoreSource = field(default_factory=_RemoteRestoreSource)
    catalogue_backup: Backup | None = None
    resolved_database: str | None = None


class _CoalescedRestore(Exception):
    def __init__(self, result: DatabasePreparationResult) -> None:
        self.result = result


def _normalize_branch(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or any(unicodedata.category(char) == "Cc" for char in value):
        raise ConfigError("source branch must be text without control characters")
    normalized = value.strip()
    if not normalized:
        raise ConfigError("source branch must not be empty")
    return normalized


def resolve_test_source(
    project: ProjectConfig, options: DatabaseRefreshOptions = DatabaseRefreshOptions()
) -> TestSourceResolution:
    config = project.test_instance
    if config is None:
        raise ConfigError("project has no [test_instance] configuration")
    try:
        base_url = normalize_base_url(config.base_url)
    except Exception as exc:
        raise ConfigError("invalid test_instance.base_url") from exc
    if config.database is not None and not config.database.strip():
        raise ConfigError("test_instance.database must not be empty")
    explicit = options.source_branch
    if explicit is not None:
        branch = _normalize_branch(explicit)
        origin = BackupBranchOrigin.EXPLICIT
    elif config.git_branch is not None:
        branch = _normalize_branch(config.git_branch)
        origin = BackupBranchOrigin.CONFIGURED
    else:
        branch = None
        origin = BackupBranchOrigin.UNKNOWN
    return TestSourceResolution(
        config=TestInstanceProjectConfig(
            base_url=base_url,
            database=config.database,
            git_branch=branch,
        ),
        branch=branch,
        origin=origin,
    )


def resolve_remote_database_name(
    configured: str | None,
    names_provider: DatabaseNameProvider,
) -> str:
    """Select exactly one remote database name for the current operation.

    When ``configured`` is explicitly set, it is returned without calling
    ``names_provider`` — this preserves work with instances where listing is
    disabled. When it is absent, names are obtained through ``names_provider``
    (typically ``DatabaseResource.names()``); exactly one name is selected.
    Zero names raise ``RemoteDatabaseNoneError``; multiple raise
    ``RemoteDatabaseAmbiguousError`` listing the names; an unavailable list
    raises ``RemoteDatabaseListUnavailableError``. All three fail before any
    download.
    """
    if configured is not None:
        return configured
    try:
        names = names_provider.names()
    except Exception as exc:
        raise RemoteDatabaseListUnavailableError(
            "database list is unavailable on the remote instance",
        ) from exc
    if len(names) == 0:
        raise RemoteDatabaseNoneError(
            "remote instance exposes no databases; set test_instance.database"
        )
    if len(names) > 1:
        raise RemoteDatabaseAmbiguousError(
            "remote instance exposes multiple databases; set test_instance.database",
            details={"available_databases": [cast("PlanJsonValue", n) for n in sorted(names)]},
        )
    return names[0]


def normalize_ref(value: str) -> str:
    if not isinstance(value, str):
        raise ConfigError("base ref must be text")
    ref = value.strip()
    if not ref:
        raise ConfigError("base ref must not be empty")
    return ref.removeprefix(_BRANCH_PREFIX)


def compare_provenance(
    expected_base_ref: str, recorded_branch: str | None
) -> BackupProvenanceComparison:
    expected = normalize_ref(expected_base_ref)
    if recorded_branch is None:
        status = BackupProvenanceStatus.UNKNOWN
    else:
        recorded = recorded_branch.strip()
        status = (
            BackupProvenanceStatus.MATCHED
            if normalize_ref(recorded) == expected
            else BackupProvenanceStatus.MISMATCHED
        )
    return BackupProvenanceComparison(
        status=status,
        expected_base_ref=expected,
        recorded_branch=recorded_branch,
    )


def classify_freshness(
    backup: Backup | NoBackup | None,
    refresh_after_hours: float | None,
    *,
    now: datetime | None = None,
) -> BackupFreshness:
    if backup is None or isinstance(backup, NoBackup):
        return BackupFreshness.MISSING
    if not Path(backup.path).is_file() or not os.access(backup.path, os.R_OK):
        return BackupFreshness.UNAVAILABLE
    if refresh_after_hours is None:
        return BackupFreshness.FRESH
    if (
        isinstance(refresh_after_hours, bool)
        or not math.isfinite(refresh_after_hours)
        or refresh_after_hours <= 0
    ):
        raise ConfigError("refresh_after_hours must be finite and greater than zero")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    downloaded = backup.downloaded_at
    if downloaded.tzinfo is None:
        downloaded = downloaded.replace(tzinfo=UTC)
    return (
        BackupFreshness.FRESH
        if downloaded + timedelta(hours=refresh_after_hours) > current
        else BackupFreshness.STALE
    )


def _truncate_utf8(value: str, maximum: int) -> str:
    raw = value.encode("utf-8")[:maximum]
    while True:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]


def generate_target_database(
    remote_database: str,
    *,
    now: datetime | None = None,
    suffix: str | None = None,
) -> str:
    slug = _TARGET_SLUG_RE.sub("_", remote_database).strip("._-") or "database"
    suffix_value = suffix or uuid.uuid4().hex[:12]
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d%H%M%S")
    marker = f"_{stamp}_{suffix_value}"
    prefix = _truncate_utf8(slug, max(1, _MAX_TARGET_BYTES - len(marker.encode("utf-8"))))
    candidate = f"{prefix}{marker}"
    candidate = _truncate_utf8(candidate, _MAX_TARGET_BYTES)
    validate_db_name(candidate)
    return candidate


def reserve_target_database(
    remote_database: str,
    exists: Callable[[str], bool],
    *,
    generator: Callable[[str], str] = generate_target_database,
    attempts: int = _TARGET_ATTEMPTS,
) -> str:
    if attempts < 1:
        raise ConfigError("target reservation attempts must be positive")
    for _ in range(attempts):
        candidate = generator(remote_database)
        validate_db_name(candidate)
        if not exists(candidate):
            return candidate
    raise ConfigError("could not reserve a unique refresh database name")


def retained_artifact_context(
    operation: str, *, backup_id: uuid.UUID | str | None = None, target_database: str | None = None
) -> str:
    details = [operation]
    if backup_id is not None:
        details.append(f"retained backup {backup_id}")
    if target_database is not None:
        details.append(f"retained database {target_database}")
    return "; ".join(details)


def relevant_manifest_conflicts(before: ProjectConfig, after: ProjectConfig) -> tuple[str, ...]:
    return tuple(
        field for field in _PREPARATION_FIELDS if getattr(before, field) != getattr(after, field)
    )


def canonical_project_identity(project_path: str | Path) -> tuple[Path, Path, str]:
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    if context is not None:
        top_result = context.process("database.prepare.git.toplevel")
        common_result = context.process("database.prepare.git.common-dir")
        top_output = getattr(top_result, "stdout", "")
        common_output = getattr(common_result, "stdout", "")
        root = Path(str(top_output).strip()).resolve()
        common = Path(str(common_output).strip())
        if not common.is_absolute():
            common = root / common
        return root, common.resolve(), repo_key(root, common)
    root = rev_parse_toplevel(Path(project_path))
    common = rev_parse_git_common_dir(root)
    return root, common, repo_key(root, common)


def _planned_project_identity(project_path: str | Path) -> tuple[Path, Path, str]:
    """Resolve the Git identity from local metadata without launching Git."""
    root = Path(project_path).resolve()
    common = git_common_dir(root)
    return root, common.resolve(), repo_key(root, common)


def _load_project(project: ProjectConfig | str | Path) -> tuple[ProjectConfig, Path]:
    if isinstance(project, ProjectConfig):
        return project, project.repository_root.resolve()
    root = Path(project).resolve()
    return ProjectConfig.load(root), root


def _reload_project(project: ProjectConfig | str | Path, root: Path) -> ProjectConfig:
    if isinstance(project, ProjectConfig) and not (root / ".odcli" / "project.toml").is_file():
        return project
    return ProjectConfig.load(root)


def _remote_password(environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    value = source.get("ODCLI_TEST_MASTER_PASSWORD")
    if value is None or not value.strip():
        raise MasterPasswordRequiredError("ODCLI_TEST_MASTER_PASSWORD is required")
    return value


def _consume_action_if_planned(step_id: str) -> None:
    """Consume an optional domain action when preparation is command-bound."""
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    if context is None:
        return
    context.action(step_id)


def _skip_preparation_branch(step_ids: Sequence[str]) -> None:
    """Account explicitly for a branch that was proven unnecessary."""
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    if context is None:
        return
    for step_id in step_ids:
        if context.planned(step_id) and not context.consumed(step_id):
            context.skip(step_id)


def _resolve_source_config(project: ProjectConfig, root: Path) -> Path:
    configured = project.source_config
    path = (
        (root / configured).resolve()
        if configured is not None and not configured.is_absolute()
        else configured
    )
    if path is None:
        path = root / "odoo.conf"
    path = path.resolve()
    if not path.is_file():
        raise InstanceConfigurationError("local source config is missing")
    if project.postgres is not None and project.postgres.mode == "compose":
        generated = project_generated_config_path(root)
        if generated.is_file():
            return generated
    return path


def _resolve_executable(value: str | Path | None, root: Path, label: str) -> str:
    if value is None:
        raise InstanceConfigurationError(f"{label} is not configured")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise InstanceConfigurationError(f"{label} is missing or not a file")
    return str(candidate)


def resolve_runtime_binding(project: ProjectConfig, root: Path) -> ProjectRuntimeBinding:
    if project.runtime_cwd is None:
        runtime_cwd = root
    else:
        runtime_cwd = Path(project.runtime_cwd)
        if not runtime_cwd.is_absolute():
            runtime_cwd = root / runtime_cwd
        runtime_cwd = runtime_cwd.resolve()
    if not runtime_cwd.is_dir():
        raise InstanceConfigurationError("runtime cwd is missing or not a directory")
    if is_uv_python_selector(project.python):
        selector = cast("str", project.python)
        uv_executable = resolve_uv_executable(selector=selector)
        return ProjectRuntimeBinding(
            python_executable=None,
            odoo_bin=_resolve_executable(project.odoo_bin, root, "odoo_bin"),
            runtime_cwd=runtime_cwd,
            python_selector=selector,
            uv_executable=str(uv_executable),
        )
    python_executable = resolve_project_runtime(root, project.python, field="python")
    if not os.access(python_executable, os.X_OK):
        raise InstanceConfigurationError("python executable is missing or not executable")
    return ProjectRuntimeBinding(
        python_executable=str(python_executable),
        odoo_bin=_resolve_executable(project.odoo_bin, root, "odoo_bin"),
        runtime_cwd=runtime_cwd,
    )


@contextlib.contextmanager
def preparation_lock(project_id: str) -> Iterator[None]:
    with exclusive_lock(database_preparation_lock_path(project_id)):
        yield


@contextlib.contextmanager
def _wait_for_preparation_lock(project_id: str, *, timeout: float = 300.0) -> Iterator[None]:
    """Hold the project lock while allowing concurrent callers to queue."""
    deadline = time.monotonic() + timeout
    with exclusive_lock_until(database_preparation_lock_path(project_id), deadline):
        yield


def _target_config_path(source_config: Path, target_path: Path | None = None) -> Path:
    if target_path is None:
        fd, name = tempfile.mkstemp(
            dir=str(source_config.parent), prefix=".odcli-refresh-", suffix=".conf"
        )
        os.close(fd)
        path = Path(name)
    else:
        path = target_path
        if path.parent != source_config.parent or path.exists():
            raise ConfigError("target preparation config path is not available")
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ConfigError("target preparation config path is not available") from exc
        else:
            os.close(fd)
    os.chmod(path, 0o600)
    return path


def _write_target_config(source_config: Path, target_config: Path, target_database: str) -> None:
    import configparser

    config = configparser.RawConfigParser(interpolation=None)
    config.read(str(source_config))
    if not config.has_section("options"):
        config.add_section("options")
    options = config["options"]
    options["db_name"] = target_database
    options["dbfilter"] = target_database
    with target_config.open("w", encoding="utf-8") as stream:
        config.write(stream)
    os.chmod(target_config, 0o600)
