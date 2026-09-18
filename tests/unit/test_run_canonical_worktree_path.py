from __future__ import annotations

import json
import os
import socket
import textwrap
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import msgspec
import platformdirs
import psutil
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.paths import (
    EnvironmentArtifactPaths,
    resolve_environment_artifact_paths,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.resources.environment import EnvironmentDatabaseMode, EnvironmentState

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


def _legacy_macos_data_root(home: Path) -> Path:
    return Path(platformdirs.user_data_dir("odoo-instance-sdk", ensure_exists=False))


def _seed_environment(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    *,
    branch: str,
    http_port: int,
    legacy_paths: bool,
) -> tuple[str, EnvironmentArtifactPaths]:
    repo_root = project_manifest.parent.parent
    git_common = repo_root / ".git"
    env_id = uuid.uuid4()
    canonical = resolve_environment_artifact_paths(
        environment_id=str(env_id),
        repository_root=repo_root,
        git_common_dir=git_common,
        python_environment_owned=False,
        python_environment_path=str(fake_python),
    )
    worktree = canonical.worktree_path
    worktree.mkdir(parents=True, exist_ok=True)
    addons_dir = worktree / "addons"
    addons_dir.mkdir(exist_ok=True)
    canonical.generated_config_path.write_text(
        textwrap.dedent(
            f"""\
            [options]
            http_interface = 127.0.0.1
            http_port = {http_port}
            db_name = comerta
            addons_path = {addons_dir}
            """
        ),
        encoding="utf-8",
    )
    canonical.dependency_lock_path.write_text("", encoding="utf-8")
    odoo_bin = project_manifest.parent / "odoo-bin"
    stored_worktree = str(worktree)
    stored_config = str(canonical.generated_config_path)
    stored_lock = str(canonical.dependency_lock_path)
    if legacy_paths:
        legacy_root = _legacy_macos_data_root(Path.home())
        legacy_env_root = (
            legacy_root / "environments" / repo_key(repo_root, git_common) / str(env_id)
        )
        stored_worktree = str(legacy_env_root / "worktree")
        stored_config = str(legacy_env_root / "odoo.conf")
        stored_lock = str(legacy_env_root / "requirements.lock")
        assert not legacy_root.exists()
    catalog = env_client.get_catalog()
    catalog.create_environment(
        {
            "id": str(env_id),
            "name": f"demo:{branch}",
            "repository_root": str(repo_root),
            "git_common_dir": str(git_common),
            "branch": branch,
            "base_ref": "main",
            "worktree_path": stored_worktree,
            "generated_config_path": stored_config,
            "python_environment_path": str(fake_python),
            "python_environment_owned": False,
            "dependency_lock_path": stored_lock,
            "http_interface": "127.0.0.1",
            "http_port": http_port,
            "db_mode": EnvironmentDatabaseMode.SHARED.value,
            "source_db_name": "comerta",
            "target_db_name": None,
            "backup_id": None,
            "runtime_json": json.dumps({"odoo_bin": str(odoo_bin), "runtime_cwd": stored_worktree}),
            "state": EnvironmentState.READY.value,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return str(env_id), canonical


@pytest.fixture(autouse=True)
def _deterministic_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.port_allocation.find_free_port",
        lambda _kind, _catalog, *, requested=None, **_kwargs: requested or 18071,
    )


def test_from_environment_resolves_legacy_catalog_paths(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
) -> None:
    env_id, canonical = _seed_environment(
        env_client,
        project_manifest,
        fake_python,
        branch="feat/canonical-core",
        http_port=18070,
        legacy_paths=True,
    )
    env = env_client.environments.get(env_id)
    assert env.worktree_path == str(canonical.worktree_path)
    instance = env_client.instance.from_environment(env)
    assert instance.config.default_cwd == canonical.worktree_path
    command = instance.run_foreground_command()
    plan = json.dumps(msgspec.to_builtins(command.plan), ensure_ascii=False)
    assert str(canonical.worktree_path) in plan
    assert "Application Support/odoo-instance-sdk" not in plan


def test_run_dry_run_uses_canonical_worktree_without_legacy_symlink(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
) -> None:
    env_id, canonical = _seed_environment(
        env_client,
        project_manifest,
        fake_python,
        branch="feat/canonical-run",
        http_port=18071,
        legacy_paths=True,
    )

    with patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client):
        result = CliRunner().invoke(
            cli,
            [
                "--project",
                str(project_manifest.parent.parent),
                "--env",
                env_id,
                "run",
                "--dry-run",
                "--format",
                "json",
                "--",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    plan = json.dumps(payload["result"], ensure_ascii=False)
    assert str(canonical.worktree_path) in plan
    assert str(canonical.generated_config_path) in plan
    assert "Application Support/odoo-instance-sdk" not in plan

    git_steps = [
        step
        for step in payload["result"]["steps"]
        if step["step_id"].startswith("instance.foreground.git.")
    ]
    assert git_steps
    assert all(str(canonical.worktree_path) in json.dumps(step) for step in git_steps)

    argv = next(
        step["argv"]
        for step in payload["result"]["steps"]
        if step["step_id"] == "instance.foreground"
    )
    addons_index = argv.index("--addons-path")
    assert str(canonical.worktree_path) in argv[addons_index + 1]


def test_run_port_preflight_recognizes_persisted_environment_runtime(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
) -> None:
    env_id, _canonical = _seed_environment(
        env_client,
        project_manifest,
        fake_python,
        branch="feat/canonical-port",
        http_port=18072,
        legacy_paths=False,
    )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 18072))
        catalog = env_client.get_catalog()
        catalog._upsert_runtime(
            "environment",
            env_id,
            root_pid=os.getpid(),
            create_time=psutil.Process(os.getpid()).create_time(),
            started_at="2026-01-01T00:00:00+00:00",
            checkout_branch="feat/canonical-port",
            commit_sha="abc1234",
            http_url="http://127.0.0.1:18072",
            http_port=18072,
            database_name="comerta",
        )

        with patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client):
            result = CliRunner().invoke(
                cli,
                [
                    "--project",
                    str(project_manifest.parent.parent),
                    "--env",
                    env_id,
                    "run",
                    "--dry-run",
                    "--format",
                    "json",
                    "--",
                ],
                catch_exceptions=False,
            )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    observation = payload["result"]["observations"][0]["preconditions"][0]
    assert observation["name"] == "http-port-free"
    assert observation["status"] == "passed"
    assert "persisted environment runtime" in observation["detail"]
    assert "ownership unknown" not in observation["detail"]


