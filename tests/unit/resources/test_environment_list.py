from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentResource,
    EnvironmentState,
)

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


class TestEnvList:
    def test_default_hides_removed(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta")
        env = env_client.environments.checkout(project_manifest, "feat/list-hidden", options=opts)
        env_client.environments.remove(env)
        envs = env_client.environments.list(project=project_manifest)
        assert all(e.state != EnvironmentState.REMOVED for e in envs)

    def test_all_shows_removed(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta")
        env = env_client.environments.checkout(project_manifest, "feat/list-all", options=opts)
        env_client.environments.remove(env)
        envs = env_client.environments.list(project=project_manifest, include_removed=True)
        assert any(e.state == EnvironmentState.REMOVED for e in envs)

    def test_failed_visible(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta")
        env = env_client.environments.checkout(project_manifest, "feat/list-failed", options=opts)
        catalog = env_client.get_catalog()
        catalog.update_environment_state(str(env.id), EnvironmentState.FAILED, last_error="test")
        envs = env_client.environments.list(project=project_manifest)
        assert any(e.state == EnvironmentState.FAILED for e in envs)

    def test_reconciliation_detects_missing_worktree(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta")
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
        opts = EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta")
        env_client.environments.checkout(project_manifest, "feat/all-proj", options=opts)
        envs = env_client.environments.list()
        assert len(envs) >= 1

    def test_list_command_consumes_captured_git_identity_once(self, tmp_path: Path) -> None:
        from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RecordingExecutor

        client = MagicMock()
        catalog = client.get_catalog.return_value
        catalog.list_environments.return_value = []
        resource = EnvironmentResource(_client=client)

        def list_result(step: object) -> ProcessResult:
            prepared = cast("PreparedStep", step)
            return ProcessResult(
                argv=prepared.argv,
                returncode=0,
                stdout=str(tmp_path) if prepared.step_id.endswith("toplevel") else ".git\n",
                stderr="",
                duration=0.0,
                cwd=prepared.cwd,
                environment=prepared.environment,
            )

        executor = RecordingExecutor(result_factory=list_result)

        result = resource.list_command(project=tmp_path, executor=executor).run()

        assert result == []
        assert [step.step_id for step in executor.executed] == [
            "environment.list.git.toplevel",
            "environment.list.git.common-dir",
        ]
        catalog.list_environments.assert_called_once_with(
            git_common_dir=str((Path.cwd() / ".git").resolve()), include_removed=False
        )
