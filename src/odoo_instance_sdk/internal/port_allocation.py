from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from odoo_instance_sdk.exceptions import EnvironmentConflictError
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

PortKind = Literal["http", "postgres"]

_HTTP_RANGE_START = 8000
_HTTP_RANGE_END = 9000
_PG_RANGE_START = 5468
_PG_RANGE_END = 65534


def find_free_port(
    kind: PortKind,
    catalog: BackupCatalog | None,
    *,
    exclude_project: Path | None = None,
    host: str = "127.0.0.1",
    requested: int | None = None,
    project: ProjectConfig | None = None,
    reserved_ports: Sequence[int] = (),
) -> int:
    """Find a free loopback port by scanning projects + environments + generated configs.

    Single source of truth is the existing config files, not a separate registry.
    ``catalog`` provides registered project roots and environment rows.
    Project manifests provide postgres ports and assigned/preferred HTTP ports.
    Generated ``odoo.conf`` files provide per-env HTTP ports.
    ``probe_address`` provides the live check.

    ``exclude_project`` skips the current project's own manifest so re-init
    doesn't see its own ports as a collision.
    """
    used = _collect_used_ports(catalog, exclude_project) | set(reserved_ports)

    if requested is not None:
        if requested in used or probe_address(host, requested) is not AddressState.FREE:
            raise EnvironmentConflictError(
                "port_in_use",
                f"Port {requested} already allocated or occupied",
                details={"port": requested, "kind": kind},
            )
        return requested

    start, end = (
        (_HTTP_RANGE_START, _HTTP_RANGE_END) if kind == "http" else (_PG_RANGE_START, _PG_RANGE_END)
    )
    preferred = None
    if kind == "http":
        preferred = project.preferred_http_port if project else None
        if preferred is None:
            preferred = 8069
        if (
            start <= preferred <= end
            and preferred not in used
            and probe_address(host, preferred) is AddressState.FREE
        ):
            return preferred

    for candidate in range(start, end + 1):
        if candidate == preferred:
            continue
        if candidate not in used and probe_address(host, candidate) is AddressState.FREE:
            return candidate

    message = (
        f"No available HTTP port in automatic allocation range {start}\u2013{end}; "
        "all candidates are allocated or unavailable. Pass --http-port explicitly."
        if kind == "http"
        else f"No free port in range {start}-{end}; pass an explicit port"
    )
    raise EnvironmentConflictError(
        "no_free_port",
        message,
        details={"range": [str(start), str(end)], "kind": kind},
    )


def _collect_used_ports(
    catalog: BackupCatalog | None,
    exclude_project: Path | None,
) -> set[int]:
    used: set[int] = set()
    project_roots: set[Path] = set()
    exclude_resolved = exclude_project.resolve() if exclude_project is not None else None

    catalog_path = _paths.get_catalog_path(ensure_exists=False)
    if catalog is None:
        rows = _read_environment_paths(catalog_path)
        project_roots.update(_read_registered_project_paths(catalog_path))
    else:
        rows = catalog.list_environments()
        project_roots.update(Path(str(row["repository_root"])) for row in catalog.list_projects())
    for row in rows:
        repo_root = Path(str(row["repository_root"]))
        project_roots.add(repo_root)
        http_port = _http_port_from_generated_config(row["generated_config_path"])
        if http_port is not None:
            used.add(http_port)

    for repo_root in project_roots:
        if exclude_resolved is not None:
            try:
                if repo_root.resolve() == exclude_resolved:
                    continue
            except OSError:
                pass
        _add_manifest_ports(repo_root, used)

    return used


def _read_environment_paths(path: Path) -> list[sqlite3.Row]:
    if not path.is_file():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT repository_root, generated_config_path "
            "FROM environments WHERE state != 'removed'"
        ).fetchall()
    finally:
        connection.close()


def _read_registered_project_paths(path: Path) -> set[Path]:
    if not path.is_file():
        return set()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            Path(str(row[0]))
            for row in connection.execute(
                "SELECT repository_root FROM projects WHERE registration_state = 'registered'"
            )
        }
    finally:
        connection.close()


def _add_manifest_ports(repo_root: Path, used: set[int]) -> None:
    manifest = repo_root / ".odcli" / "project.toml"
    if not manifest.is_file():
        return
    try:
        cfg = ProjectConfig.load(repo_root)
    except Exception:
        return
    if cfg.preferred_http_port is not None:
        used.add(cfg.preferred_http_port)
    if cfg.assigned_http_port is not None:
        used.add(cfg.assigned_http_port)
    if cfg.postgres is not None and cfg.postgres.port is not None:
        used.add(cfg.postgres.port)


def _http_port_from_generated_config(path: Path) -> int | None:
    """Read http_port from a generated odoo.conf (single source for per-env HTTP port)."""
    try:
        cfg = parse_odoo_config(Path(str(path)))
    except Exception:
        return None
    raw = cfg.get("http_port")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
