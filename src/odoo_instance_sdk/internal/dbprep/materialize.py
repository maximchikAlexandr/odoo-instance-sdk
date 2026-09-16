from __future__ import annotations

# ruff: noqa: F821
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    DatabaseAlreadyExistsError,
    InstanceConfigurationError,
    MasterPasswordRequiredError,
)
from odoo_instance_sdk.internal.db_name import validate_db_name
from odoo_instance_sdk.internal.dbprep.source_1 import (
    DatabasePreparationFailureContext as DatabasePreparationFailureContext,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _CoalescedRestore as _CoalescedRestore,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _consume_action_if_planned as _consume_action_if_planned,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _load_project as _load_project,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _planned_project_identity as _planned_project_identity,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _reload_project as _reload_project,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _remote_password as _remote_password,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _RemoteRestoreSource as _RemoteRestoreSource,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _resolve_source_config as _resolve_source_config,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _target_config_path as _target_config_path,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _wait_for_preparation_lock as _wait_for_preparation_lock,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    build_selected_backup_restore_steps as build_selected_backup_restore_steps,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    canonical_project_identity as canonical_project_identity,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    generate_target_database as generate_target_database,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    resolve_runtime_binding as resolve_runtime_binding,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    resolve_test_source as resolve_test_source,
)
from odoo_instance_sdk.internal.dbprep.source_2 import (
    _annotate_retained_failure as _annotate_retained_failure,
)
from odoo_instance_sdk.internal.dbprep.source_2 import (
    _coerce_restore_source as _coerce_restore_source,
)
from odoo_instance_sdk.internal.locks import (
    backup_lock_path,
    exclusive_lock,
)
from odoo_instance_sdk.internal.odoo_config import infer_base_url, parse_odoo_config
from odoo_instance_sdk.internal.project_env import (
    effective_project_environment,
    load_project_environment,
)
from odoo_instance_sdk.internal.project_runtime import (
    resolve_project_http_port,
)
from odoo_instance_sdk.internal.test_instance_trust import require_test_instance_origin_approval
from odoo_instance_sdk.internal.urls import assert_local, normalize_base_url
from odoo_instance_sdk.models import (
    Backup,
    BackupBranchOrigin,
    BackupFreshness,
    DatabasePreparationAction,
    DatabasePreparationResult,
    DatabaseRefreshOptions,
    StartConfig,
)
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        RunContext,
    )
    from odoo_instance_sdk.models import DevelopmentEnvironment
    from odoo_instance_sdk.resources.instance import OdooInstance


