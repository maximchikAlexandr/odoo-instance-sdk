"""Catalogue-backed identity resolution for focused environment inspection."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.internal.paths import get_catalog_path
from odoo_instance_sdk.internal.repo_key import repo_key


def resolve_env_show_catalogue(selector: str | None, *, cwd: Path) -> tuple[str, str]:
    """Resolve the selected environment and project before monitor probes."""
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog_path = get_catalog_path(ensure_exists=False)
    try:
        if not catalog_path.is_file():
            raise RuntimeError("environment catalogue unavailable; cannot resolve env show")
    except OSError as exc:
        raise RuntimeError("environment catalogue unavailable; cannot resolve env show") from exc
    try:
        catalog = BackupCatalog(db_path=catalog_path)
        try:
            rows = catalog.list_environments(include_removed=selector is None)
        finally:
            catalog.close()
    except (BackupCatalogError, OSError) as exc:
        raise RuntimeError("environment catalogue read failed; cannot resolve env show") from exc

    row = _select_catalogue_row(rows, selector, cwd=cwd)
    project_id = (
        row["project_id"]
        or f"project_{repo_key(Path(str(row['repository_root'])), Path(str(row['git_common_dir'])))}"
    )
    return str(row["id"]), str(project_id)


def _select_catalogue_row(
    rows: Sequence[sqlite3.Row], selector: str | None, *, cwd: Path
) -> sqlite3.Row:
    if selector is None:
        current = cwd.resolve()
        candidates = []
        for row in rows:
            worktree = Path(str(row["worktree_path"])).resolve()
            try:
                current.relative_to(worktree)
            except ValueError:
                continue
            candidates.append(row)
        if not candidates:
            raise ValueError("No environment matches the current working directory")
        if len(candidates) > 1:
            raise ValueError(
                "Ambiguous current environment: " + ", ".join(str(row["id"]) for row in candidates)
            )
        return candidates[0]

    by_id = [row for row in rows if str(row["id"]) == selector]
    candidates = by_id or [row for row in rows if str(row["name"]) == selector]
    if not candidates:
        raise ValueError(f"Environment not found: {selector}")
    if len(candidates) > 1:
        raise ValueError(
            f"Ambiguous environment selector {selector!r}: "
            + ", ".join(str(row["id"]) for row in candidates)
        )
    return candidates[0]
