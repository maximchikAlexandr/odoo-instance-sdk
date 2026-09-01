from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from odoo_instance_sdk.exceptions import EnvironmentConflictError
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

PortKind = Literal["http", "postgres"]

_HTTP_RANGE_START = 8069
_HTTP_RANGE_END = 8099
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
) -> int:
    """Find a free loopback port by scanning existing catalog + project manifests + generated configs.

    Single source of truth is the existing config files, not a separate registry.
    ``catalog`` provides environment rows (project roots + generated_config paths).
    Project manifests provide postgres ports and preferred HTTP ports.
    Generated ``odoo.conf`` files provide per-env HTTP ports.
    ``probe_address`` provides the live check.

    ``exclude_project`` skips the current project's own manifest so re-init
    doesn't see its own ports as a collision.
    """
    used = _collect_used_ports(catalog, exclude_project)

    if requested is not None:
        if requested in used or probe_address(host, requested) is not AddressState.FREE:
            raise EnvironmentConflictError(
                "port_in_use",
                f"Port {requested} already allocated or occupied",
                details={"port": requested, "kind": kind},
            )
        return requested

    start, end = _range_for(kind, project)
    candidate = start
    while candidate <= end:
        if candidate not in used and probe_address(host, candidate) is AddressState.FREE:
            return candidate
        candidate += 1

    raise EnvironmentConflictError(
        "no_free_port",
        f"No free port in range {start}-{end}; pass an explicit port",
        details={"range": [str(start), str(end)], "kind": kind},
    )


def _range_for(kind: PortKind, project: ProjectConfig | None) -> tuple[int, int]:
    if kind == "http":
        preferred = project.preferred_http_port if project else None
        if preferred is not None:
            return preferred, _HTTP_RANGE_END
        return _HTTP_RANGE_START, _HTTP_RANGE_END
    return _PG_RANGE_START, _PG_RANGE_END


def _collect_used_ports(
    catalog: BackupCatalog | None,
    exclude_project: Path | None,
) -> set[int]:
    used: set[int] = set()
    project_roots: set[Path] = set()
    exclude_resolved = exclude_project.resolve() if exclude_project is not None else None

    if catalog is not None:
        for row in catalog.list_environments():
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