def _restore_preflight(  # noqa: C901
    client: OdooClient,
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions,
    wait_for_lock: bool = True,
    coalesce: bool = False,
    target_database: str | None = None,
    restore_source: _RestoreSource | uuid.UUID | str | None = None,
) -> Iterator[RestorePreflight]:
    """Own the complete restore preflight and preparation-lock lifetime."""
    if not options.restore:
        raise ConfigError("restore preparation requires restore=True")

    selected_source = _coerce_restore_source(restore_source)
    initial, root = _load_project(project)
    initial_source = (
        resolve_test_source(initial, options)
        if isinstance(selected_source, _RemoteRestoreSource)
        else None
    )
    if initial_source is not None:
        require_test_instance_origin_approval(initial_source.config.base_url)
    _, _, project_id = canonical_project_identity(root)
    lock_context = (
        _wait_for_preparation_lock(project_id) if wait_for_lock else preparation_lock(project_id)
    )
    _consume_action_if_planned("database.prepare.lock")
    with lock_context:
        current = _reload_project(project, root)
        source = (
            resolve_test_source(current, options)
            if isinstance(selected_source, _RemoteRestoreSource)
            else None
        )
        if source is not None:
            require_test_instance_origin_approval(source.config.base_url)
        catalogue_backup = None
        if isinstance(selected_source, _CatalogueRestoreSource):
            _consume_action_if_planned("database.prepare.catalogue-backup")
            with exclusive_lock(backup_lock_path(str(selected_source.backup_id))):
                catalogue_backup = _catalogue_backup_preflight(
                    client.get_catalog(), selected_source, current
                )
        if coalesce and current.refresh_after_hours is not None:
            mapped = _latest_default_backup(client, current, root)
            if (
                isinstance(mapped, Backup)
                and classify_freshness(mapped, current.refresh_after_hours) is BackupFreshness.FRESH
                and source is not None
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
        configured_http_port = local_cfg.get("http_port")
        try:
            parsed_http_port = int(configured_http_port) if configured_http_port else None
        except ValueError:
            parsed_http_port = None
        local_cfg["http_port"] = str(
            resolve_project_http_port(current.preferred_http_port, parsed_http_port)
        )
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
        # Classify ownership before either remote download or local restore
        # effects. Pending and malformed claims must not be treated as legacy.
        cluster._restore_provenance()
        local = client.instance.from_config(
            source_config,
            base_url=local_url,
            master_password=local_password,
        )
        # ``from_config`` is intentionally transport-only and therefore does
        # not infer the project Compose claim. Restore provenance must carry
        # the exact active cluster and data root into the local instance so a
        # later public ``db.drop`` can validate the same ownership evidence.
        local._postgres_cluster = cluster
        if target_database is None:
            source_database = (
                source.config.database
                if source is not None
                else (catalogue_backup.database_name if catalogue_backup is not None else "")
            )
            if not source_database:
                raise ConfigError("catalogue backup was not resolved")
            target = reserve_target_database(source_database, local.databases.exists)
        else:
            validate_db_name(target_database)
            if local.databases.exists(target_database):
                raise DatabaseAlreadyExistsError(
                    f"Database {target_database!r} already exists on {local_url}"
                )
            target = target_database
        yield RestorePreflight(
            project=current,
            project_id=project_id,
            source=source,
            restore_source=selected_source,
            catalogue_backup=catalogue_backup,
            source_config=source_config,
            local_instance=local,
            runtime=runtime,
            postgres_cluster=cluster,
            target_database=target,
        )


def prepare_restore(  # noqa: C901
    client: OdooClient,
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions = DatabaseRefreshOptions(restore=True),
    coalesce: bool = False,
    restore_inputs: tuple[str, Path] | None = None,
    restore_source: _RestoreSource | uuid.UUID | str | None = None,
    target_database: str | None = None,
) -> DatabasePreparationResult:
    """Run the full restore preparation while retaining the project lock."""
    if not options.restore:
        raise ConfigError("restore preparation requires restore=True")
    _initial, root = _load_project(project)
    project_environment = load_project_environment(root)
    selected_source = _coerce_restore_source(restore_source)
    if target_database is not None:
        validate_db_name(target_database)
        if restore_inputs is None:
            source_config = _resolve_source_config(_initial, root)
            restore_inputs = (
                target_database,
                source_config.parent / f".odcli-refresh-{uuid.uuid4().hex}.conf",
            )
        elif restore_inputs[0] != target_database:
            raise ConfigError("target database was captured with a different value")
    remote_password = (
        _remote_password(effective_project_environment(project_environment))
        if isinstance(selected_source, _RemoteRestoreSource)
        else None
    )
    try:
        preflight_context = _restore_preflight(
            client,
            project,
            options=options,
            coalesce=coalesce,
            target_database=restore_inputs[0] if restore_inputs is not None else None,
        )
        if not isinstance(selected_source, _RemoteRestoreSource):
            preflight_context = _restore_preflight(
                client,
                project,
                options=options,
                coalesce=coalesce,
                target_database=restore_inputs[0] if restore_inputs is not None else None,
                restore_source=selected_source,
            )
        with preflight_context as preflight:
            current = preflight.project
            root = current.repository_root
            source = preflight.source
            backup: Backup | None = preflight.catalogue_backup
            database_confirmed = False
            default_switch_confirmed = False
            try:
                if isinstance(preflight.restore_source, _RemoteRestoreSource):
                    assert source is not None and remote_password is not None
                    remote = client.instance(
                        source.config.base_url, master_password=remote_password
                    )
                    _consume_action_if_planned("database.prepare.remote-backup")
                    backup = remote.databases.backup(
                        source.config.database,
                        source_git_branch=source.branch,
                    )
                _consume_action_if_planned("database.prepare.local-restore")
                assert backup is not None
                preflight.local_instance.databases.restore(
                    backup,
                    preflight.target_database,
                    copy=True,
                    neutralize_database=True,
                )
                database_confirmed = True
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
                        project_environment=project_environment,
                        target_config_path=restore_inputs[1]
                        if restore_inputs is not None
                        else None,
                        preferred_http_port=current.preferred_http_port,
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
                default_switch_confirmed = True
                return DatabasePreparationResult(
                    mode=DatabasePreparationAction.RESTORE,
                    backup=backup,
                    source_git_branch=backup.source_git_branch,
                    branch_origin=source.origin
                    if source is not None
                    else BackupBranchOrigin.UNKNOWN,
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
                    backup_id=(
                        preflight.restore_source.backup_id
                        if isinstance(preflight.restore_source, _CatalogueRestoreSource)
                        else None
                    ),
                    database_confirmed=database_confirmed,
                    default_switch_confirmed=default_switch_confirmed,
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
    except BaseException as exc:
        if not isinstance(getattr(exc, "failure_context", None), DatabasePreparationFailureContext):
            _annotate_retained_failure(
                error=exc,
                backup=None,
                target_database=restore_inputs[0] if restore_inputs is not None else None,
                backup_id=(
                    selected_source.backup_id
                    if isinstance(selected_source, _CatalogueRestoreSource)
                    else None
                ),
            )
        raise


def prepare_download(
    client: OdooClient,
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
    wait_for_lock: bool = False,
) -> DatabasePreparationResult:
    if options.restore:
        raise ConfigError("restore preparation is not available in download-only mode")
    initial, root = _load_project(project)
    password = _remote_password()
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
    restore_source: _RestoreSource | uuid.UUID | str | None = None,
) -> RestorePreflight:
    with _restore_preflight(
        client, project, options=options, wait_for_lock=False, restore_source=restore_source
    ) as preflight:
        return preflight


def _capture_restore_inputs(
    project: ProjectConfig | str | Path,
    options: DatabaseRefreshOptions,
    *,
    restore_source: _RestoreSource | uuid.UUID | str | None = None,
    client: OdooClient | None = None,
    target_database: str | None = None,
    selected_environment: DevelopmentEnvironment | None = None,
) -> tuple[str, Path] | None:
    """Capture target-bound paths without creating files or reserving a DB."""
    if not options.restore:
        return None
    if selected_environment is not None:
        if selected_environment.target_db_name is None:
            raise ConfigError("selected environment has no target database")
        source_config = Path(selected_environment.generated_config_path)
        return target_database or selected_environment.target_db_name, source_config
    initial, root = _load_project(project)
    source_config = _resolve_source_config(initial, root)
    selected_source = _coerce_restore_source(restore_source)
    if isinstance(selected_source, _RemoteRestoreSource):
        source_database = resolve_test_source(initial, options).config.database
    else:
        if client is None:
            return None
        projection = client.get_catalog()._resolve_backup_projection(str(selected_source.backup_id))
        source_database = projection.backup.database_name
    target = target_database or generate_target_database(source_database)
    validate_db_name(target)
    target_config = source_config.parent / f".odcli-refresh-{uuid.uuid4().hex}.conf"
    return target, target_config


def _preparation_process_steps(
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions,
    restore_inputs: tuple[str, Path] | None = None,
    selected_environment: DevelopmentEnvironment | None = None,
    selected_instance: OdooInstance | None = None,
    selected_restore: SelectedBackupRestorePayload | None = None,
) -> tuple[PreparedStep | PreparedAction, ...]:
    """Build the child-process part of a preparation command before effects.

    The preparation implementation deliberately keeps catalog, filesystem,
    HTTP, and lock work in its domain callback.  Its Git, PostgreSQL, compose,
    and optional Odoo-shell launches, however, must be visible in the same
    private snapshot so the active ledger can reject substitutions.
    """
    from odoo_instance_sdk.internal.proc import PreparedStep

    if selected_environment is not None:
        if selected_instance is None or selected_restore is None or restore_inputs is None:
            raise ConfigError("selected restore inputs were not captured")
        return build_selected_backup_restore_steps(
            selected_instance,
            target_database=restore_inputs[0],
            dump_path=selected_restore.dump_path,
            backup_format=selected_restore.format,
        )

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

    from odoo_instance_sdk.internal.pg.builder import build_psql_specification
    from odoo_instance_sdk.resources.database import _RESET_ADMIN_PASSWORD_SCRIPT
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    project_environment = load_project_environment(root)

    # The real Git identity is captured by the two process steps above and
    # resolved by the callback under the active ledger.  Planning must not
    # launch Git or otherwise inspect mutable repository state.
    _, _, project_id = _planned_project_identity(root)
    cluster = PostgresCluster._from_config(
        initial,
        repository_root=root,
        compose_runner=None,
        project_id=project_id,
    )
    compose_temporary_path = None
    if cluster.mode == "compose":
        compose_temporary_path = (
            cluster.compose_file.parent / f".compose-{uuid.uuid4().hex}.yaml.tmp"
        )
    steps.extend(
        step
        for step in cluster._ensure_running_steps(60.0, temporary_path=compose_temporary_path)
        if isinstance(step, PreparedStep)
    )

    source_config = _resolve_source_config(initial, root)
    parsed = parse_odoo_config(source_config)
    db_user = parsed.get("db_user")
    target_database = restore_inputs[0] if restore_inputs is not None else None
    if db_user and target_database is not None:
        db_host = parsed.get("db_host")
        raw_port = parsed.get("db_port")
        try:
            db_port = int(raw_port) if raw_port else 5432
        except ValueError:
            db_port = 5432
        password = parsed.get("db_password")
        steps.extend(
            (
                build_psql_specification(
                    step_id="database.restore.exists-reservation",
                    host=db_host,
                    port=db_port,
                    user=db_user,
                    password=password,
                    database="postgres",
                    args=(
                        "-c",
                        f"SELECT 1 FROM pg_database WHERE datname='{target_database.replace(chr(39), chr(39) + chr(39))}'",
                    ),
                    _trusted_args=("-t", "-A"),
                    timeout=30.0,
                ).prepared_step,
                build_psql_specification(
                    step_id="database.restore.exists-before",
                    host=db_host,
                    port=db_port,
                    user=db_user,
                    password=password,
                    database="postgres",
                    args=(
                        "-c",
                        f"SELECT 1 FROM pg_database WHERE datname='{target_database.replace(chr(39), chr(39) + chr(39))}'",
                    ),
                    _trusted_args=("-t", "-A"),
                    timeout=30.0,
                ).prepared_step,
                build_psql_specification(
                    step_id="database.restore.exists-after",
                    host=db_host,
                    port=db_port,
                    user=db_user,
                    password=password,
                    database="postgres",
                    args=(
                        "-c",
                        f"SELECT 1 FROM pg_database WHERE datname='{target_database.replace(chr(39), chr(39) + chr(39))}'",
                    ),
                    _trusted_args=("-t", "-A"),
                    timeout=30.0,
                ).prepared_step,
            )
        )

    if options.reset_admin_password:
        from odoo_instance_sdk.resources.instance import _build_shell_script_step

        runtime = resolve_runtime_binding(initial, root)
        start_config = StartConfig.from_odoo_config(source_config)
        start_config.http_port = resolve_project_http_port(
            initial.preferred_http_port, start_config.http_port
        )
        if restore_inputs is None:
            raise ConfigError("reset admin preparation inputs were not captured")
        start_config.config_path = str(restore_inputs[1])
        start_config.dbfilter = restore_inputs[0]
        start_config.db_name = restore_inputs[0]
        secret_config_path = str(restore_inputs[1]) + ".secret"
        shell_step, _, _, _ = _build_shell_script_step(
            start_config,
            executable_prefix=runtime.command_prefix,
            default_cwd=runtime.runtime_cwd,
            source=_RESET_ADMIN_PASSWORD_SCRIPT,
            commit=True,
            secret_config_path=secret_config_path,
            project_environment=project_environment,
        )
        steps.append(shell_step)
    return tuple(steps)


def _preparation_action_steps(
    *,
    operation: str,
    options: DatabaseRefreshOptions,
    restore_source: _RestoreSource | uuid.UUID | str | None = None,
) -> tuple[PreparedAction, ...]:
    """Return honest in-process boundaries for the preparation coordinator."""
    from odoo_instance_sdk.internal.proc import PreparedAction

    selected_source = _coerce_restore_source(restore_source)
    action_steps = [
        PreparedAction(
            step_id="database.prepare.lock",
            action="acquire-preparation-lock",
            description="Serialize project database preparation",
            mutating=True,
        ),
    ]
    if isinstance(selected_source, _RemoteRestoreSource):
        action_steps.append(
            PreparedAction(
                step_id="database.prepare.remote-backup",
                action="download-remote-backup",
                description="Request the selected remote database backup",
                mutating=True,
            )
        )
    else:
        action_steps.append(
            PreparedAction(
                step_id="database.prepare.catalogue-backup",
                action="inspect-catalogue-backup",
                description="Validate the selected registered backup artifact",
                read_only=True,
            )
        )
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
        restore_source: _RestoreSource | uuid.UUID | str | None = None,
        target_database: str | None = None,
    ) -> DatabasePreparationResult:
        return self.prepare_command(
            project,
            options=options,
            coalesce=coalesce,
            restore_source=restore_source,
            target_database=target_database,
        ).run()

    def prepare_command(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        coalesce: bool = False,
        restore_source: _RestoreSource | uuid.UUID | str | None = None,
        target_database: str | None = None,
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabasePreparationResult]:
        restore_inputs = _capture_restore_inputs(
            project,
            options,
            restore_source=restore_source,
            client=self.client,
            target_database=target_database,
        )
        steps: tuple[PreparedStep | PreparedAction, ...] = (
            *_preparation_action_steps(
                operation="prepare", options=options, restore_source=restore_source
            ),
            *_preparation_process_steps(project, options=options, restore_inputs=restore_inputs),
        )
        return self._action_command(
            "database.prepare",
            "Prepare a project database",
            lambda: self._prepare_impl(
                project,
                options=options,
                coalesce=coalesce,
                restore_inputs=restore_inputs,
                restore_source=restore_source,
                target_database=target_database,
            ),
            executor=executor,
            steps=steps,
            optional_steps=tuple(
                step.step_id
                for step in steps
                if step.step_id
                in {
                    "database.restore.exists-reservation",
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
        restore_inputs: tuple[str, Path] | None = None,
        restore_source: _RestoreSource | uuid.UUID | str | None = None,
        target_database: str | None = None,
    ) -> DatabasePreparationResult:
        if options.restore:
            return prepare_restore(
                self.client,
                project,
                options=options,
                coalesce=coalesce,
                restore_inputs=restore_inputs,
                restore_source=restore_source,
                target_database=target_database,
            )
        return prepare_download(self.client, project, options=options, wait_for_lock=True)

    def refresh_database(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        restore_source: _RestoreSource | uuid.UUID | str | None = None,
        target_database: str | None = None,
    ) -> DatabasePreparationResult:
        return self.refresh_database_command(
            project,
            options=options,
            restore_source=restore_source,
            target_database=target_database,
        ).run()

    def refresh_database_command(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        restore_source: _RestoreSource | uuid.UUID | str | None = None,
        target_database: str | None = None,
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabasePreparationResult]:
        restore_inputs = _capture_restore_inputs(
            project,
            options,
            restore_source=restore_source,
            client=self.client,
            target_database=target_database,
        )
        steps: tuple[PreparedStep | PreparedAction, ...] = (
            *_preparation_action_steps(
                operation="refresh", options=options, restore_source=restore_source
            ),
            *_preparation_process_steps(project, options=options, restore_inputs=restore_inputs),
        )
        return self._action_command(
            "database.refresh",
            "Refresh a project database",
            lambda: self._prepare_impl(
                project,
                options=options,
                coalesce=False,
                restore_inputs=restore_inputs,
                restore_source=restore_source,
                target_database=target_database,
            ),
            executor=executor,
            steps=steps,
            optional_steps=tuple(
                step.step_id
                for step in steps
                if step.step_id
                in {
                    "database.restore.exists-reservation",
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
            ),
        )
