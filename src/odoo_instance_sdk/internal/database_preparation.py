from __future__ import annotations

import contextlib
import math
import os
import re
import tempfile
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    DatabaseManagerUnavailableError,
    EnvironmentConflictError,
    InstanceConfigurationError,
    MasterPasswordRequiredError,
)
from odoo_instance_sdk.internal.db_name import validate_db_name
from odoo_instance_sdk.internal.git_worktree import (
    rev_parse_git_common_dir,
    rev_parse_toplevel,
)
from odoo_instance_sdk.internal.locks import (
    database_preparation_artifact_lock_path,
    database_preparation_lock_path,
    exclusive_lock,
    exclusive_lock_until,
)
from odoo_instance_sdk.internal.odoo_config import infer_base_url, parse_odoo_config
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.test_instance_trust import require_test_instance_origin_approval
from odoo_instance_sdk.internal.urls import assert_local, normalize_base_url
from odoo_instance_sdk.models import (
    Backup,
    BackupBranchOrigin,
    BackupFreshness,
    BackupProvenanceComparison,
    BackupProvenanceStatus,
    DatabasePreparationAction,
    DatabasePreparationResult,
    DatabaseRefreshOptions,
    NoBackup,
    StartConfig,
)
from odoo_instance_sdk.project import ProjectConfig, TestInstanceProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        RunContext,
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


@dataclass(frozen=True, slots=True)
class TestSourceResolution:
    config: TestInstanceProjectConfig
    branch: str | None
    origin: BackupBranchOrigin


@dataclass(frozen=True, slots=True)
class ProjectRuntimeBinding:
    python_executable: str
    odoo_bin: str
    runtime_cwd: Path

    @property
    def command_prefix(self) -> tuple[str, str]:
        return (self.python_executable, self.odoo_bin)


