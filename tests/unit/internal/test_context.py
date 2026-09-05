from __future__ import annotations

import re
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.commands.context import (
    CliContext,
    ResolvedContext,
    RuntimeView,
    environment_provenance,
    project_provenance,
    ready_instance,
    resolve_project_path,
)
from odoo_instance_sdk.commands.context import (
    resolve_environment as resolve_cli_environment,
)
from odoo_instance_sdk.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentResolutionError,
    ProjectContextError,
)
from odoo_instance_sdk.internal.context import resolve_project
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentState,
)
from odoo_instance_sdk.resources.instance import OdooInstance


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


def test_runtime_view_keeps_environment_owner_and_lifecycle_narrowing(tmp_path: Path) -> None:
    env = _make_env(worktree=str(tmp_path), generated_config_path=str(tmp_path / "odoo.conf"))
    instance = SimpleNamespace(
        config=SimpleNamespace(
            start_config=StartConfig(db_name="project_db", http_port=18069),
            command_prefix=(sys.executable, str(tmp_path / "odoo-bin")),
        )
    )
    resolved = ResolvedContext(
        client=MagicMock(),
        instance=cast("OdooInstance", instance),
        source=env,
        provenance="worktree",
    )

    view = resolved.runtime

    assert isinstance(view, RuntimeView)
    assert view.owner_kind == "environment"
    assert view.environment_id == str(env.id)
    assert view.environment_name == env.name
    assert view.root == tmp_path
    assert view.database == "project_db"
    assert view.http_url == "http://127.0.0.1:18069"
    assert view.command_prefix == (sys.executable, str(tmp_path / "odoo-bin"))
    assert resolved.require_environment() is env


def test_runtime_view_exposes_project_owner_without_synthetic_environment(tmp_path: Path) -> None:
    project = ProjectConfig(
        repository_root=tmp_path,
        python=Path(sys.executable),
        odoo_bin=Path(sys.executable),
    )
    instance = SimpleNamespace(
        config=SimpleNamespace(
            start_config=StartConfig(db_name="project_db", http_port=18070),
            command_prefix=(sys.executable, str(tmp_path / "odoo-bin")),
        )
    )
    resolved = ResolvedContext(
        client=MagicMock(),
        instance=cast("OdooInstance", instance),
        source=project,
        provenance="cwd",
    )

    view = resolved.runtime

    assert view.owner_kind == "project"
    assert view.project_id.startswith(f"project_{tmp_path.name}_")
    assert view.environment_id is None
    assert view.environment_name is None
    assert view.repository_root == tmp_path
    assert view.root == tmp_path
    assert view.base_provenance == "project"
    with pytest.raises(EnvironmentResolutionError, match="requires a development environment"):
        resolved.require_environment()


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


def test_resolve_project_prefers_registered_worktree_over_nearest_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nearest = tmp_path / "nearest"
    nearest_manifest_dir = nearest / ".odcli"
    nearest_manifest_dir.mkdir(parents=True)
    (nearest_manifest_dir / "project.toml").write_text("[project]\n")
    registered = tmp_path / "registered"
    registered_manifest_dir = registered / ".odcli"
    registered_manifest_dir.mkdir(parents=True)
    (registered_manifest_dir / "project.toml").write_text("[project]\n")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context._project_from_registered_worktree",
        lambda _cwd: registered,
    )

    resolved = resolve_project(None, cwd=nearest)

    assert isinstance(resolved, ProjectConfig)
    assert resolved.repository_root == registered.resolve()


def test_resolve_project_no_manifest_errors(tmp_path: Path) -> None:
    from odoo_instance_sdk.exceptions import ProjectContextError

    with pytest.raises(ProjectContextError):
        resolve_project(None, cwd=tmp_path)


