"""Ownership proofs for caller-owned COPY database cleanup."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.db_name import validate_filestore_containment
from odoo_instance_sdk.internal.pg.drop_types import (
    AdoptedCopyDatabaseClaim,
    DropOwnershipEvidence,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def validate_adopted_copy_claim(
    catalog: BackupCatalog,
    instance: OdooInstance,
    cluster: PostgresCluster,
    database: str,
    claim: AdoptedCopyDatabaseClaim,
) -> None:
    """Require exact adoption and endpoint identity before either drop path."""
    if (
        claim.environment_id == ""
        or claim.database != database
        or not claim.project_id
        or not claim.checkout_repository_root
        or not claim.checkout_git_common_dir
        or not claim.adoption_input_fingerprint
    ):
        raise ConfigError("adopted COPY database drop claim does not match target")
    from odoo_instance_sdk.storage.backup_catalog import normalize_db_host

    if (
        normalize_db_host(claim.endpoint_host) != normalize_db_host(cluster.endpoint_host)
        or claim.endpoint_port != cluster.endpoint_port
        or normalize_db_host(claim.endpoint_host)
        != normalize_db_host(instance.config.db_host or "127.0.0.1")
        or claim.endpoint_port != (instance.config.db_port or 5432)
    ):
        raise ConfigError("adopted COPY database drop endpoint evidence does not match")

    row = catalog.get_environment(claim.environment_id)
    if (
        row is None
        or row["code_ownership"] != "caller_owned"
        or row["project_id"] != claim.project_id
        or row["checkout_repository_root"] != claim.checkout_repository_root
        or row["checkout_git_common_dir"] != claim.checkout_git_common_dir
        or row["adoption_input_fingerprint"] != claim.adoption_input_fingerprint
        or row["checkout_repository_root"] != row["worktree_path"]
        or row["checkout_git_common_dir"] != row["git_common_dir"]
    ):
        raise ConfigError("adopted COPY database drop requires adoption ownership evidence")
    if row["project_id"] != f"project_{cluster._project_id}" or str(row["repository_root"]) != str(
        cluster._repository_root
    ):
        raise ConfigError("adopted COPY database drop project evidence does not match")


def adopted_copy_ownership_evidence(  # noqa: C901 -- bounded external adopted-copy evidence gate
    catalog: BackupCatalog,
    instance: OdooInstance,
    cluster: PostgresCluster,
    database: str,
    claim: AdoptedCopyDatabaseClaim,
    *,
    expected_backup_id: str | None,
    expected_rollback_database: str | None,
    volume_step_id: str | None,
    container_step_id: str | None,
    allow_reconciled_absent: bool,
) -> DropOwnershipEvidence:
    """Validate one adopted COPY target on the project's approved external endpoint."""
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    if not isinstance(cluster, PostgresCluster) or cluster.mode != "external":
        raise ConfigError("adopted COPY database drop requires approved shared PostgreSQL")
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    if context is not None:
        for step_id in (volume_step_id, container_step_id):
            if step_id is not None and context.planned(step_id) and not context.consumed(step_id):
                context.skip(step_id)
    validate_adopted_copy_claim(catalog, instance, cluster, database, claim)
    row = catalog.get_environment(claim.environment_id)
    if row is None:
        raise ConfigError("adopted COPY database drop requires adoption ownership evidence")
    from odoo_instance_sdk.internal.pg.drop import (
        _catalog_database_in_use,
        _validate_allowed_environment,
    )

    _validate_allowed_environment(
        catalog,
        claim.environment_id,
        database,
        expected_backup_id,
        expected_rollback_database,
    )
    if _catalog_database_in_use(catalog, database, allow_environment_id=claim.environment_id):
        raise ConfigError("database is bound to an active environment or process")

    binding_database = (
        database if expected_rollback_database is None else str(row["target_db_name"])
    )
    event = catalog._latest_database_event(
        cluster.endpoint_host, cluster.endpoint_port, binding_database
    )
    if event is None or event["event_type"] not in {"restored", "dropped"}:
        raise ConfigError("adopted COPY database drop requires an exact current lifecycle event")
    event_is_reconciled_absent = event["event_type"] == "dropped"
    if event_is_reconciled_absent and not allow_reconciled_absent:
        raise ConfigError("adopted COPY database drop requires an exact current lifecycle event")
    binding = catalog._latest_restore_binding(
        cluster.endpoint_host, cluster.endpoint_port, binding_database
    )
    if (
        binding is None
        or binding["cluster_id"] is not None
        or binding["state"]
        not in ({"complete"} if event_is_reconciled_absent else {"complete", "incomplete"})
    ):
        raise ConfigError("adopted COPY database drop restore identity does not match")
    if not event_is_reconciled_absent and (
        event["cluster_id"] is not None
        or event["database_name"] != binding_database
        or event["backup_id"] != binding["backup_id"]
        or event["source_kind"] != binding["source_kind"]
        or event["source_sha256"] != binding["source_sha256"]
        or event["data_directory"] != binding["data_directory"]
    ):
        raise ConfigError("adopted COPY database drop restore identity does not match")
    source_kind = binding["source_kind"]
    source_sha256 = binding["source_sha256"]
    backup_id = binding["backup_id"]
    if source_kind not in {"catalogue", "local_archive"}:
        raise ConfigError("adopted COPY database drop restore provenance is invalid")
    if source_kind == "catalogue":
        if not isinstance(backup_id, str) or not backup_id or source_sha256 is not None:
            raise ConfigError("adopted COPY database drop restore provenance is invalid")
    elif (
        backup_id is not None
        or not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(char not in "0123456789abcdef" for char in source_sha256)
    ):
        raise ConfigError("adopted COPY database drop restore provenance is invalid")
    if expected_backup_id is not None and backup_id != expected_backup_id:
        raise ConfigError("adopted COPY database drop backup ownership changed")
    data_directory = binding["data_directory"]
    if source_kind == "local_archive" and not safe_proven_filestore(
        data_directory if isinstance(data_directory, str) else None, binding_database
    ):
        raise ConfigError("adopted COPY local archive lacks contained data-directory evidence")
    return DropOwnershipEvidence(
        event_type="dropped" if event_is_reconciled_absent else "restored",
        event_sequence=int(event["sequence"]),
        cluster_id=f"external:{cluster.endpoint}",
        compose_project="",
        volume_name="",
        backup_id=backup_id,
        source_kind=source_kind,
        source_sha256=source_sha256,
        data_directory=data_directory if isinstance(data_directory, str) else None,
        restore_state=str(binding["state"]),
    )


def safe_proven_filestore(data_directory: str | None, database: str) -> bool:
    if data_directory is None:
        return False
    base = Path(data_directory)
    root = base / "filestore"
    if base.is_symlink() or not base.is_dir() or root.is_symlink() or not root.is_dir():
        return False
    try:
        validate_filestore_containment(base, database)
    except ConfigError:
        return False
    candidate = root / database
    return not candidate.is_symlink()
