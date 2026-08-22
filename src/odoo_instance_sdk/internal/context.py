from __future__ import annotations

import sqlite3
from pathlib import Path

from odoo_instance_sdk.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentResolutionError,
    ProjectContextError,
    ProjectManifestNotFoundError,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment import DevelopmentEnvironment


def _find_nearest_manifest(start: Path, boundary: Path | None) -> Path | None:
    current = start.resolve()
    if not current.is_dir():
        current = current.parent
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        candidate = current / ".odcli" / "project.toml"
        if candidate.is_file():
            return candidate
        if current == boundary or current.parent == current:
            return None
        current = current.parent
    return None


def resolve_project(explicit: Path | None, cwd: Path | None = None) -> ProjectConfig | Path:
    base = (cwd or Path.cwd()).resolve()
    from odoo_instance_sdk.internal.git_worktree import rev_parse_toplevel

    if explicit is not None:
        selected = Path(explicit).resolve()
        try:
            boundary: Path | None = rev_parse_toplevel(selected)
        except Exception:
            boundary = None
        manifest = _find_nearest_manifest(selected, boundary)
        if manifest is not None:
            try:
                return ProjectConfig.load(manifest.parent.parent)
            except ProjectManifestNotFoundError as e:
                raise ProjectContextError(str(e)) from e
        raise ProjectContextError(
            f"Explicit --project {explicit} is not inside a project with .odcli/project.toml; run odcli init"
        )
    try:
        boundary = rev_parse_toplevel(base)
    except Exception:
        boundary = None
    manifest = _find_nearest_manifest(base, boundary)
    if manifest is not None:
        try:
            return ProjectConfig.load(manifest.parent.parent)
        except ProjectManifestNotFoundError as e:
            raise ProjectContextError(str(e)) from e
    registered = _project_from_registered_worktree(base)
    if registered is not None:
        return ProjectConfig.load(registered)
    raise ProjectContextError(
        "No .odcli/project.toml found upward from cwd; run odcli init or pass --project PATH"
    )


def _project_from_registered_worktree(cwd: Path) -> Path | None:
    """Resolve nested registered worktrees without creating/opening a catalog."""
    from odoo_instance_sdk.internal.paths import get_catalog_path

    catalog = get_catalog_path()
    if not catalog.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{catalog}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT repository_root, worktree_path, git_common_dir "
                "FROM environments WHERE state <> 'removed'"
            ).fetchall()
    except sqlite3.Error:
        return None
    from odoo_instance_sdk.internal.git_worktree import rev_parse_git_common_dir, rev_parse_toplevel

    for root, worktree, common_dir in rows:
        try:
            cwd.relative_to(Path(str(worktree)).resolve())
        except ValueError:
            continue
        try:
            if rev_parse_git_common_dir(rev_parse_toplevel(cwd)) != Path(str(common_dir)):
                continue
        except Exception:
            continue
        return Path(str(root))
    return None


def resolve_environment(
    client: object,
    explicit: str | None,
    *,
    cwd: Path | None = None,
) -> DevelopmentEnvironment:
    base = (cwd or Path.cwd()).resolve()
    environments = _list_environments(client)
    if explicit is not None:
        return _resolve_explicit(explicit, environments)
    env = _infer_from_worktree(base, environments)
    if env is not None:
        return env
    candidates = [f"{e.name} ({e.id})" for e in environments]
    raise EnvironmentResolutionError(
        "No environment resolved; pass --env or cd into a registered worktree",
        candidates=candidates,
    )


def _resolve_explicit(
    explicit: str, environments: list[DevelopmentEnvironment]
) -> DevelopmentEnvironment:
    by_id = [e for e in environments if str(e.id) == explicit]
    if len(by_id) == 1:
        return by_id[0]
    by_name = [e for e in environments if e.name == explicit]
    if len(by_name) > 1:
        raise EnvironmentResolutionError(
            f"Ambiguous environment selector {explicit!r}",
            candidates=[str(e.id) for e in by_name],
        )
    if len(by_name) == 1:
        return by_name[0]
    if len(by_id) == 0 and len(by_name) == 0:
        raise EnvironmentNotFoundError(explicit)
    raise EnvironmentResolutionError(
        f"Ambiguous environment selector {explicit!r}",
        candidates=[str(e.id) for e in by_id + by_name],
    )


def _list_environments(client: object) -> list[DevelopmentEnvironment]:
    list_method = getattr(client, "environments", None)
    if list_method is None:
        return []
    try:
        return list_method.list()  # type: ignore[no-any-return]
    except NotImplementedError:
        return []


def _infer_from_worktree(
    cwd: Path,
    environments: list[DevelopmentEnvironment],
) -> DevelopmentEnvironment | None:
    for env in environments:
        try:
            worktree = Path(env.worktree_path).resolve()
        except OSError:
            continue
        try:
            cwd.relative_to(worktree)
        except ValueError:
            continue
        try:
            from odoo_instance_sdk.internal.git_worktree import (
                rev_parse_git_common_dir,
                rev_parse_toplevel,
            )

            if (
                rev_parse_git_common_dir(rev_parse_toplevel(cwd)).resolve()
                != Path(env.git_common_dir).resolve()
            ):
                continue
        except Exception:
            continue
        return env
    return None
