from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from odoo_instance_sdk.exceptions import EnvironmentConflictError
from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentState,
)

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


class TestEnvRemove:
    def test_dirty_worktree_blocks(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, git_repo: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/rm-dirty", options=opts)
        worktree = Path(env.worktree_path)
        (worktree / "dirty.txt").write_text("uncommitted")
        subprocess.run(["git", "add", "dirty.txt"], cwd=worktree, check=True, capture_output=True)
        with pytest.raises(EnvironmentConflictError):
            env_client.environments.remove(env)
        updated = env_client.environments.get(str(env.id))
        assert updated.state == EnvironmentState.CLEANUP_FAILED

    def test_shared_source_db_never_dropped(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED
        )
        env = env_client.environments.checkout(project_manifest, "feat/rm-shared", options=opts)
        env_client.environments.remove(env)
        removed = env_client.environments.get(str(env.id))
        assert removed.state == EnvironmentState.REMOVED

    def test_idempotent_missing_artifact(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/rm-idem", options=opts)
        import shutil

        shutil.rmtree(Path(env.worktree_path), ignore_errors=True)
        env_client.environments.remove(env)
        removed = env_client.environments.get(str(env.id))
        assert removed.state == EnvironmentState.REMOVED

    def test_remove_deletes_generated_config(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/rm-cfg", options=opts)
        cfg_path = Path(env.generated_config_path)
        assert cfg_path.is_file()
        env_client.environments.remove(env)
        assert not cfg_path.exists()

    def test_audit_rows_kept(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/rm-audit", options=opts)
        env_client.environments.remove(env)
        catalog = env_client.get_catalog()
        row = catalog.get_environment(str(env.id))
        assert row is not None
        assert row["state"] == EnvironmentState.REMOVED.value
