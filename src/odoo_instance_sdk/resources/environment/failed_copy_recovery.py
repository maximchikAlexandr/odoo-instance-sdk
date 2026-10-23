"""Narrow recovery checks for failed caller-owned COPY environments."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import EnvironmentConflictError
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.db_name import validate_filestore_containment
from odoo_instance_sdk.internal.odoo_config import get_admin_passwd, parse_odoo_config
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import EnvironmentCodeOwnership
from odoo_instance_sdk.models.backup import DevelopmentEnvironment
from odoo_instance_sdk.resources.environment.checkout_artifacts import (
    _has_symlink_component,
    _row_to_env,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    CopyCleanupPlan,
    EnvironmentDatabaseMode,
    EnvironmentState,
)
from odoo_instance_sdk.storage.backup_catalog import (
    BackupCatalog,
    CopyJournalStage,
    normalize_db_host,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedStep
    from odoo_instance_sdk.resources.environment.cleanup import _CleanupMixin
    from odoo_instance_sdk.resources.instance import OdooInstance


def failed_copy_absence_probe_steps(
    cleanup: _CleanupMixin, env: DevelopmentEnvironment
) -> tuple[PreparedStep, ...]:
    """Plan exact PostgreSQL probes used by failed-adoption recovery."""
    from odoo_instance_sdk.internal.proc import PreparedStep

    if (
        env.db_mode is not EnvironmentDatabaseMode.COPY
        or env.state is not EnvironmentState.FAILED
        or env.code_ownership is not EnvironmentCodeOwnership.CALLER_OWNED
        or env.backup_id is not None
        or not env.source_db_name
        or env.target_db_name is None
        or env.source_db_name == env.target_db_name
        or not env.adoption_input_fingerprint
        or not env.last_error
        or Path(env.generated_config_path).is_file()
        or cleanup._client.get_catalog().get_copy_journal(str(env.id)) is not None
    ):
        return ()
    source_rows = [
        row
        for row in cleanup._client.get_catalog().list_environments(project_id=env.project_id)
        if row["id"] != str(env.id)
        and row["state"] == EnvironmentState.READY.value
        and row["db_mode"] == EnvironmentDatabaseMode.COPY.value
        and row["target_db_name"] == env.source_db_name
        and row["repository_root"] == env.repository_root
    ]
    if len(source_rows) != 1:
        return ()
    source = _row_to_env(source_rows[0])
    config_path = Path(source.generated_config_path)
    if not config_path.is_file():
        return ()
    config = parse_odoo_config(config_path)
    master_password = get_admin_passwd(config)
    if master_password is None:
        return ()
    instance = cleanup._client.instance.from_config(config_path, master_password=master_password)
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    cluster = PostgresCluster.from_project(Path(env.repository_root))
    instance._postgres_cluster = cluster
    endpoint_host = normalize_db_host(instance.config.db_host or "127.0.0.1")
    endpoint_port = instance.config.db_port or 5432
    if (
        normalize_db_host(cluster.endpoint_host) != endpoint_host
        or cluster.endpoint_port != endpoint_port
    ):
        return ()
    source_probe = instance.databases._psql_probe_for(
        env.source_db_name, "environment.remove.database.exists-before"
    )
    target_probe = instance.databases._psql_probe_for(
        env.target_db_name, "environment.remove.database.exists-after"
    )
    if not isinstance(source_probe, PreparedStep) or not isinstance(target_probe, PreparedStep):
        return ()
    return (source_probe, target_probe)


def preflight_failed_copy_without_config(  # noqa: C901 -- keep recovery ownership evidence together
    cleanup: _CleanupMixin,
    catalog: BackupCatalog,
    env: DevelopmentEnvironment,
) -> bool:
    """Permit metadata cleanup only when a failed adopted target is proven absent."""
    if (
        env.state is not EnvironmentState.FAILED
        or env.code_ownership is not EnvironmentCodeOwnership.CALLER_OWNED
        or env.backup_id is not None
        or not env.source_db_name
        or env.target_db_name is None
        or env.source_db_name == env.target_db_name
        or not env.adoption_input_fingerprint
        or not env.last_error
    ):
        return False

    from odoo_instance_sdk.internal.pg.adopted_drop import validate_adopted_copy_claim
    from odoo_instance_sdk.internal.pg.drop_types import (
        AdoptedCopyDatabaseClaim,
        catalog_database_in_use,
    )
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    if catalog_database_in_use(catalog, env.target_db_name, allow_environment_id=str(env.id)):
        raise EnvironmentConflictError(
            "copy_recovery_unverified",
            "failed COPY target remains bound to another environment",
        )

    source_rows = [
        row
        for row in catalog.list_environments(project_id=env.project_id)
        if row["id"] != str(env.id)
        and row["state"] == EnvironmentState.READY.value
        and row["db_mode"] == EnvironmentDatabaseMode.COPY.value
        and row["target_db_name"] == env.source_db_name
        and row["repository_root"] == env.repository_root
    ]
    if len(source_rows) != 1:
        raise EnvironmentConflictError(
            "copy_recovery_unverified", "failed COPY source environment is not uniquely bound"
        )
    source = _row_to_env(source_rows[0])
    source_journal = catalog.get_copy_journal(str(source.id))
    if (
        source.code_ownership is not EnvironmentCodeOwnership.CALLER_OWNED
        or not source.adoption_input_fingerprint
        or source_journal is None
        or source_journal["stage"] != CopyJournalStage.RESTORED.value
        or source_journal["target_database"] != env.source_db_name
        or source.backup_id is None
        or source_journal["backup_id"] != str(source.backup_id)
    ):
        raise EnvironmentConflictError(
            "copy_recovery_unverified", "failed COPY source restore evidence is incomplete"
        )

    source_root = Path(source.artifact_root or Path(source.worktree_path).parent)
    expected_source_root = (
        _paths.get_environments_root()
        / repo_key(Path(source.repository_root), Path(source.git_common_dir))
        / str(source.id)
    )
    source_config = Path(source.generated_config_path)
    if (
        source_root.absolute() != expected_source_root.absolute()
        or _has_symlink_component(source_root)
        or source_config != source_root / "odoo.conf"
        or not source_config.is_file()
        or _has_symlink_component(source_config)
    ):
        raise EnvironmentConflictError(
            "copy_recovery_unverified", "failed COPY source configuration ownership is invalid"
        )

    source_plan: CopyCleanupPlan | None = cleanup._preflight_copy_remove(catalog, source)
    if (
        source_plan is None
        or source_plan.stage is not CopyJournalStage.RESTORED
        or source_plan.instance is None
    ):
        raise EnvironmentConflictError(
            "copy_recovery_unverified", "failed COPY source restore cannot be revalidated"
        )
    instance = source_plan.instance
    cluster = PostgresCluster.from_project(Path(env.repository_root))
    instance._postgres_cluster = cluster
    endpoint_host = normalize_db_host(instance.config.db_host or "127.0.0.1")
    endpoint_port = instance.config.db_port or 5432
    if (
        normalize_db_host(cluster.endpoint_host) != endpoint_host
        or cluster.endpoint_port != endpoint_port
    ):
        raise EnvironmentConflictError(
            "copy_recovery_unverified",
            "failed COPY endpoint does not match source restore evidence",
        )

    claim = AdoptedCopyDatabaseClaim(
        environment_id=str(env.id),
        database=env.target_db_name,
        endpoint_host=instance.config.db_host or "127.0.0.1",
        endpoint_port=endpoint_port,
        project_id=env.project_id or "",
        checkout_repository_root=env.checkout_repository_root or "",
        checkout_git_common_dir=env.checkout_git_common_dir or "",
        adoption_input_fingerprint=env.adoption_input_fingerprint,
    )
    validate_adopted_copy_claim(catalog, instance, cluster, env.target_db_name, claim)
    source_claim = AdoptedCopyDatabaseClaim(
        environment_id=str(source.id),
        database=source.target_db_name or "",
        endpoint_host=instance.config.db_host or "127.0.0.1",
        endpoint_port=endpoint_port,
        project_id=source.project_id or "",
        checkout_repository_root=source.checkout_repository_root or "",
        checkout_git_common_dir=source.checkout_git_common_dir or "",
        adoption_input_fingerprint=source.adoption_input_fingerprint,
    )
    validate_adopted_copy_claim(
        catalog, instance, cluster, source.target_db_name or "", source_claim
    )
    try:
        source_exists = instance.databases.exists(env.source_db_name)
        target_exists = instance.databases.exists(env.target_db_name)
    except Exception as exc:
        raise EnvironmentConflictError(
            "copy_recovery_unverified", "failed COPY database absence could not be verified"
        ) from exc
    if not source_exists:
        raise EnvironmentConflictError(
            "copy_recovery_unverified", "failed COPY source database is absent"
        )
    if target_exists:
        raise EnvironmentConflictError(
            "copy_recovery_unverified", "failed COPY target database still exists"
        )

    source_config_values = parse_odoo_config(source_config)
    data_dir_value = source_config_values.get("data_dir")
    if not data_dir_value:
        raise EnvironmentConflictError(
            "copy_recovery_unverified", "failed COPY source filestore binding is unavailable"
        )
    data_dir = Path(data_dir_value).expanduser().absolute()
    if _has_symlink_component(data_dir):
        raise EnvironmentConflictError(
            "copy_recovery_unverified", "failed COPY filestore root contains a symlink"
        )
    target_filestore = validate_filestore_containment(data_dir, env.target_db_name)
    if target_filestore.is_symlink() or target_filestore.exists():
        raise EnvironmentConflictError(
            "copy_recovery_unverified", "failed COPY target filestore still exists"
        )
    return True


def validate_copy_journal_ownership(
    env: DevelopmentEnvironment, journal: sqlite3.Row, instance: OdooInstance
) -> None:
    """Fail closed unless config, environment row and durable journal agree."""
    expected_backup = str(env.backup_id) if env.backup_id is not None else None
    values_match = (
        str(journal["environment_id"]) == str(env.id)
        and str(journal["target_database"]) == str(env.target_db_name)
        and (str(journal["backup_id"]) if journal["backup_id"] is not None else None)
        == expected_backup
        and str(journal["db_host"]) == normalize_db_host(instance.config.db_host)
        and int(journal["db_port"]) == (instance.config.db_port or 5432)
        and (str(journal["db_user"]) if journal["db_user"] is not None else None)
        == instance.config.db_user
    )
    if not values_match:
        raise EnvironmentConflictError(
            "copy_cluster_mismatch",
            "copy journal, environment ownership, and generated config disagree",
        )
