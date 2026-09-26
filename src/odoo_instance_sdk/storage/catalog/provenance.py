from __future__ import annotations

from odoo_instance_sdk.exceptions import BackupCatalogError


def restore_provenance(
    backup_id: str | None,
    source_kind: str | None,
    source_sha256: str | None,
) -> tuple[str, str | None, str | None]:
    """Validate and normalize one complete restore evidence tuple."""
    kind = source_kind or ("catalogue" if backup_id is not None else None)
    if kind == "catalogue":
        if not isinstance(backup_id, str) or not backup_id.strip() or source_sha256 is not None:
            raise BackupCatalogError("catalogue restore provenance requires only backup_id")
        return kind, backup_id, None
    if kind == "local_archive":
        if backup_id is not None or not isinstance(source_sha256, str):
            raise BackupCatalogError(
                "local_archive restore provenance requires source_sha256 and no backup_id"
            )
        digest = source_sha256
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise BackupCatalogError("local_archive source_sha256 must be lowercase 64-hex")
        return kind, None, digest
    raise BackupCatalogError("restore provenance requires a supported source_kind")
