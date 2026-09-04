from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentResolutionError,
    ProjectContextError,
    ProjectManifestNotFoundError,
)
from odoo_instance_sdk.internal import git_worktree
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.paths import get_catalog_path
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment import DevelopmentEnvironment

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


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
    if explicit is not None:
        selected = Path(explicit).resolve()
        try:
            boundary: Path | None = git_worktree.rev_parse_toplevel(selected)
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
        boundary = git_worktree.rev_parse_toplevel(base)
    except Exception:
        boundary = None
    registered = _project_from_registered_worktree(base)
    if registered is not None:
        return ProjectConfig.load(registered)
    manifest = _find_nearest_manifest(base, boundary)
    if manifest is not None:
        try:
            return ProjectConfig.load(manifest.parent.parent)
        except ProjectManifestNotFoundError as e:
            raise ProjectContextError(str(e)) from e
    raise ProjectContextError(
        "No .odcli/project.toml found upward from cwd; run odcli init or pass --project PATH"
    )


def _project_from_registered_worktree(cwd: Path) -> Path | None:
    """Resolve nested registered worktrees without creating/opening a catalog."""
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
    for root, worktree, common_dir in rows:
        try:
            cwd.relative_to(Path(str(worktree)).resolve())
        except ValueError:
            continue
        try:
            if git_worktree.rev_parse_git_common_dir(git_worktree.rev_parse_toplevel(cwd)) != Path(
                str(common_dir)
            ):
                continue
        except Exception:
            continue
        return Path(str(root))
    return None


def resolve_environment(
    client: OdooClient,
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


def _list_environments(client: OdooClient) -> list[DevelopmentEnvironment]:
    try:
        return client.environments.list()
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
            if (
                git_worktree.rev_parse_git_common_dir(
                    git_worktree.rev_parse_toplevel(cwd)
                ).resolve()
                != Path(env.git_common_dir).resolve()
            ):
                continue
        except Exception:
            continue
        return env
    return None


def _check_port_free(env_obj: DevelopmentEnvironment) -> bool:
    return probe_address(env_obj.http_interface, env_obj.http_port) is AddressState.FREE


def _verify_env_runtime(env_obj: DevelopmentEnvironment) -> None:
    worktree = Path(env_obj.worktree_path)
    if not worktree.is_dir():
        raise RuntimeError(f"worktree missing: {worktree}")
    config_path = Path(env_obj.generated_config_path)
    if not config_path.is_file():
        raise RuntimeError(f"generated config missing: {config_path}")
    py_path = Path(env_obj.python_environment_path)
    if env_obj.python_environment_owned:
        if not (py_path / "bin" / "python").exists():
            raise RuntimeError(f"recorded Python missing: {py_path / 'bin' / 'python'}")
    elif not py_path.exists():
        raise RuntimeError(f"recorded Python missing: {py_path}")
