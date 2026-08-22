from __future__ import annotations

import re
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
    generated_config_path: str = "/wt/odoo.conf",
    python_environment_path: str = "/venv",
    python_environment_owned: bool = False,
) -> DevelopmentEnvironment:
    return DevelopmentEnvironment(
        id=env_id or uuid.uuid4(),
        name=name,
        repository_root="/repo",
        git_common_dir="/repo/.git",
        branch="main",
        base_ref="HEAD",
        worktree_path=worktree,
        generated_config_path=generated_config_path,
        python_environment_path=python_environment_path,
        python_environment_owned=python_environment_owned,
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


def test_resolve_project_explicit_nested_path(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text("[project]\nodoo_bin = '/opt/odoo'\n")
    nested = tmp_path / "nested" / "path"
    nested.mkdir(parents=True)
    cfg = resolve_project(nested, cwd=tmp_path)
    assert isinstance(cfg, ProjectConfig)
    assert cfg.repository_root == tmp_path


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


def test_resolve_environment_infers_from_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    env = _make_env(name="inferred", worktree=str(worktree))
    client = MagicMock()
    client.environments.list.return_value = [env]
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.rev_parse_toplevel", lambda _path: worktree
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.rev_parse_git_common_dir",
        lambda _path: Path(env.git_common_dir),
    )
    from odoo_instance_sdk.internal.context import resolve_environment

    result = resolve_environment(client, None, cwd=worktree)
    assert result.name == "inferred"


def test_ready_instance_reads_selector_from_click_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    config = worktree / "odoo.conf"
    config.write_text("[options]\n")
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    env = _make_env(
        name="ready",
        worktree=str(worktree),
        generated_config_path=str(config),
        python_environment_path=str(python),
    )
    client = MagicMock()
    instance = MagicMock()
    client.instance.from_environment.return_value = instance
    ctx = MagicMock()
    ctx.obj = {"env": env.name}
    monkeypatch.setattr("odoo_instance_sdk.internal.context.OdooClient", lambda **_kwargs: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.resolve_environment",
        lambda _client, selector: env if selector == env.name else None,
    )
    from odoo_instance_sdk.internal.context import ready_instance

    result_client, result_env, result_instance = ready_instance(ctx)

    assert result_client is client
    assert result_env is env
    assert result_instance is instance
    client.instance.from_environment.assert_called_once_with(env)


@pytest.mark.parametrize(
    (
        "worktree_exists",
        "config_exists",
        "python_owned",
        "python_exists",
        "expected_error",
    ),
    [
        (False, False, False, False, "worktree missing: {worktree}"),
        (True, False, False, False, "generated config missing: {config}"),
        (True, True, True, True, None),
        (True, True, True, False, "recorded Python missing: {python}/bin/python"),
        (True, True, False, False, "recorded Python missing: {python}"),
    ],
    ids=[
        "missing-worktree",
        "missing-config",
        "owned-python-present",
        "owned-python-missing",
        "reused-python-missing",
    ],
)
def test_ready_instance_validates_recorded_runtime_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worktree_exists: bool,
    config_exists: bool,
    python_owned: bool,
    python_exists: bool,
    expected_error: str | None,
) -> None:
    worktree = tmp_path / "worktree"
    config = worktree / "odoo.conf"
    python = tmp_path / "python"
    if worktree_exists:
        worktree.mkdir()
    if config_exists:
        config.write_text("[options]\n")
    python_executable = python / "bin" / "python" if python_owned else python
    if python_exists:
        python_executable.parent.mkdir(parents=True, exist_ok=True)
        python_executable.write_text("#!/bin/sh\n")
    env = _make_env(
        worktree=str(worktree),
        generated_config_path=str(config),
        python_environment_path=str(python),
        python_environment_owned=python_owned,
    )
    client = MagicMock()
    ctx = MagicMock()
    ctx.obj = {"env": env.name}
    monkeypatch.setattr("odoo_instance_sdk.internal.context.OdooClient", lambda **_kwargs: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.resolve_environment",
        lambda _client, selector: env if selector == env.name else None,
    )
    from odoo_instance_sdk.internal.context import ready_instance

    if expected_error is None:
        ready_instance(ctx)
        client.instance.from_environment.assert_called_once_with(env)
    else:
        with pytest.raises(
            RuntimeError,
            match=re.escape(expected_error.format(worktree=worktree, config=config, python=python)),
        ):
            ready_instance(ctx)
        client.instance.from_environment.assert_not_called()
