from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from odoo_instance_sdk.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentResolutionError,
    ProjectContextError,
    ProjectManifestNotFoundError,
)
from odoo_instance_sdk.internal import git_worktree
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.paths import (
    get_catalog_path,
    resolve_environment_artifact_paths,
)
from odoo_instance_sdk.internal.repo_key import parse_git_common_dir
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


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    """Filesystem/catalog facts captured without crossing a process boundary."""

    project: ProjectConfig
    nearest_repository_root: Path | None
    git_common_dir: Path | None
    runtime_trusted: bool = False


def _nearest_git_marker(cwd: Path) -> tuple[Path | None, Path | None]:
    current = cwd.resolve()
    if not current.is_dir():
        current = current.parent
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        marker = current / ".git"
        try:
            present = os.path.lexists(marker)
        except OSError:
            present = False
        if present:
            return current, parse_git_common_dir(current)
        if current.parent == current:
            break
        current = current.parent
    return None, None


def _catalog_project_root(  # noqa: C901
    cwd: Path,
    boundary: Path | None,
    common_dir: Path | None,
) -> Path | None:
    if boundary is None or common_dir is None:
        return None
    catalog = get_catalog_path(ensure_exists=False)
    if not catalog.is_file():
        return None
    matches: set[Path] = set()
    try:
        from urllib.parse import quote

        with sqlite3.connect(
            f"file:{quote(str(catalog.resolve()), safe='/')}?mode=ro", uri=True
        ) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT repository_root, git_common_dir FROM projects"
                ).fetchall()
            except sqlite3.Error:
                rows = []
            for row in rows:
                try:
                    root = Path(str(row["repository_root"])).resolve()
                    stored_common = Path(str(row["git_common_dir"])).resolve()
                except (KeyError, OSError, RuntimeError):
                    continue
                if root == boundary and stored_common == common_dir:
                    matches.add(root)
            try:
                rows = conn.execute(
                    "SELECT repository_root, worktree_path, git_common_dir "
                    "FROM environments WHERE state <> 'removed'"
                ).fetchall()
            except sqlite3.Error:
                rows = []
            for row in rows:
                try:
                    root = Path(str(row["repository_root"])).resolve()
                    worktree = Path(str(row["worktree_path"])).resolve()
                    stored_common = Path(str(row["git_common_dir"])).resolve()
                except (KeyError, OSError, RuntimeError):
                    continue
                if worktree == boundary and stored_common == common_dir:
                    matches.add(root)
    except (OSError, sqlite3.Error):
        return None
    if len(matches) != 1:
        return None
    try:
        cwd.relative_to(boundary)
        return next(iter(matches))
    except (ValueError, RuntimeError):
        return None


def resolve_project_snapshot(cwd: Path | None = None) -> ProjectSnapshot | None:
    """Capture the current project using only bounded local reads."""
    base = (cwd or Path.cwd()).resolve()
    boundary, common_dir = _nearest_git_marker(base)
    catalog_root = _catalog_project_root(base, boundary, common_dir)
    if catalog_root is not None:
        try:
            return ProjectSnapshot(
                project=ProjectConfig.load(catalog_root),
                nearest_repository_root=boundary,
                git_common_dir=common_dir,
                runtime_trusted=True,
            )
        except Exception:
            pass
    manifest = _find_nearest_manifest(base, boundary)
    if manifest is None:
        return None
    try:
        project = ProjectConfig.load(manifest.parent.parent)
    except Exception:
        return None
    return ProjectSnapshot(
        project=project,
        nearest_repository_root=boundary,
        git_common_dir=common_dir,
    )


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
    catalog = get_catalog_path(ensure_exists=False)
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
    raw_environments = _list_environments(client)
    if explicit is not None:
        return _resolve_explicit(
            explicit,
            [_canonical_environment(env) for env in raw_environments],
        )
    env = _infer_from_worktree(base, raw_environments)
    if env is not None:
        return _canonical_environment(env)
    candidates = [f"{e.name} ({e.id})" for e in raw_environments]
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


def _persisted_environment_runtime_owner(
    client: OdooClient,
    environment_id: str,
    http_port: int,
) -> int | None:
    """Return the persisted root PID when it still owns the reserved HTTP port."""
    runtime_row = client.get_catalog().get_environment_runtime(environment_id)
    if runtime_row is None:
        return None
    try:
        root_pid = int(str(runtime_row["root_pid"]))
        create_time = float(str(runtime_row["create_time"]))
        recorded_port = int(str(runtime_row["http_port"]))
    except (KeyError, TypeError, ValueError):
        return None
    if recorded_port != http_port:
        return None
    try:
        process = psutil.Process(root_pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None
    try:
        if float(process.create_time()) != create_time:
            return None
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None
    return root_pid


def _environment_http_port_preflight(
    env_obj: DevelopmentEnvironment,
    client: OdooClient,
) -> tuple[bool, str]:
    """Return spawn availability and a sanitized detail for the reserved HTTP port."""
    endpoint = f"{env_obj.http_interface}:{env_obj.http_port}"
    try:
        state = probe_address(env_obj.http_interface, env_obj.http_port)
    except OSError as error:
        return False, f"unable to inspect {endpoint}: {error}"
    if state is AddressState.FREE:
        return True, f"{endpoint} is available"
    owner = _persisted_environment_runtime_owner(client, str(env_obj.id), env_obj.http_port)
    if owner is not None:
        return True, f"{endpoint} is occupied by persisted environment runtime (pid={owner})"
    return False, f"{endpoint} is occupied (ownership unknown)"


def _canonical_environment(env_obj: DevelopmentEnvironment) -> DevelopmentEnvironment:
    """Project catalogue-backed paths onto the canonical ``~/.odcli`` layout."""
    artifacts = resolve_environment_artifact_paths(
        environment_id=str(env_obj.id),
        repository_root=env_obj.repository_root,
        git_common_dir=env_obj.git_common_dir,
        python_environment_owned=env_obj.python_environment_owned,
        python_environment_path=env_obj.python_environment_path,
    )
    original_worktree = Path(env_obj.worktree_path)
    try:
        original_worktree_exists = original_worktree.is_dir()
    except OSError:
        original_worktree_exists = False
    if original_worktree_exists and not artifacts.worktree_path.is_dir():
        return env_obj
    if (
        env_obj.worktree_path == str(artifacts.worktree_path)
        and env_obj.generated_config_path == str(artifacts.generated_config_path)
        and env_obj.dependency_lock_path == str(artifacts.dependency_lock_path)
        and env_obj.python_environment_path == str(artifacts.python_environment_path)
    ):
        return env_obj
    from msgspec.structs import replace

    return replace(
        env_obj,
        worktree_path=str(artifacts.worktree_path),
        generated_config_path=str(artifacts.generated_config_path),
        dependency_lock_path=str(artifacts.dependency_lock_path),
        python_environment_path=str(artifacts.python_environment_path),
    )


def _verify_env_runtime(env_obj: DevelopmentEnvironment) -> None:
    env_obj = _canonical_environment(env_obj)
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
