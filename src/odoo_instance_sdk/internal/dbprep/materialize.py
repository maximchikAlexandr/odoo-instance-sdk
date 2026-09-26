from __future__ import annotations  # noqa: I001 -- keep dbprep source bindings in one import block; remove when Ruff supports grouped aliases.

import contextlib
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec

from odoo_instance_sdk.exceptions import (
    AdminPasswordRequiredError,
    ConfigError,
    DatabaseAlreadyExistsError,
    InstanceConfigurationError,
    MasterPasswordRequiredError,
)
from odoo_instance_sdk.internal.dbprep.materialize_steps import (
    _preparation_action_steps,
    _preparation_process_steps,
)
from odoo_instance_sdk.internal.db_name import validate_db_name
from odoo_instance_sdk.internal.dbprep.source import (
    DatabasePreparationFailureContext as DatabasePreparationFailureContext,
    RestorePreflight as RestorePreflight,
    SelectedBackupRestorePayload as SelectedBackupRestorePayload,
    T as T,
    _CatalogueRestoreSource as _CatalogueRestoreSource,
    _LocalArchiveRestoreSource as _LocalArchiveRestoreSource,
    _CoalescedRestore as _CoalescedRestore,
    _consume_action_if_planned as _consume_action_if_planned,
    _load_project as _load_project,
    _planned_project_identity as _planned_project_identity,
    _reload_project as _reload_project,
    _remote_password as _remote_password,
    _RemoteRestoreSource as _RemoteRestoreSource,
    _resolve_source_config as _resolve_source_config,
    _RestoreSource as _RestoreSource,
    _RestoreSourceInput as _RestoreSourceInput,
    _skip_preparation_branch as _skip_preparation_branch,
    _target_config_path as _target_config_path,
    _assert_verified_snapshot_unchanged as _assert_verified_snapshot_unchanged,
    _materialize_verified_snapshot as _materialize_verified_snapshot,
    capture_local_archive_restore as capture_local_archive_restore,
    cleanup_selected_backup_restore as cleanup_selected_backup_restore,
    build_selected_backup_restore_steps as build_selected_backup_restore_steps,
    canonical_project_identity as canonical_project_identity,
    classify_freshness as classify_freshness,
    generate_target_database as generate_target_database,
    reserve_target_database as reserve_target_database,
    resolve_remote_database_name as resolve_remote_database_name,
    resolve_runtime_binding as resolve_runtime_binding,
    resolve_test_source as resolve_test_source,
)
from odoo_instance_sdk.internal.dbprep.source_binding import (
    _annotate_retained_failure as _annotate_retained_failure,
    _catalogue_backup_preflight as _catalogue_backup_preflight,
    _coerce_restore_source as _coerce_restore_source,
    _latest_default_backup as _latest_default_backup,
    _manifest_after_preparation as _manifest_after_preparation,
    build_target_instance as build_target_instance,
)
from odoo_instance_sdk.internal.locks import (
    backup_lock_path,
    database_preparation_lock_path,
    exclusive_lock,
    exclusive_lock_until,
)
from odoo_instance_sdk.internal.odoo_config import (
    _resolve_data_dir,
    infer_base_url,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.project_env import (
    effective_project_environment,
    load_project_environment,
)
from odoo_instance_sdk.internal.project_manifest import write_manifest
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


@contextlib.contextmanager
def _wait_for_preparation_lock(project_id: str, *, timeout: float = 300.0) -> Iterator[None]:
    with exclusive_lock_until(
        database_preparation_lock_path(project_id), time.monotonic() + timeout
    ):
        yield


@contextlib.contextmanager
def _restore_preflight(  # noqa: C901
    client: OdooClient,
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions,
    wait_for_lock: bool = True,
    coalesce: bool = False,
    target_database: str | None = None,
    restore_source: _RestoreSourceInput = None,
    selected_restore: SelectedBackupRestorePayload | None = None,
    remote_password: str | None = None,
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
    lock_path = database_preparation_lock_path(project_id)
    lock_context = (
        _wait_for_preparation_lock(project_id) if wait_for_lock else exclusive_lock(lock_path)
    )
    _consume_action_if_planned("database.prepare.lock")
    with lock_context:
        current = _reload_project(project, root)
        source = (
            resolve_test_source(current, options)
            if isinstance(selected_source, _RemoteRestoreSource)
            else None
        )
        if isinstance(selected_source, _LocalArchiveRestoreSource):
            _consume_action_if_planned("database.prepare.local-archive.validate")
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
        resolved_database: str | None = None
        if (
            isinstance(selected_source, _RemoteRestoreSource)
            and source is not None
            and source.config.database is None
        ):
            assert remote_password is not None
            remote_for_resolution = client.instance(
                source.config.base_url, master_password=remote_password
            )
            resolved_database = resolve_remote_database_name(None, remote_for_resolution.databases)
        if target_database is None:
            source_database = (
                resolved_database
                if resolved_database is not None
                else (
                    source.config.database
                    if source is not None
                    else (
                        catalogue_backup.database_name
                        if catalogue_backup is not None
                        else (
                            selected_restore.database_name if selected_restore is not None else ""
                        )
                    )
                )
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
            resolved_database=resolved_database,
            selected_restore=selected_restore,
        )


def prepare_restore(  # noqa: C901
    client: OdooClient,
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions = DatabaseRefreshOptions(restore=True),
    coalesce: bool = False,
    restore_inputs: tuple[str, Path] | None = None,
    restore_source: _RestoreSourceInput = None,
    selected_restore: SelectedBackupRestorePayload | None = None,
    target_database: str | None = None,
    admin_password: str | None = None,
    admin_password_provenance: str = "environment",
) -> DatabasePreparationResult:
    """Run the full restore preparation while retaining the project lock."""
    if not options.restore:
        raise ConfigError("restore preparation requires restore=True")
    if options.reset_admin_password and not admin_password:
        raise AdminPasswordRequiredError("administrator password is required before admin reset")
    resolved_admin_password: str = admin_password or ""
    _initial, root = _load_project(project)
    project_environment = load_project_environment(root)
    selected_source = _coerce_restore_source(restore_source)
    if isinstance(selected_source, _LocalArchiveRestoreSource) and selected_restore is None:
        selected_restore = _capture_selected_restore(project, selected_source)
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
        if selected_restore is None:
            preflight_context = _restore_preflight(
                client,
                project,
                options=options,
                coalesce=coalesce,
                target_database=restore_inputs[0] if restore_inputs is not None else None,
                remote_password=remote_password,
            )
        else:
            preflight_context = _restore_preflight(
                client,
                project,
                options=options,
                coalesce=coalesce,
                target_database=restore_inputs[0] if restore_inputs is not None else None,
                remote_password=remote_password,
                selected_restore=selected_restore,
            )
        if not isinstance(selected_source, _RemoteRestoreSource):
            if selected_restore is None:
                preflight_context = _restore_preflight(
                    client,
                    project,
                    options=options,
                    coalesce=coalesce,
                    target_database=restore_inputs[0] if restore_inputs is not None else None,
                    restore_source=selected_source,
                    remote_password=remote_password,
                )
            else:
                preflight_context = _restore_preflight(
                    client,
                    project,
                    options=options,
                    coalesce=coalesce,
                    target_database=restore_inputs[0] if restore_inputs is not None else None,
                    restore_source=selected_source,
                    remote_password=remote_password,
                    selected_restore=selected_restore,
                )
        with preflight_context as preflight:
            current = preflight.project
            root = current.repository_root
            source = preflight.source
            backup: Backup | None = preflight.catalogue_backup
            local_restore = selected_restore or preflight.selected_restore
            database_confirmed = False
            default_switch_confirmed = False
            try:
                if isinstance(preflight.restore_source, _RemoteRestoreSource):
                    assert source is not None and remote_password is not None
                    remote = client.instance(
                        source.config.base_url, master_password=remote_password
                    )
                    database_name = (
                        preflight.resolved_database
                        if preflight.resolved_database is not None
                        else source.config.database
                    )
                    assert database_name is not None
                    _consume_action_if_planned("database.prepare.remote-backup")
                    catalog_project_id = f"project_{preflight.project_id}"
                    from odoo_instance_sdk.internal.repo_key import git_common_dir

                    client.get_catalog()._register_project(
                        catalog_project_id, root, git_common_dir(root)
                    )
                    from odoo_instance_sdk.internal.restore_stages import restore_stage

                    with restore_stage("backup_prepare"):
                        backup = remote.databases.backup(
                            database_name,
                            source_git_branch=source.branch,
                            project_id=catalog_project_id,
                        )
                else:
                    from odoo_instance_sdk.internal.restore_stages import publish_stage

                    publish_stage("backup_prepare", kind="completed")
                _consume_action_if_planned("database.prepare.local-restore")
                if isinstance(preflight.restore_source, _LocalArchiveRestoreSource):
                    if local_restore is None:
                        raise ConfigError(  # noqa: TRY301
                            "local archive restore evidence was not captured"
                        )
                    restore_payload = local_restore
                    _consume_action_if_planned("database.prepare.local-archive.snapshot")
                    _materialize_verified_snapshot(restore_payload)
                    _assert_verified_snapshot_unchanged(restore_payload)
                else:
                    assert backup is not None
                from odoo_instance_sdk.internal.restore_stages import (
                    restore_stage as _restore_stage,
                )

                with (
                    _restore_stage("auxiliary_start"),
                    _restore_stage("db_restore"),
                    _restore_stage("db_verify"),
                    _restore_stage("filestore_restore"),
                ):
                    if isinstance(preflight.restore_source, _LocalArchiveRestoreSource):
                        preflight.local_instance.databases._restore_local_archive(
                            restore_payload,
                            preflight.target_database,
                            copy=True,
                            neutralize_database=True,
                        )
                    else:
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
                    from odoo_instance_sdk.internal.restore_stages import (
                        restore_stage as _restore_stage,
                    )

                    with (
                        _restore_stage("admin_reset"),
                        build_target_instance(
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
                        ) as target_instance,
                    ):
                        target_instance.databases.reset_admin_password(
                            admin_password=resolved_admin_password,
                            provenance=admin_password_provenance,
                        )
                    reset_completed = True

                final_config = _manifest_after_preparation(root, current)
                switched = msgspec.structs.replace(
                    final_config, default_source_database=preflight.target_database
                )
                _consume_action_if_planned("database.prepare.default-switch")
                from odoo_instance_sdk.internal.restore_stages import (
                    restore_stage as _restore_stage,
                )

                with _restore_stage("default_switch"):
                    write_manifest(root, switched)
                default_switch_confirmed = True
                return DatabasePreparationResult(
                    mode=DatabasePreparationAction.RESTORE,
                    backup=backup,
                    source_git_branch=backup.source_git_branch if backup is not None else None,
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
                    source_kind=(
                        "local_archive"
                        if isinstance(preflight.restore_source, _LocalArchiveRestoreSource)
                        else "catalogue"
                        if isinstance(preflight.restore_source, _CatalogueRestoreSource)
                        else None
                    ),
                    source_sha256=local_restore.verified_sha256
                    if local_restore is not None
                    and isinstance(preflight.restore_source, _LocalArchiveRestoreSource)
                    else None,
                )
                raise
            finally:
                if local_restore is not None:
                    cleanup_selected_backup_restore(local_restore)
                    _consume_action_if_planned("database.prepare.local-archive.cleanup")
    except _CoalescedRestore as coalesced:
        _skip_preparation_branch(
            (
                "database.prepare.remote-backup",
                "database.prepare.local-archive.validate",
                "database.prepare.local-archive.snapshot",
                "database.prepare.local-archive.cleanup",
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
        if isinstance(selected_source, _LocalArchiveRestoreSource):
            _consume_action_if_planned("database.prepare.local-archive.cleanup")
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
                source_kind=(
                    "local_archive"
                    if isinstance(selected_source, _LocalArchiveRestoreSource)
                    else None
                ),
                source_sha256=(
                    selected_restore.verified_sha256
                    if selected_restore is not None
                    and isinstance(selected_source, _LocalArchiveRestoreSource)
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
    repo_root, git_common, project_key = canonical_project_identity(root)
    lock_path = database_preparation_lock_path(project_key)
    lock_context = (
        _wait_for_preparation_lock(project_key) if wait_for_lock else exclusive_lock(lock_path)
    )
    _consume_action_if_planned("database.prepare.lock")
    with lock_context:
        current = _reload_project(project, root)
        source = resolve_test_source(current, options)
        require_test_instance_origin_approval(source.config.base_url)
        remote = client.instance(source.config.base_url, master_password=password)
        database_name = resolve_remote_database_name(source.config.database, remote.databases)
        _consume_action_if_planned("database.prepare.remote-backup")
        catalog_project_id = f"project_{project_key}"
        client.get_catalog()._register_project(catalog_project_id, repo_root, git_common)
        backup = remote.databases.backup(
            database_name,
            source_git_branch=source.branch,
            project_id=catalog_project_id,
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
    restore_source: _RestoreSourceInput = None,
    remote_password: str | None = None,
) -> RestorePreflight:
    with _restore_preflight(
        client,
        project,
        options=options,
        wait_for_lock=False,
        restore_source=restore_source,
        remote_password=remote_password,
    ) as preflight:
        return preflight


def _capture_restore_inputs(
    project: ProjectConfig | str | Path,
    options: DatabaseRefreshOptions,
    *,
    restore_source: _RestoreSourceInput = None,
    client: OdooClient | None = None,
    target_database: str | None = None,
    selected_environment: DevelopmentEnvironment | None = None,
    selected_restore: SelectedBackupRestorePayload | None = None,
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
    elif isinstance(selected_source, _CatalogueRestoreSource):
        if client is None:
            return None
        projection = client.get_catalog()._resolve_backup_projection(str(selected_source.backup_id))
        source_database = projection.backup.database_name
    elif selected_restore is not None:
        source_database = selected_restore.database_name
    else:
        source_database = None
    if source_database is None:
        source_database = "remote"
    target = target_database or generate_target_database(source_database)
    validate_db_name(target)
    target_config = source_config.parent / f".odcli-refresh-{uuid.uuid4().hex}.conf"
    return target, target_config


def _capture_selected_restore(
    project: ProjectConfig | str | Path,
    restore_source: _RestoreSourceInput,
) -> SelectedBackupRestorePayload | None:
    """Capture local archive evidence without creating project artifacts."""
    selected_source = _coerce_restore_source(restore_source)
    if not isinstance(selected_source, _LocalArchiveRestoreSource):
        return None
    _project, root = _load_project(project)
    source_config = _resolve_source_config(_project, root)
    start_config = StartConfig.from_odoo_config(source_config)
    data_dir = (
        _resolve_data_dir(start_config.data_dir, source_config) if start_config.data_dir else None
    )
    return capture_local_archive_restore(
        selected_source,
        snapshot_directory=root / ".odcli" / "restore",
        data_dir=data_dir,
    )


@dataclass(slots=True)
class DatabasePreparationCoordinator:
    client: OdooClient

    def prepare(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        coalesce: bool = False,
        restore_source: _RestoreSourceInput = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
    ) -> DatabasePreparationResult:
        return self.prepare_command(
            project,
            options=options,
            coalesce=coalesce,
            restore_source=restore_source,
            target_database=target_database,
            admin_password=admin_password,
            admin_password_provenance=admin_password_provenance,
        ).run()

    def prepare_command(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        coalesce: bool = False,
        restore_source: _RestoreSourceInput = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabasePreparationResult]:
        selected_restore = (
            _capture_selected_restore(project, restore_source) if options.restore else None
        )
        restore_inputs = _capture_restore_inputs(
            project,
            options,
            restore_source=restore_source,
            client=self.client,
            target_database=target_database,
            selected_restore=selected_restore,
        )
        steps: tuple[PreparedStep | PreparedAction, ...] = (
            *_preparation_action_steps(
                operation="prepare", options=options, restore_source=restore_source
            ),
            *_preparation_process_steps(
                project,
                options=options,
                restore_inputs=restore_inputs,
                admin_password=admin_password,
            ),
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
                selected_restore=selected_restore,
                target_database=target_database,
                admin_password=admin_password,
                admin_password_provenance=admin_password_provenance,
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
                    "database.prepare.local-archive.cleanup",
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
        restore_source: _RestoreSourceInput = None,
        selected_restore: SelectedBackupRestorePayload | None = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
    ) -> DatabasePreparationResult:
        if options.restore:
            return prepare_restore(
                self.client,
                project,
                options=options,
                coalesce=coalesce,
                restore_inputs=restore_inputs,
                restore_source=restore_source,
                selected_restore=selected_restore,
                target_database=target_database,
                admin_password=admin_password,
                admin_password_provenance=admin_password_provenance,
            )
        return prepare_download(self.client, project, options=options, wait_for_lock=True)

    def refresh_database(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        restore_source: _RestoreSourceInput = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
    ) -> DatabasePreparationResult:
        return self.refresh_database_command(
            project,
            options=options,
            restore_source=restore_source,
            target_database=target_database,
            admin_password=admin_password,
            admin_password_provenance=admin_password_provenance,
        ).run()

    def refresh_database_command(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        restore_source: _RestoreSourceInput = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabasePreparationResult]:
        selected_restore = (
            _capture_selected_restore(project, restore_source) if options.restore else None
        )
        restore_inputs = _capture_restore_inputs(
            project,
            options,
            restore_source=restore_source,
            client=self.client,
            target_database=target_database,
            selected_restore=selected_restore,
        )
        steps: tuple[PreparedStep | PreparedAction, ...] = (
            *_preparation_action_steps(
                operation="refresh", options=options, restore_source=restore_source
            ),
            *_preparation_process_steps(
                project,
                options=options,
                restore_inputs=restore_inputs,
                admin_password=admin_password,
            ),
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
                selected_restore=selected_restore,
                target_database=target_database,
                admin_password=admin_password,
                admin_password_provenance=admin_password_provenance,
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
                    "database.prepare.local-archive.cleanup",
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