class DatabasePreparationFailureContext(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """Secret-free identifiers retained after a preparation failure."""

    retained_backup_id: uuid.UUID | None = None
    retained_database: str | None = None


@dataclass(frozen=True, slots=True)
class RestorePreflight:
    project: ProjectConfig
    project_id: str
    source: TestSourceResolution
    source_config: Path
    local_instance: OdooInstance
    runtime: ProjectRuntimeBinding
    postgres_cluster: PostgresCluster
    target_database: str


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
    if not config.database.strip():
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
    marker = f"_refresh_{stamp}_{suffix_value}"
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
    root = rev_parse_toplevel(Path(project_path))
    common = rev_parse_git_common_dir(root)
    return root, common, repo_key(root, common)


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
    return path


def _resolve_executable(value: str | Path | None, root: Path, label: str) -> str:
    if value is None:
        raise InstanceConfigurationError(f"{label} is not configured")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise InstanceConfigurationError(f"{label} executable is missing or not executable")
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
    return ProjectRuntimeBinding(
        python_executable=_resolve_executable(project.python, root, "python"),
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


def _target_config_path(source_config: Path) -> Path:
    fd, name = tempfile.mkstemp(
        dir=str(source_config.parent), prefix=".odcli-refresh-", suffix=".conf"
    )
    os.close(fd)
    path = Path(name)
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


@contextlib.contextmanager
def build_target_instance(
    client: OdooClient,
    *,
    source_config: Path,
    target_database: str,
    runtime: ProjectRuntimeBinding,
    postgres_cluster: PostgresCluster,
    project_id: str,
) -> Iterator[OdooInstance]:
    """Build a target-only instance and remove its ephemeral config on exit."""
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.resources.instance import OdooInstance

    target_config = _target_config_path(source_config)
    try:
        _write_target_config(source_config, target_config, target_database)
        parsed = parse_odoo_config(target_config)
        local_url = normalize_base_url(infer_base_url(parsed))
        assert_local(local_url)
        from odoo_instance_sdk.models import StartConfig

        start_config = StartConfig.from_odoo_config(target_config)
        db_port = start_config.db_port
        if start_config.db_host and db_port is None:
            db_port = 5432
        instance = OdooInstance(
            config=InstanceConfig(
                base_url=local_url,
                master_password=parsed.get("admin_passwd"),
                configured_database_names=(target_database,),
                start_config=start_config,
                command_prefix=runtime.command_prefix,
                default_cwd=runtime.runtime_cwd,
                db_host=start_config.db_host,
                db_port=db_port,
                db_user=start_config.db_user,
                db_password=start_config.db_password,
            ),
            _client=client,
            _artifact_lock_path=database_preparation_artifact_lock_path(
                project_id, target_database
            ),
            _postgres_cluster=postgres_cluster,
        )
        yield instance
    finally:
        with contextlib.suppress(OSError):
            target_config.unlink(missing_ok=True)


def _annotate_retained_failure(
    error: BaseException,
    *,
    backup: Backup | None,
    target_database: str | None,
) -> None:
    """Attach only non-secret retained-artifact identifiers to a failure."""
    context = DatabasePreparationFailureContext(
        retained_backup_id=backup.id if backup is not None else None,
        retained_database=target_database,
    )
    setattr(error, "failure_context", context)
    note = retained_artifact_context(
        "database preparation retained artifacts",
        backup_id=backup.id if backup is not None else None,
        target_database=target_database,
    )
    error.add_note(note)


def _manifest_after_preparation(root: Path, baseline: ProjectConfig) -> ProjectConfig:
    current = ProjectConfig.load(root)
    conflicts = relevant_manifest_conflicts(baseline, current)
    if conflicts:
        raise EnvironmentConflictError(
            "preparation_manifest_conflict",
            "project preparation settings changed during refresh",
            details={"fields": cast("JsonValue", list(conflicts))},
        )
    return current


def _latest_default_backup(
    client: OdooClient, project: ProjectConfig, root: Path
) -> Backup | NoBackup | None:
    """Read the mapped default without initializing a catalog or making HTTP calls."""
    default = project.default_source_database
    if default is None:
        return None
    source_config = _resolve_source_config(project, root)
    parsed = parse_odoo_config(source_config)
    raw_port = parsed.get("db_port")
    try:
        port = int(raw_port) if raw_port else 5432
    except ValueError:
        port = 5432
    host = parsed.get("db_host")
    return client.get_catalog().latest_restore(host, port, default) or None


@contextlib.contextmanager
def _restore_preflight(
    client: OdooClient,
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions,
    wait_for_lock: bool = True,
    coalesce: bool = False,
) -> Iterator[RestorePreflight]:
    """Own the complete restore preflight and preparation-lock lifetime."""
    if not options.restore:
        raise ConfigError("restore preparation requires restore=True")

    initial, root = _load_project(project)
    initial_source = resolve_test_source(initial, options)
    require_test_instance_origin_approval(initial_source.config.base_url)
    _, _, project_id = canonical_project_identity(root)
    lock_context = (
        _wait_for_preparation_lock(project_id) if wait_for_lock else preparation_lock(project_id)
    )
    _consume_action_if_planned("database.prepare.lock")
    with lock_context:
        current = _reload_project(project, root)
        source = resolve_test_source(current, options)
        require_test_instance_origin_approval(source.config.base_url)
        if coalesce and current.refresh_after_hours is not None:
            mapped = _latest_default_backup(client, current, root)
            if (
                isinstance(mapped, Backup)
                and classify_freshness(mapped, current.refresh_after_hours) is BackupFreshness.FRESH
                and mapped.source_git_branch == source.branch
            ):
                raise _CoalescedRestore(
                    DatabasePreparationResult(
                        mode=DatabasePreparationAction.RESTORE,
                        backup=mapped,
                        source_git_branch=mapped.source_git_branch,
                        branch_origin=source.origin,
                        restored_database=current.default_source_database,
                        previous_default=current.default_source_database,
                        effective_default=current.default_source_database,
                        warnings=("reused fresh project database",),
                    )
                )
        source_config = _resolve_source_config(current, root)
        local_cfg = parse_odoo_config(source_config)
        local_password = local_cfg.get("admin_passwd")
        if local_password is None or not local_password.strip():
            raise MasterPasswordRequiredError("local source config has no admin_passwd")
        try:
            local_url = normalize_base_url(infer_base_url(local_cfg))
            assert_local(local_url)
        except Exception as exc:
            raise InstanceConfigurationError(
                "local source config must bind a local Odoo endpoint"
            ) from exc
        runtime = resolve_runtime_binding(current, root)
        from odoo_instance_sdk.internal.proc import active_context
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        context = active_context()
        if context is None:
            cluster = PostgresCluster.from_project(root)
        else:
            cluster = PostgresCluster._from_config(
                current,
                repository_root=root,
                compose_runner=None,
                project_id=project_id,
            )
        cluster.ensure_running(timeout=60.0)
        local = client.instance.from_config(
            source_config,
            base_url=local_url,
            master_password=local_password,
        )
        if not local.databases.names():
            raise DatabaseManagerUnavailableError("local database manager returned no databases")
        target = reserve_target_database(source.config.database, local.databases.exists)
        yield RestorePreflight(
            project=current,
            project_id=project_id,
            source=source,
            source_config=source_config,
            local_instance=local,
            runtime=runtime,
            postgres_cluster=cluster,
            target_database=target,
        )


def prepare_restore(
    client: OdooClient,
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions = DatabaseRefreshOptions(restore=True),
    coalesce: bool = False,
) -> DatabasePreparationResult:
    """Run the full restore preparation while retaining the project lock."""
    if not options.restore:
        raise ConfigError("restore preparation requires restore=True")
    remote_password = _remote_password()
    try:
        with _restore_preflight(client, project, options=options, coalesce=coalesce) as preflight:
            current = preflight.project
            root = current.repository_root
            source = preflight.source
            remote = client.instance(source.config.base_url, master_password=remote_password)
            backup: Backup | None = None
            try:
                _consume_action_if_planned("database.prepare.remote-backup")
                backup = remote.databases.backup(
                    source.config.database,
                    source_git_branch=source.branch,
                )
                _consume_action_if_planned("database.prepare.local-restore")
                preflight.local_instance.databases.restore(
                    backup,
                    preflight.target_database,
                    copy=True,
                    neutralize_database=True,
                )
                reset_completed = False
                if options.reset_admin_password:
                    _consume_action_if_planned("database.prepare.odoo-reset")
                    with build_target_instance(
                        client,
                        source_config=preflight.source_config,
                        target_database=preflight.target_database,
                        runtime=preflight.runtime,
                        postgres_cluster=preflight.postgres_cluster,
                        project_id=preflight.project_id,
                    ) as target_instance:
                        target_instance.databases.reset_admin_password()
                    reset_completed = True

                final_config = _manifest_after_preparation(root, current)
                switched = msgspec.structs.replace(
                    final_config, default_source_database=preflight.target_database
                )
                from odoo_instance_sdk.internal.project_manifest import write_manifest

                _consume_action_if_planned("database.prepare.default-switch")
                write_manifest(root, switched)
                return DatabasePreparationResult(
                    mode=DatabasePreparationAction.RESTORE,
                    backup=backup,
                    source_git_branch=source.branch,
                    branch_origin=source.origin,
                    restored_database=preflight.target_database,
                    admin_password_reset=reset_completed,
                    default_switched=True,
                    previous_default=current.default_source_database,
                    effective_default=preflight.target_database,
                )
            except BaseException as exc:
                _consume_action_if_planned("database.prepare.rollback")
                _annotate_retained_failure(
                    error=exc,
                    backup=backup,
                    target_database=preflight.target_database,
                )
                raise
    except _CoalescedRestore as coalesced:
        _skip_preparation_branch(
            (
                "database.prepare.remote-backup",
                "database.prepare.local-restore",
                "database.prepare.odoo-reset",
                "database.prepare.default-switch",
                "database.prepare.rollback",
                "postgres.ensure.image.pull",
                "postgres.ensure.image.inspect",
                "postgres.ensure.status.ps",
                "postgres.ensure.status.health",
                "postgres.ensure.config",
                "postgres.ensure.up",
                "postgres.ensure.final.ps",
                "postgres.ensure.final.health",
                "database.restore.exists-before",
                "database.restore.exists-after",
                "instance.shell_script",
            )
        )
        return coalesced.result


def prepare_download(
    client: OdooClient,
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
    wait_for_lock: bool = False,
) -> DatabasePreparationResult:
    if options.restore:
        raise ConfigError("restore preparation is not available in download-only mode")
    password = _remote_password()
    initial, root = _load_project(project)
    source = resolve_test_source(initial, options)
    require_test_instance_origin_approval(source.config.base_url)
    _, _, project_id = canonical_project_identity(root)
    lock_context = (
        _wait_for_preparation_lock(project_id) if wait_for_lock else preparation_lock(project_id)
    )
    _consume_action_if_planned("database.prepare.lock")
    with lock_context:
        current = _reload_project(project, root)
        source = resolve_test_source(current, options)
        require_test_instance_origin_approval(source.config.base_url)
        remote = client.instance(source.config.base_url, master_password=password)
        _consume_action_if_planned("database.prepare.remote-backup")
        backup = remote.databases.backup(
            source.config.database,
            source_git_branch=source.branch,
        )
        return DatabasePreparationResult(
            mode=DatabasePreparationAction.DOWNLOAD,
            backup=backup,
            source_git_branch=source.branch,
            branch_origin=source.origin,
            previous_default=current.default_source_database,
            effective_default=current.default_source_database,
        )


def preflight_restore(
    client: OdooClient,
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions = DatabaseRefreshOptions(restore=True),
) -> RestorePreflight:
    with _restore_preflight(client, project, options=options, wait_for_lock=False) as preflight:
        return preflight


def locked_restore_preflight(
    client: OdooClient,
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions = DatabaseRefreshOptions(restore=True),
) -> contextlib.AbstractContextManager[RestorePreflight]:
    """Yield the shared restore preflight state while retaining its lock."""
    return _restore_preflight(client, project, options=options, wait_for_lock=False)


def _preparation_process_steps(
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions,
) -> tuple[PreparedStep, ...]:
    """Build the child-process part of a preparation command before effects.

    The preparation implementation deliberately keeps catalog, filesystem,
    HTTP, and lock work in its domain callback.  Its Git, PostgreSQL, compose,
    and optional Odoo-shell launches, however, must be visible in the same
    private snapshot so the active ledger can reject substitutions.
    """
    from odoo_instance_sdk.internal.proc import PreparedStep

    initial, root = _load_project(project)
    steps: list[PreparedStep] = [
        PreparedStep(
            step_id="database.prepare.git.toplevel",
            argv=("git", "-C", str(root), "rev-parse", "--show-toplevel"),
            timeout=30.0,
            read_only=True,
            text=True,
        ),
        PreparedStep(
            step_id="database.prepare.git.common-dir",
            argv=("git", "-C", str(root), "rev-parse", "--git-common-dir"),
            timeout=30.0,
            read_only=True,
            text=True,
        ),
    ]
    if not options.restore:
        return tuple(steps)

    from odoo_instance_sdk.resources.database import (
        _RESET_ADMIN_PASSWORD_SCRIPT,
        _database_psql_step,
    )
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    # The same lifecycle builder is used by ``ensure_running``.  A placeholder
    # identity is deliberate: it is an explicitly late-bound repository ID,
    # not a second planner or a probe performed while constructing the command.
    cluster = PostgresCluster._from_config(
        initial,
        repository_root=root,
        compose_runner=None,
        project_id="<runtime>",
    )
    steps.extend(
        step for step in cluster._ensure_running_steps(60.0) if isinstance(step, PreparedStep)
    )

    source_config = _resolve_source_config(initial, root)
    parsed = parse_odoo_config(source_config)
    db_user = parsed.get("db_user")
    if db_user:
        db_host = parsed.get("db_host")
        raw_port = parsed.get("db_port")
        try:
            db_port = int(raw_port) if raw_port else 5432
        except ValueError:
            db_port = 5432
        password = parsed.get("db_password")
        steps.extend(
            (
                _database_psql_step(
                    step_id="database.restore.exists-before",
                    host=db_host,
                    port=db_port,
                    user=db_user,
                    password=password,
                    database_name="<runtime>",
                ),
                _database_psql_step(
                    step_id="database.restore.exists-after",
                    host=db_host,
                    port=db_port,
                    user=db_user,
                    password=password,
                    database_name="<runtime>",
                ),
            )
        )

    if options.reset_admin_password:
        from odoo_instance_sdk.resources.instance import _build_shell_script_step

        runtime = resolve_runtime_binding(initial, root)
        start_config = StartConfig.from_odoo_config(source_config)
        start_config.config_path = "<runtime>"
        start_config.dbfilter = "<runtime>"
        start_config.db_name = "<runtime>"
        shell_step, _, _, _ = _build_shell_script_step(
            start_config,
            executable_prefix=runtime.command_prefix,
            default_cwd=runtime.runtime_cwd,
            source=_RESET_ADMIN_PASSWORD_SCRIPT,
            commit=True,
        )
        steps.append(shell_step)
    return tuple(steps)


def _preparation_action_steps(
    *, operation: str, options: DatabaseRefreshOptions
) -> tuple[PreparedAction, ...]:
    """Return honest in-process boundaries for the preparation coordinator."""
    from odoo_instance_sdk.internal.proc import PreparedAction

    action_steps = [
        PreparedAction(
            step_id="database.prepare.lock",
            action="acquire-preparation-lock",
            description="Serialize project database preparation",
            mutating=True,
        ),
        PreparedAction(
            step_id="database.prepare.remote-backup",
            action="download-remote-backup",
            description="Request the selected remote database backup",
            mutating=True,
        ),
    ]
    if options.restore:
        action_steps.append(
            PreparedAction(
                step_id="database.prepare.local-restore",
                action="restore-local-database",
                description="Restore the captured backup into the reserved database",
                mutating=True,
            )
        )
        if options.reset_admin_password:
            action_steps.append(
                PreparedAction(
                    step_id="database.prepare.odoo-reset",
                    action="reset-odoo-admin-password",
                    description="Reset the administrator password in the target database",
                    mutating=True,
                )
            )
        action_steps.extend(
            (
                PreparedAction(
                    step_id="database.prepare.default-switch",
                    action="switch-default-database",
                    description="Publish the prepared database as the project default",
                    mutating=True,
                ),
                PreparedAction(
                    step_id="database.prepare.rollback",
                    action="compensate-preparation-failure",
                    description="Retain artifacts and record preparation compensation",
                    read_only=True,
                ),
            )
        )
    return tuple(action_steps)


@dataclass(slots=True)
class DatabasePreparationCoordinator:
    client: OdooClient

    def prepare(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        coalesce: bool = False,
    ) -> DatabasePreparationResult:
        return self.prepare_command(project, options=options, coalesce=coalesce).run()

    def prepare_command(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        coalesce: bool = False,
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabasePreparationResult]:
        steps: tuple[PreparedStep | PreparedAction, ...] = (
            *_preparation_action_steps(operation="prepare", options=options),
            *_preparation_process_steps(project, options=options),
        )
        return self._action_command(
            "database.prepare",
            "Prepare a project database",
            lambda: self._prepare_impl(project, options=options, coalesce=coalesce),
            executor=executor,
            steps=steps,
            optional_steps=tuple(
                step.step_id
                for step in steps
                if step.step_id
                in {
                    "database.restore.exists-before",
                    "database.restore.exists-after",
                    "database.prepare.rollback",
                }
            ),
        )

    def _prepare_impl(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions,
        coalesce: bool,
    ) -> DatabasePreparationResult:
        if options.restore:
            return prepare_restore(self.client, project, options=options, coalesce=coalesce)
        return prepare_download(self.client, project, options=options, wait_for_lock=True)

    def refresh_database(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
    ) -> DatabasePreparationResult:
        return self.refresh_database_command(project, options=options).run()

    def refresh_database_command(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabasePreparationResult]:
        steps: tuple[PreparedStep | PreparedAction, ...] = (
            *_preparation_action_steps(operation="refresh", options=options),
            *_preparation_process_steps(project, options=options),
        )
        return self._action_command(
            "database.refresh",
            "Refresh a project database",
            lambda: self._prepare_impl(project, options=options, coalesce=False),
            executor=executor,
            steps=steps,
            optional_steps=tuple(
                step.step_id
                for step in steps
                if step.step_id
                in {
                    "database.restore.exists-before",
                    "database.restore.exists-after",
                    "database.prepare.rollback",
                }
            ),
        )

    def _action_command(
        self,
        step_id: str,
        description: str,
        callback: Callable[[], T],
        *,
        executor: ProcessExecutor | None,
        steps: Sequence[PreparedStep | PreparedAction] = (),
        optional_steps: Sequence[str] = (),
    ) -> Command[T]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            SubprocessExecutor,
            prepared_command,
        )

        step = PreparedAction(
            step_id=step_id, action=step_id, description=description, mutating=True
        )

        def run(context: RunContext[T]) -> T:
            context.action(step_id)
            result = callback()
            for optional_step_id in optional_steps:
                if not context.consumed(optional_step_id):
                    context.skip(optional_step_id)
            return result

        prepared_steps: tuple[PreparedAction | PreparedStep, ...] = (step, *steps)

        return Command.from_prepared(
            ExecutionPlan(
                steps=tuple(item.public_projection() for item in prepared_steps),
            ),
            prepared_command(
                run,
                prepared_steps,
                executor=executor or SubprocessExecutor(),
                strict=bool(steps),
            ),
        )
