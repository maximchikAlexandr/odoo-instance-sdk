"""Safety policy helpers for the captured self-update command boundary."""

from __future__ import annotations

import sys

from odoo_instance_sdk.exceptions import PreflightFailedError
from odoo_instance_sdk.internal.proc import PreparedStep, RunContext
from odoo_instance_sdk.internal.self_update import (
    _MIN_PYTHON,
    _SOURCE_REPO,
    _SUPPORTED_PLATFORMS,
    InstalledProvenance,
    _canonical_supported_source_repo,
    _catalog_schema_version,
    _is_full_sha,
    _local_source_repo_path,
    _preflight_disk_check,
    _storage_migration_state,
    _validate_catalog_migration_path,
    _validate_storage_migration_path,
)
from odoo_instance_sdk.internal.self_update_ancestry import RevisionRelation
from odoo_instance_sdk.models.update import UpdateResult

_ORIGIN_STEP_ID = "update.inspect.origin"
_ANCESTRY_CLEANUP_STEP_ID = "update.inspect.ancestry-cleanup"


def source_origin_step(provenance: InstalledProvenance) -> PreparedStep | None:
    path = _local_source_repo_path(provenance.source_repo)
    if path is None:
        return None
    return PreparedStep(
        step_id=_ORIGIN_STEP_ID,
        argv=("git", "remote", "get-url", "origin"),
        cwd=str(path),
        read_only=True,
        timeout=30.0,
    )


def probe_source_repo(provenance: InstalledProvenance) -> str | None:
    canonical = _canonical_supported_source_repo(provenance.source_repo)
    if canonical is not None:
        return canonical
    return _SOURCE_REPO if source_origin_step(provenance) is not None else None


def skip_revision_probe(
    context: RunContext[UpdateResult],
    steps: tuple[PreparedStep, ...],
    cleanup_step_id: str | None,
) -> None:
    for step in steps:
        context.skip(step.step_id)
    if cleanup_step_id is not None:
        context.skip(cleanup_step_id)


def validate_downgrade_snapshot_restore() -> str | None:
    """Require a proven rollback state before permitting a downgrade."""
    storage_state = _storage_migration_state()
    if storage_state != "absent":
        return f"downgrade snapshot cannot restore storage state {storage_state!r}"
    if _catalog_schema_version() == "unreadable":
        return "downgrade snapshot cannot restore an unreadable catalog"
    return None


def revision_preflight_error(
    *,
    ref: str,
    provenance: InstalledProvenance,
    allow_downgrade: bool,
    relation: RevisionRelation | None = None,
    source_origin_verified: bool = False,
) -> str | None:
    if not _is_full_sha(ref):
        return None
    installed_sha = provenance.commit_id
    if installed_sha is None or not _is_full_sha(installed_sha):
        return "cannot verify revision ancestry; installed commit is unavailable"
    canonical_source_repo = _canonical_supported_source_repo(provenance.source_repo)
    if canonical_source_repo is None and not source_origin_verified:
        return "cannot verify revision ancestry; source provenance is unsupported"
    if relation is None:
        return "cannot verify revision ancestry; target history is unavailable"
    if relation == "ancestor" and not allow_downgrade:
        return "downgrade refused without --allow-downgrade"
    if relation == "ancestor":
        error = validate_downgrade_snapshot_restore()
        if error is not None:
            return error
    if relation not in {"same", "descendant", "ancestor"}:
        return "cannot verify revision ancestry; target history is unavailable"
    return None


def preflight_error(
    *,
    ref: str,
    provenance: InstalledProvenance,
    allow_downgrade: bool,
    relation: RevisionRelation | None = None,
    source_origin_verified: bool = False,
) -> str | None:
    if sys.version_info < _MIN_PYTHON:
        return (
            f"Python {'.'.join(str(part) for part in _MIN_PYTHON)}+ is required; "
            f"found {sys.version_info.major}.{sys.version_info.minor}"
        )
    if sys.platform not in _SUPPORTED_PLATFORMS:
        return f"unsupported platform {sys.platform!r}"
    disk_error = _preflight_disk_check()
    if disk_error is not None:
        return disk_error
    if _catalog_schema_version() == "unreadable":
        return "catalog schema is unreadable"
    try:
        _validate_catalog_migration_path()
        _validate_storage_migration_path()
    except PreflightFailedError as exc:
        return str(exc)
    return revision_preflight_error(
        ref=ref,
        provenance=provenance,
        allow_downgrade=allow_downgrade,
        relation=relation,
        source_origin_verified=source_origin_verified,
    )


__all__ = [
    "preflight_error",
    "probe_source_repo",
    "revision_preflight_error",
    "skip_revision_probe",
    "source_origin_step",
    "validate_downgrade_snapshot_restore",
]
