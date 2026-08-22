from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentState,
)

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


class TestEnvList:
    def test_default_hides_removed(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/list-hidden", options=opts)
        env_client.environments.remove(env)
        envs = env_client.environments.list(project=project_manifest)
        assert all(e.state != EnvironmentState.REMOVED for e in envs)

    def test_all_shows_removed(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/list-all", options=opts)
        env_client.environments.remove(env)
        envs = env_client.environments.list(project=project_manifest, include_removed=True)
        assert any(e.state == EnvironmentState.REMOVED for e in envs)

    def test_failed_visible(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/list-failed", options=opts)
        catalog = env_client.get_catalog()
        catalog.update_environment_state(str(env.id), EnvironmentState.FAILED, last_error="test")
        envs = env_client.environments.list(project=project_manifest)
        assert any(e.state == EnvironmentState.FAILED for e in envs)

    def test_reconciliation_detects_missing_worktree(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/list-recon", options=opts)
        import shutil

        shutil.rmtree(Path(env.worktree_path), ignore_errors=True)
        envs = env_client.environments.list(project=project_manifest)
        found = [e for e in envs if str(e.id) == str(env.id)]
        assert len(found) == 1
        assert not Path(found[0].worktree_path).exists()

    def test_all_projects_no_project_context(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env_client.environments.checkout(project_manifest, "feat/all-proj", options=opts)
        envs = env_client.environments.list()
        assert len(envs) >= 1
