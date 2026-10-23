"""Snapshot provenance serialization for self-update recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

from odoo_instance_sdk.execution import JsonValue

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.self_update import InstalledProvenance


def snapshot_metadata(
    provenance: InstalledProvenance, ref: str, journal_version: int
) -> dict[str, JsonValue]:
    metadata: dict[str, JsonValue] = {
        "version": journal_version,
        "previous_version": provenance.version,
        "package_revision": provenance.version,
        "previous_sha": provenance.commit_id,
        "install_requirement": _install_requirement(provenance, ref),
        "source_repo": provenance.source_repo,
        "direct_requirement": provenance.direct_requirement,
        "requested_extras": list(provenance.requested_extras),
        "target_ref": ref,
        "snapshot_sha": provenance.commit_id,
    }
    companion = provenance.companion
    if companion is not None:
        metadata["companion"] = {
            "version": companion.version,
            "previous_sha": companion.package_sha,
            "previous_sdk_sha": companion.sdk_sha,
            "source_repo": companion.source_repo,
            "environment": str(companion.environment),
        }
    return metadata


def _install_requirement(provenance: InstalledProvenance, ref: str) -> str:
    from odoo_instance_sdk.internal.self_update import _install_argv

    return _install_argv(
        ref,
        source_repo=provenance.source_repo,
        extras=provenance.requested_extras,
    )[-1]
