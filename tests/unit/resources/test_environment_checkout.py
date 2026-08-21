from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from odoo_instance_sdk.exceptions import (
    ConfigError,
    EnvironmentConflictError,
    EnvironmentNotFoundError,
    InstanceConfigurationError,
)
from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentState,
)

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


class TestCheckoutPreflight:
    def test_missing_venv_interpreter_hints_create_venv(
        self, env_client: OdooClient, project_manifest: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python="/nonexistent/python")
        with pytest.raises((InstanceConfigurationError, ConfigError)):
            env_client.environments.checkout(project_manifest, "feat/x", options=opts)

    def test_active_environment_conflict(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED
        )
        env_client.environments.checkout(project_manifest, "feat/x", options=opts)
        with pytest.raises(EnvironmentConflictError):
            env_client.environments.checkout(project_manifest, "feat/x", options=opts)

    def test_multiple_db_names_requires_source_db(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, git_repo: Path
    ) -> None:
        cfg = git_repo / "odoo.conf"
        cfg.write_text(
            "[options]\ndb_name = comerta,test\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n"
        )
        manifest = project_manifest / ".odcli" / "project.toml"
        manifest.write_text(
            textwrap.dedent(f"""\
            [project]
            odoo_bin = "/usr/bin/odoo"
            python = "{fake_python}"
            source_config = "odoo.conf"
        """)
        )
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED, config_path=cfg
        )
        with pytest.raises(ConfigError):
            env_client.environments.checkout(project_manifest, "feat/multi", options=opts)

    def test_dirty_main_does_not_block(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, git_repo: Path
    ) -> None:
        (git_repo / "dirty.txt").write_text("uncommitted")
        subprocess.run(["git", "add", "dirty.txt"], cwd=git_repo, check=True, capture_output=True)
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED
        )
        env = env_client.environments.checkout(project_manifest, "feat/dirty", options=opts)
        assert env.state == EnvironmentState.READY


class TestCheckoutShared:
    def test_shared_checkout_success(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, source_config: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            config_path=source_config,
        )
        env = env_client.environments.checkout(project_manifest, "feat/shared", options=opts)
        assert env.state == EnvironmentState.READY
        assert env.db_mode == EnvironmentDatabaseMode.SHARED
        assert env.source_db_name == "comerta"
        assert env.target_db_name is None
        assert env.backup_id is None
        assert Path(env.worktree_path).is_dir()
        assert Path(env.generated_config_path).is_file()

    def test_generated_config_has_correct_db_name(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        )
        env = env_client.environments.checkout(project_manifest, "feat/cfg", options=opts)
        gen_cfg = Path(env.generated_config_path)
        content = gen_cfg.read_text()
        assert "comerta" in content
        assert f"http_port = {env.http_port}" in content

    def test_shared_remove_does_not_drop_source_db(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        )
        env = env_client.environments.checkout(project_manifest, "feat/rm", options=opts)
        env_client.environments.remove(env)
        removed = env_client.environments.get(str(env.id))
        assert removed.state == EnvironmentState.REMOVED


class TestCheckoutDryRun:
    def test_dry_run_nothing_created(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        )
        env = env_client.environments.checkout(
            project_manifest, "feat/dry", options=opts, dry_run=True
        )
        assert env.state == EnvironmentState.CREATING
        assert not Path(env.worktree_path).exists()
        assert not Path(env.generated_config_path).exists()


class TestWorktreeBranchRules:
    def test_existing_local_branch(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, git_repo: Path
    ) -> None:
        subprocess.run(
            ["git", "branch", "feat/existing"], cwd=git_repo, check=True, capture_output=True
        )
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        )
        env = env_client.environments.checkout(project_manifest, "feat/existing", options=opts)
        assert env.state == EnvironmentState.READY

    def test_absent_branch_created_from_base(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, git_repo: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        )
        env = env_client.environments.checkout(project_manifest, "feat/absent", options=opts)
        assert env.state == EnvironmentState.READY
        result = subprocess.run(
            ["git", "-C", str(git_repo), "branch", "--list", "feat/absent"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "feat/absent" in result.stdout


class TestGetAndList:
    def test_get_by_id(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/get", options=opts)
        fetched = env_client.environments.get(str(env.id))
        assert fetched.id == env.id

    def test_get_not_found(self, env_client: OdooClient) -> None:
        with pytest.raises(EnvironmentNotFoundError):
            env_client.environments.get("nonexistent-id")

    def test_list_hides_removed(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/list1", options=opts)
        env_client.environments.remove(env)
        envs = env_client.environments.list(project=project_manifest)
        assert all(e.state != EnvironmentState.REMOVED for e in envs)
        envs_all = env_client.environments.list(project=project_manifest, include_removed=True)
        assert any(e.state == EnvironmentState.REMOVED for e in envs_all)