def test_env_list_and_run_agree_on_canonical_worktree_path(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
) -> None:
    env_id, canonical = _seed_environment(
        env_client,
        project_manifest,
        fake_python,
        branch="feat/canonical-list",
        http_port=18073,
        legacy_paths=True,
    )

    with patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client):
        list_result = CliRunner().invoke(
            cli,
            [
                "--project",
                str(project_manifest.parent.parent),
                "env",
                "list",
                "--format",
                "json",
            ],
            catch_exceptions=False,
        )
        run_result = CliRunner().invoke(
            cli,
            [
                "--project",
                str(project_manifest.parent.parent),
                "--env",
                env_id,
                "run",
                "--dry-run",
                "--format",
                "json",
                "--",
            ],
            catch_exceptions=False,
        )

    assert list_result.exit_code == 0, list_result.output
    assert run_result.exit_code == 0, run_result.output
    listed = json.loads(list_result.stdout)
    run_payload = json.loads(run_result.stdout)
    env_rows = [row for row in listed["result"]["rows"] if row.get("environment_id") == env_id]
    assert len(env_rows) == 1
    assert env_rows[0]["worktree_path"] == str(canonical.worktree_path)
    assert str(canonical.worktree_path) in json.dumps(run_payload["result"])


def test_legacy_path_is_only_used_in_storage_migration_module() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source_root = repo_root / "src" / "odoo_instance_sdk"
    allowed = {source_root / "internal" / "storage_migration.py"}
    needles = (
        "Application Support/odoo-instance-sdk",
        'user_data_dir("odoo-instance-sdk")',
        "user_data_dir('odoo-instance-sdk')",
    )
    offenders: list[str] = []
    for path in source_root.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                offenders.append(f"{path.relative_to(repo_root)}:{needle}")
    assert offenders == []
