from __future__ import annotations

import contextlib
import hashlib  # noqa: F401 -- compatibility patch point for restore tests.
import math
import os
import re
import shutil  # noqa: F401 -- compatibility patch point for archive transport.
import tempfile
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar, cast, runtime_checkable

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
from odoo_instance_sdk.internal.dbprep import source_restore as _source_restore
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
from odoo_instance_sdk.internal.project_env import (
    effective_project_environment,
    load_project_environment,
)
from odoo_instance_sdk.internal.project_runtime import (
    is_uv_python_selector,
    resolve_project_runtime,
    resolve_uv_executable,
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
from odoo_instance_sdk.project import (
    ProjectConfig,
    RemoteSourceConfig,
    TestInstanceProjectConfig,
    normalize_remote_name,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.internal.proc import PreparedAction
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster
T = TypeVar("T")
_CatalogueRestoreSource = _source_restore._CatalogueRestoreSource
_LocalArchiveRestoreSource = _source_restore._LocalArchiveRestoreSource
_RemoteRestoreSource = _source_restore._RemoteRestoreSource
_RestoreSource = _source_restore._RestoreSource
_RestoreSourceInput = _source_restore._RestoreSourceInput
SelectedBackupRestorePayload = _source_restore.SelectedBackupRestorePayload
capture_local_archive_restore = _source_restore.capture_local_archive_restore
capture_selected_backup_restore = _source_restore.capture_selected_backup_restore
cleanup_selected_backup_restore = _source_restore.cleanup_selected_backup_restore
_verified_file = _source_restore._verified_file
_assert_verified_snapshot_unchanged = _source_restore._assert_verified_snapshot_unchanged
ProjectRuntimeBinding = _source_restore.ProjectRuntimeBinding
DatabasePreparationFailureContext = _source_restore.DatabasePreparationFailureContext


_open_verified_zip = _source_restore._open_verified_zip


def _materialize_verified_snapshot(
    payload: SelectedBackupRestorePayload,
) -> SelectedBackupRestorePayload:
    original = _source_restore._open_verified_zip
    _source_restore._open_verified_zip = _open_verified_zip
    try:
        return _source_restore._materialize_verified_snapshot(payload)
    finally:
        _source_restore._open_verified_zip = original


def materialize_selected_backup_dump(payload: SelectedBackupRestorePayload) -> None:
    original = _source_restore._open_verified_zip
    _source_restore._open_verified_zip = _open_verified_zip
    try:
        _source_restore.materialize_selected_backup_dump(payload)
    finally:
        _source_restore._open_verified_zip = original


def materialize_selected_backup_filestore(
    destination: Path, payload: SelectedBackupRestorePayload
) -> None:
    original = _source_restore._open_verified_zip
    _source_restore._open_verified_zip = _open_verified_zip
    try:
        _source_restore.materialize_selected_backup_filestore(destination, payload)
    finally:
        _source_restore._open_verified_zip = original


_REMOTE_MASTER_PASSWORD = "ODCLI_TEST_MASTER_PASSWORD"

_BRANCH_PREFIX = "refs/heads/"
_MAX_TARGET_BYTES = 63
_TARGET_ATTEMPTS = 100
_TARGET_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_PREPARATION_FIELDS = (
    "test_instance",
    "remote_instances",
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
    config: TestInstanceProjectConfig | RemoteSourceConfig
    branch: str | None
    origin: BackupBranchOrigin
    source_name: str | None = None


class CopyBackupContext(Protocol):
    """The execution context needed while acquiring a COPY backup."""

    def action(self, step_id: str) -> PreparedAction: ...

    def complete_action(self, step_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class CopyBackupAcquisition:
    """A COPY input and the ownership policy for its cleanup."""

    backup: Backup
    ownership: Literal["owned", "borrowed"]


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
    selected_restore: SelectedBackupRestorePayload | None = None


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
    config: TestInstanceProjectConfig | RemoteSourceConfig
    configured_branch: str | None
    source_name: str | None
    if options.remote_name is not None:
        name = normalize_remote_name(options.remote_name)
        selected = next(
            (
                source
                for source in project.remote_instances
                if normalize_remote_name(source.name) == name
            ),
            None,
        )
        if selected is None:
            available = ", ".join(source.name for source in project.remote_instances) or "none"
            raise ConfigError(f"unknown remote source {name!r}; available names: {available}")
        config = selected
        base_url = config.base_url
        configured_branch = config.git_branch
        source_name = name
    else:
        legacy = project.test_instance
        if legacy is None:
            raise ConfigError("project has no [test_instance] configuration")
        config = legacy
        base_url = config.base_url
        configured_branch = config.git_branch
        source_name = None
    try:
        normalized_url = normalize_base_url(base_url)
    except Exception as exc:
        label = f"remote source {source_name!r}" if source_name else "test_instance"
        raise ConfigError(f"invalid {label}.base_url") from exc
    if config.database is not None and not config.database.strip():
        label = f"remote source {source_name!r}" if source_name else "test_instance"
        raise ConfigError(f"{label}.database must not be empty")
    explicit = options.source_branch
    if explicit is not None:
        branch = _normalize_branch(explicit)
        origin = BackupBranchOrigin.EXPLICIT
    elif configured_branch is not None:
        branch = _normalize_branch(configured_branch)
        origin = BackupBranchOrigin.CONFIGURED
    else:
        branch = None
        origin = BackupBranchOrigin.UNKNOWN
    if source_name is not None:
        assert isinstance(config, RemoteSourceConfig)
        assert branch is not None
        assert config.database is not None
    if source_name is not None:
        named_config = cast("RemoteSourceConfig", config)
        resolved_config: RemoteSourceConfig | TestInstanceProjectConfig = RemoteSourceConfig(
            name=source_name,
            base_url=normalized_url,
            database=named_config.database,
            git_branch=cast("str", branch),
        )
    else:
        resolved_config = TestInstanceProjectConfig(
            base_url=normalized_url,
            database=config.database,
            git_branch=branch,
        )
    return TestSourceResolution(
        config=resolved_config,
        branch=branch,
        origin=origin,
        source_name=source_name,
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


def remote_password_key(remote_name: str | None = None) -> str:
    if remote_name is None:
        return _REMOTE_MASTER_PASSWORD
    return f"ODCLI_REMOTE_{normalize_remote_name(remote_name).upper()}_MASTER_PASSWORD"


def _remote_password(
    environ: Mapping[str, str] | None = None, *, remote_name: str | None = None
) -> str:
    source = os.environ if environ is None else environ
    key = remote_password_key(remote_name)
    value = source.get(key)
    if value is None or not value.strip():
        raise MasterPasswordRequiredError(f"{key} is required")
    return value


def acquire_copy_backup(
    client: OdooClient,
    project: ProjectConfig,
    *,
    local_instance: OdooInstance,
    source_db: str,
    repo_root: Path,
    remote_name: str | None,
    selected_backup: Backup | None,
    context: CopyBackupContext,
) -> CopyBackupAcquisition:
    """Acquire one COPY input at the preparation/source boundary."""
    if selected_backup is not None:
        client.get_catalog().verify_identity(selected_backup, verify_content=True)
        return CopyBackupAcquisition(backup=selected_backup, ownership="borrowed")
    if remote_name is not None:
        source = resolve_test_source(project, DatabaseRefreshOptions(remote_name=remote_name))
        password = _remote_password(
            effective_project_environment(load_project_environment(repo_root)),
            remote_name=remote_name,
        )
        remote = client.instance(source.config.base_url, master_password=password)
        project_id = f"project_{repo_key(repo_root, git_common_dir(repo_root))}"
        context.action("database.backup.wait")
        context.complete_action("database.backup.wait")
        context.action("database.backup.transfer")
        backup = remote.databases.backup(
            source_db,
            format=BackupFormat.ZIP,
            filestore=True,
            source_git_branch=source.branch,
            source_name=source.source_name,
            project_id=project_id,
        )
        context.complete_action("database.backup.transfer")
        return CopyBackupAcquisition(backup=backup, ownership="borrowed")
    return CopyBackupAcquisition(
        backup=local_instance.databases.backup(source_db, format=BackupFormat.ZIP, filestore=True),
        ownership="owned",
    )


def _consume_action_if_planned(step_id: str) -> None:
    """Consume an optional domain action when preparation is command-bound."""
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    if context is None:
        return
    if context.planned(step_id) and not context.consumed(step_id):
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


from odoo_instance_sdk.internal.dbprep.archive_transport import (  # noqa: E402
    build_selected_backup_restore_steps as _build_selected_backup_restore_steps,
)

build_selected_backup_restore_steps = _build_selected_backup_restore_steps
