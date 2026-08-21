from __future__ import annotations

from pathlib import Path

from odoo_instance_sdk.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentResolutionError,
    ProjectContextError,
    ProjectManifestNotFoundError,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment import DevelopmentEnvironment


def _find_nearest_manifest(start: Path) -> Path | None:
    current = start.resolve()
    if not current.is_dir():
        current = current.parent
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        candidate = current / ".odcli" / "project.toml"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def resolve_project(explicit: Path | None, cwd: Path | None = None) -> ProjectConfig | Path:
    base = (cwd or Path.cwd()).resolve()
    if explicit is not None:
        project_root = Path(explicit).resolve()
        if (project_root / ".odcli" / "project.toml").is_file():
            try:
                return ProjectConfig.load(project_root)
            except ProjectManifestNotFoundError as e:
                raise ProjectContextError(str(e)) from e
        raise ProjectContextError(
            f"Explicit --project {explicit} has no .odcli/project.toml; run odcli init"
        )
    manifest = _find_nearest_manifest(base)
    if manifest is not None:
        try:
            return ProjectConfig.load(manifest.parent.parent)
        except ProjectManifestNotFoundError as e:
            raise ProjectContextError(str(e)) from e
    raise ProjectContextError(
        "No .odcli/project.toml found upward from cwd; run odcli init or pass --project PATH"
    )


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
        return env
    return None