def test_cli_context_records_explicit_project_resolution(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text("[project]\n")
    context = CliContext(project=str(tmp_path), env="selected")

    resolved = resolve_project_path(context)

    assert resolved == tmp_path.resolve()
    assert context.project == str(tmp_path)
    assert project_provenance(context) == "explicit"


def test_cli_context_records_cwd_project_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text("[project]\n")
    nested = tmp_path / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    context = CliContext()

    resolved = resolve_project_path(context)

    assert resolved == tmp_path.resolve()
    assert context.project is None
    assert project_provenance(context) == "cwd"


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


def test_cli_context_records_explicit_environment_resolution() -> None:
    env = _make_env(name="my-env")
    client = MagicMock()
    client.environments.list.return_value = [env]
    context = CliContext(env=env.name)

    result = resolve_cli_environment(client, env.name)

    assert result is env
    assert context.env == env.name
    assert environment_provenance(context) == "explicit"


def test_cli_context_records_cwd_environment_resolution(
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
    context = CliContext()

    result = resolve_cli_environment(client, None, cwd=worktree)

    assert result is env
    assert context.env is None
    assert environment_provenance(context) == "cwd"


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


def test_ready_instance_reads_selector_from_typed_context(
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
    ctx = CliContext(env=env.name, project="/repo")
    monkeypatch.setattr("odoo_instance_sdk.commands.context.OdooClient", lambda **_kwargs: client)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.resolve_environment",
        lambda _client, selector, **_kwargs: env if selector == env.name else None,
    )
    resolved = ready_instance(ctx)

    assert resolved.client is client
    assert resolved.source is env
    assert resolved.instance is instance
    assert resolved.provenance == "explicit"
    client.instance.from_environment.assert_called_once_with(env)


def test_ready_instance_falls_back_to_nearest_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = ProjectConfig(repository_root=tmp_path)
    client = MagicMock()
    instance = MagicMock()
    client.instance.from_project.return_value = instance
    ctx = CliContext()
    monkeypatch.setattr("odoo_instance_sdk.commands.context.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.resolve_environment",
        MagicMock(side_effect=EnvironmentResolutionError("no worktree")),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.resolve_project", lambda *_a, **_k: project
    )

    resolved = ready_instance(ctx)

    assert resolved.client is client
    assert resolved.source is project
    assert resolved.instance is instance
    assert resolved.provenance == "cwd"
    client.instance.from_project.assert_called_once_with(project)


def test_ready_instance_invalid_explicit_environment_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    project_resolver = MagicMock()
    ctx = CliContext(env="missing")
    monkeypatch.setattr("odoo_instance_sdk.commands.context.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.resolve_environment",
        MagicMock(side_effect=EnvironmentNotFoundError("missing")),
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.context.resolve_project", project_resolver)

    with pytest.raises(EnvironmentNotFoundError):
        ready_instance(ctx)

    project_resolver.assert_not_called()
    client.instance.from_project.assert_not_called()


def test_project_context_accessors_do_not_create_environment(tmp_path: Path) -> None:
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=Path(".venv/bin/python"),
    )
    context = ResolvedContext(
        client=MagicMock(),
        instance=MagicMock(),
        source=project,
        provenance="explicit",
    )

    assert context.worktree_path() == tmp_path
    assert context.python_path() == python
    with pytest.raises(EnvironmentResolutionError, match="requires a development environment"):
        context.require_environment()


def test_project_python_selector_is_resolved_through_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved_python = tmp_path / "resolved-python3"
    resolved_python.write_text("#!/bin/sh\n")
    resolved_python.chmod(0o755)
    project = ProjectConfig(repository_root=tmp_path, python="python3")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_runtime.shutil.which",
        lambda value: str(resolved_python) if value == "python3" else None,
    )
    context = ResolvedContext(
        client=MagicMock(),
        instance=MagicMock(),
        source=project,
        provenance="explicit",
    )

    assert context.python_path() == resolved_python


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
def test_project_python_uv_selector_uses_real_uv_resolution(tmp_path: Path) -> None:
    project = ProjectConfig(repository_root=tmp_path, python="3.12")
    context = ResolvedContext(
        client=MagicMock(),
        instance=MagicMock(),
        source=project,
        provenance="explicit",
    )

    resolved = context.python_path()

    assert resolved.is_absolute()
    assert resolved.is_file()


def test_ready_instance_explicit_env_ignores_malformed_cwd_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text("[project\n")
    monkeypatch.chdir(tmp_path)
    env = _make_env()
    client = MagicMock()
    instance = MagicMock()
    client.instance.from_environment.return_value = instance
    ctx = CliContext(env=env.name)
    monkeypatch.setattr("odoo_instance_sdk.commands.context.OdooClient", lambda **_kwargs: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.resolve_environment",
        lambda _client, selector, **_kwargs: env if selector == env.name else None,
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.context._verify_env_runtime", lambda _env: None)

    ready_instance(ctx)

    assert ctx.project is None
    assert ctx.env == env.name
    assert environment_provenance(ctx) == "explicit"


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
    ctx = CliContext(env=env.name, project="/repo")
    monkeypatch.setattr("odoo_instance_sdk.commands.context.OdooClient", lambda **_kwargs: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.context.resolve_project_path", lambda _ctx: Path("/repo")
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.resolve_environment",
        lambda _client, selector, **_kwargs: env if selector == env.name else None,
    )
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


def test_ready_instance_exact_worktree_wins_over_foreign_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    env = _make_env(worktree=str(worktree))
    client = MagicMock()
    client.environments.list.return_value = [env]
    ctx = CliContext(project="/selected")
    monkeypatch.setattr("odoo_instance_sdk.commands.context.OdooClient", lambda **_kwargs: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.rev_parse_toplevel", lambda _path: worktree
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.rev_parse_git_common_dir",
        lambda _path: Path(env.git_common_dir),
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.context._verify_env_runtime", lambda _env: None)
    monkeypatch.chdir(worktree)

    resolved = ready_instance(ctx)

    assert resolved.source is env
    assert resolved.provenance == "worktree"
    client.instance.from_environment.assert_called_once_with(env)


def test_ready_instance_rejects_missing_explicit_project(tmp_path: Path) -> None:
    ctx = CliContext(project=str(tmp_path / "missing"))
    with pytest.raises(ProjectContextError, match="Explicit --project"):
        ready_instance(ctx)


def test_ready_instance_rejects_unknown_environment_for_explicit_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    ctx = CliContext(project="/selected", env="unknown")
    monkeypatch.setattr("odoo_instance_sdk.commands.context.OdooClient", lambda **_kwargs: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.context.resolve_project_path", lambda _ctx: Path("/selected")
    )

    def raise_unknown(*_args: object, **_kwargs: object) -> DevelopmentEnvironment:
        raise EnvironmentNotFoundError("unknown")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.resolve_environment",
        raise_unknown,
    )

    with pytest.raises(EnvironmentNotFoundError, match="unknown"):
        ready_instance(ctx)
    client.instance.from_environment.assert_not_called()
