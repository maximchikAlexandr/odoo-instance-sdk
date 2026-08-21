from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.exceptions import EnvironmentNotFoundError, EnvironmentResolutionError
from odoo_instance_sdk.internal.context import resolve_project
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentState,
)


def _make_env(
    *,
    env_id: uuid.UUID | None = None,
    name: str = "env-1",
    worktree: str = "/tmp/wt-1",
) -> DevelopmentEnvironment:
    return DevelopmentEnvironment(
        id=env_id or uuid.uuid4(),
        name=name,
        repository_root="/repo",
        git_common_dir="/repo/.git",
        branch="main",
        base_ref="HEAD",
        worktree_path=worktree,
        generated_config_path="/wt/odoo.conf",
        python_environment_path="/venv",
        python_environment_owned=False,
        dependency_lock_path="/lock",
        http_interface="127.0.0.1",
        http_port=8069,
        db_mode=EnvironmentDatabaseMode.SHARED,
        state=EnvironmentState.READY,
        created_at=datetime.now(UTC),
    )


def test_resolve_project_explicit(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text("[project]\nodoo_bin = '/opt/odoo'\n")
    cfg = resolve_project(tmp_path, cwd=tmp_path)
    assert isinstance(cfg, ProjectConfig)
    assert cfg.odoo_bin == Path("/opt/odoo")


def test_resolve_project_nearest_manifest(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text("[project]\n")
    subdir = tmp_path / "sub" / "deep"
    subdir.mkdir(parents=True)
    cfg = resolve_project(None, cwd=subdir)
    assert isinstance(cfg, ProjectConfig)


def test_resolve_project_no_manifest_errors(tmp_path: Path) -> None:
    from odoo_instance_sdk.exceptions import ProjectContextError

    with pytest.raises(ProjectContextError):
        resolve_project(None, cwd=tmp_path)


def test_resolve_environment_explicit_uuid() -> None:
    env_id = uuid.uuid4()
    env = _make_env(env_id=env_id)
    client = MagicMock()
    client.environments.list.return_value = [env]
    from odoo_instance_sdk.internal.context import resolve_environment

    result = resolve_environment(client, str(env_id))
    assert result.id == env_id


def test_resolve_environment_explicit_name() -> None:
    env = _make_env(name="my-env")
    client = MagicMock()
    client.environments.list.return_value = [env]
    from odoo_instance_sdk.internal.context import resolve_environment

    result = resolve_environment(client, "my-env")
    assert result.name == "my-env"


def test_resolve_environment_not_found() -> None:
    client = MagicMock()
    client.environments.list.return_value = []
    from odoo_instance_sdk.internal.context import resolve_environment

    with pytest.raises(EnvironmentNotFoundError):
        resolve_environment(client, "nonexistent")


def test_resolve_environment_ambiguous_name_errors() -> None:
    env1 = _make_env(name="feat", worktree="/wt-1")
    env2 = _make_env(name="feat", worktree="/wt-2")
    client = MagicMock()
    client.environments.list.return_value = [env1, env2]
    from odoo_instance_sdk.internal.context import resolve_environment

    with pytest.raises(EnvironmentResolutionError, match="Ambiguous"):
        resolve_environment(client, "feat")


def test_resolve_environment_single_ready_not_silently_selected() -> None:
    env = _make_env(name="only")
    client = MagicMock()
    client.environments.list.return_value = [env]
    from odoo_instance_sdk.internal.context import resolve_environment

    with pytest.raises(EnvironmentResolutionError):
        resolve_environment(client, None, cwd=Path("/not-in-worktree"))


def test_resolve_environment_infers_from_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    env = _make_env(name="inferred", worktree=str(worktree))
    client = MagicMock()
    client.environments.list.return_value = [env]
    from odoo_instance_sdk.internal.context import resolve_environment

    result = resolve_environment(client, None, cwd=worktree)
    assert result.name == "inferred"
