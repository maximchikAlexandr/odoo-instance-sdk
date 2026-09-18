"""Compatibility re-export shim."""

from __future__ import annotations

from odoo_instance_sdk.internal.dbprep.materialize import (
    DatabasePreparationCoordinator as DatabasePreparationCoordinator,
)
from odoo_instance_sdk.internal.dbprep.materialize import (
    _preparation_process_steps as _preparation_process_steps,
)
from odoo_instance_sdk.internal.dbprep.materialize import (
    preflight_restore as preflight_restore,
)
from odoo_instance_sdk.internal.dbprep.materialize import (
    prepare_download as prepare_download,
)
from odoo_instance_sdk.internal.dbprep.materialize import (
    prepare_restore as prepare_restore,
)
from odoo_instance_sdk.internal.dbprep.source import (
    DatabasePreparationFailureContext as DatabasePreparationFailureContext,
)
from odoo_instance_sdk.internal.dbprep.source import (
    ProjectRuntimeBinding as ProjectRuntimeBinding,
)
from odoo_instance_sdk.internal.dbprep.source import (
    RestorePreflight as RestorePreflight,
)
from odoo_instance_sdk.internal.dbprep.source import (
    _CatalogueRestoreSource as _CatalogueRestoreSource,
)
from odoo_instance_sdk.internal.dbprep.source import (
    _open_verified_zip as _open_verified_zip,
)
from odoo_instance_sdk.internal.dbprep.source import (
    _planned_project_identity as _planned_project_identity,
)
from odoo_instance_sdk.internal.dbprep.source import (
    _remote_password as _remote_password,
)
from odoo_instance_sdk.internal.dbprep.source import (
    build_selected_backup_restore_steps as build_selected_backup_restore_steps,
)
from odoo_instance_sdk.internal.dbprep.source import (
    canonical_project_identity as canonical_project_identity,
)
from odoo_instance_sdk.internal.dbprep.source import (
    capture_selected_backup_restore as capture_selected_backup_restore,
)
from odoo_instance_sdk.internal.dbprep.source import (
    classify_freshness as classify_freshness,
)
from odoo_instance_sdk.internal.dbprep.source import (
    compare_provenance as compare_provenance,
)
from odoo_instance_sdk.internal.dbprep.source import (
    generate_target_database as generate_target_database,
)
from odoo_instance_sdk.internal.dbprep.source import (
    materialize_selected_backup_dump as materialize_selected_backup_dump,
)
from odoo_instance_sdk.internal.dbprep.source import (
    materialize_selected_backup_filestore as materialize_selected_backup_filestore,
)
from odoo_instance_sdk.internal.dbprep.source import (
    preparation_lock as preparation_lock,
)
from odoo_instance_sdk.internal.dbprep.source import (
    relevant_manifest_conflicts as relevant_manifest_conflicts,
)
from odoo_instance_sdk.internal.dbprep.source import (
    reserve_target_database as reserve_target_database,
)
from odoo_instance_sdk.internal.dbprep.source import (
    resolve_runtime_binding as resolve_runtime_binding,
)
from odoo_instance_sdk.internal.dbprep.source import (
    resolve_test_source as resolve_test_source,
)
from odoo_instance_sdk.internal.dbprep.source_binding import (
    _catalogue_backup_preflight as _catalogue_backup_preflight,
)
from odoo_instance_sdk.internal.dbprep.source_binding import (
    _manifest_after_preparation as _manifest_after_preparation,
)
from odoo_instance_sdk.internal.dbprep.source_binding import (
    build_target_instance as build_target_instance,
)
from odoo_instance_sdk.internal.locks import exclusive_lock as exclusive_lock
from odoo_instance_sdk.internal.locks import exclusive_lock_until as exclusive_lock_until
from odoo_instance_sdk.internal.project_manifest import write_manifest as write_manifest

__all__ = [
    "DatabasePreparationCoordinator",
    "DatabasePreparationFailureContext",
    "ProjectRuntimeBinding",
    "RestorePreflight",
    "_CatalogueRestoreSource",
    "_catalogue_backup_preflight",
    "_manifest_after_preparation",
    "_open_verified_zip",
    "_planned_project_identity",
    "_preparation_process_steps",
    "build_selected_backup_restore_steps",
    "build_target_instance",
    "canonical_project_identity",
    "capture_selected_backup_restore",
    "classify_freshness",
    "compare_provenance",
    "exclusive_lock",
    "exclusive_lock_until",
    "generate_target_database",
    "materialize_selected_backup_dump",
    "materialize_selected_backup_filestore",
    "preflight_restore",
    "preparation_lock",
    "prepare_download",
    "prepare_restore",
    "relevant_manifest_conflicts",
    "reserve_target_database",
    "resolve_runtime_binding",
    "resolve_test_source",
    "write_manifest",
]
