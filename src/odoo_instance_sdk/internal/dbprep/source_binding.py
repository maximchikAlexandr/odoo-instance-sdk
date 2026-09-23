from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.exceptions import (
    ConfigError,
    EnvironmentConflictError,
)
from odoo_instance_sdk.internal.dbprep.source import (
    DatabasePreparationFailureContext,
    ProjectRuntimeBinding,
    _CatalogueRestoreSource,
    _RemoteRestoreSource,
    _resolve_source_config,
    _RestoreSource,
    _target_config_path,
    _write_target_config,
    relevant_manifest_conflicts,
    resolve_test_source,
    retained_artifact_context,
)
from odoo_instance_sdk.internal.locks import (
    database_preparation_artifact_lock_path,
)
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.internal.project_runtime import (
    resolve_project_http_port,
)
from odoo_instance_sdk.internal.urls import assert_local, normalize_base_url
from odoo_instance_sdk.models import (
    Backup,
    BackupFormat,
    NoBackup,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, BackupProjection

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster


@contextlib.contextmanager
def build_target_instance(
    client: OdooClient,
    *,
    source_config: Path,
    target_database: str,
    runtime: ProjectRuntimeBinding,
    postgres_cluster: PostgresCluster,
    project_id: str,
    project_environment: Mapping[str, str] | None = None,
    target_config_path: Path | None = None,
    preferred_http_port: int | None = None,
) -> Iterator[OdooInstance]:
    """Build a target-only instance and remove its ephemeral config on exit."""
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.resources.instance import OdooInstance

    target_config = _target_config_path(source_config, target_config_path)
    try:
        _write_target_config(source_config, target_config, target_database)
        parsed = parse_odoo_config(target_config)
        from odoo_instance_sdk.models import StartConfig

        start_config = StartConfig.from_odoo_config(target_config)
        start_config.http_port = resolve_project_http_port(
            preferred_http_port, start_config.http_port
        )
        local_url = normalize_base_url(
            f"http://{start_config.http_interface}:{start_config.http_port}"
        )
        assert_local(local_url)
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
                project_environment=project_environment or {},
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
    backup_id: uuid.UUID | None = None,
    database_confirmed: bool = False,
    default_switch_confirmed: bool = False,
) -> None:
    """Attach only non-secret retained-artifact identifiers to a failure."""
    restore_stage_id = getattr(error, "restore_stage_id", None)
    restore_stage_elapsed = getattr(error, "restore_stage_elapsed", None)
    context = DatabasePreparationFailureContext(
        retained_backup_id=backup.id if backup is not None else None,
        retained_database=target_database,
        backup_id=backup.id if backup is not None else backup_id,
        database_confirmed=database_confirmed,
        default_switch_confirmed=default_switch_confirmed,
        restore_stage_id=restore_stage_id if isinstance(restore_stage_id, str) else None,
        restore_stage_elapsed=(
            restore_stage_elapsed if isinstance(restore_stage_elapsed, (int, float)) else None
        ),
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


def _coerce_restore_source(source: _RestoreSource | uuid.UUID | str | None) -> _RestoreSource:
    if source is None:
        return _RemoteRestoreSource()
    if isinstance(source, _RemoteRestoreSource):
        return source
    if isinstance(source, _CatalogueRestoreSource):
        source = source.backup_id
    try:
        return _CatalogueRestoreSource(uuid.UUID(str(source)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigError("catalogue backup identifier must be a complete UUID") from exc


def _catalogue_backup_preflight(
    catalog: BackupCatalog,
    source: _CatalogueRestoreSource,
    project: ProjectConfig,
) -> Backup:
    """Validate the exact published artifact before any local DB effect."""
    projection: BackupProjection = catalog._resolve_backup_projection(str(source.backup_id))
    if projection.state.value != "available":
        raise ConfigError("catalogue backup is not available")
    backup = projection.backup
    path = Path(backup.path)
    if path.is_symlink() or not path.is_file() or not os.access(path, os.R_OK):
        raise ConfigError("catalogue backup file is missing or not a regular readable file")
    if backup.size_bytes != path.stat().st_size:
        raise ConfigError("catalogue backup size does not match its recorded identity")
    catalog.verify_identity(backup, verify_content=True)

    if backup.format == BackupFormat.ZIP:
        from odoo_instance_sdk.internal.backup_validation import (
            raise_restore_preflight_errors,
            raise_zip_validation_error,
            validate_zip,
        )

        validation = validate_zip(path)
        if not validation.valid:
            raise_zip_validation_error(validation)
            raise ConfigError(
                "selected backup archive is unavailable or invalid"
            )  # pragma: no cover
        if validation.db_name != backup.database_name:
            raise ConfigError("selected backup database name does not match catalog metadata")
        raise_restore_preflight_errors(validation.uncompressed_bytes, None)

    # A configured remote source is a project binding hint, never an authority
    # for ownership.  Projects without it can still restore a registered point.
    if project.test_instance is not None:
        expected = resolve_test_source(project).config
        if expected.base_url != backup.source_base_url or (
            expected.database is not None and expected.database != backup.database_name
        ):
            raise ConfigError("catalogue backup is not bound to this project source")
    return backup
