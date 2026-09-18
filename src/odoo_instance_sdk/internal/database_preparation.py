"""Compatibility re-export shim."""

from __future__ import annotations  # noqa: I001

from odoo_instance_sdk.internal.dbprep.materialize import (
    DatabasePreparationCoordinator as DatabasePreparationCoordinator,
    _preparation_process_steps as _preparation_process_steps,
    preflight_restore as preflight_restore,
    prepare_download as prepare_download,
    prepare_restore as prepare_restore,
)
from odoo_instance_sdk.internal.dbprep.source import (
    DatabasePreparationFailureContext as DatabasePreparationFailureContext,
    ProjectRuntimeBinding as ProjectRuntimeBinding,
    RestorePreflight as RestorePreflight,
    _CatalogueRestoreSource as _CatalogueRestoreSource,
    _open_verified_zip as _open_verified_zip,
    _planned_project_identity as _planned_project_identity,
    _remote_password as _remote_password,
    build_selected_backup_restore_steps as build_selected_backup_restore_steps,
    canonical_project_identity as canonical_project_identity,
    capture_selected_backup_restore as capture_selected_backup_restore,
    classify_freshness as classify_freshness,
    compare_provenance as compare_provenance,
    generate_target_database as generate_target_database,
    materialize_selected_backup_dump as materialize_selected_backup_dump,
    materialize_selected_backup_filestore as materialize_selected_backup_filestore,
    preparation_lock as preparation_lock,
    relevant_manifest_conflicts as relevant_manifest_conflicts,
    reserve_target_database as reserve_target_database,
    resolve_runtime_binding as resolve_runtime_binding,
    resolve_test_source as resolve_test_source,
)
from odoo_instance_sdk.internal.dbprep.source_binding import (
    _catalogue_backup_preflight as _catalogue_backup_preflight,
    _manifest_after_preparation as _manifest_after_preparation,
    build_target_instance as build_target_instance,
)
from odoo_instance_sdk.internal.locks import (
    exclusive_lock as exclusive_lock,
    exclusive_lock_until as exclusive_lock_until,
)
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
