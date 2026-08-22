from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
)

if TYPE_CHECKING:
    from click.testing import Result

    from odoo_instance_sdk import OdooClient


def _checkout_ready_env(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    branch: str = "feat/vscode-gen",
) -> DevelopmentEnvironment:
    opts = EnvironmentCheckoutOptions(
        python=str(fake_python),
        db_mode=EnvironmentDatabaseMode.SHARED,
        odoo_bin=fake_python.parent / "odoo-bin",
    )
    return env_client.environments.checkout(project_manifest, branch, options=opts)


def _invoke(runner: CliRunner, env_client: OdooClient, args: list[str]) -> Result:
    with patch("odoo_instance_sdk.cli._make_client", return_value=env_client):
        return runner.invoke(cli, args, catch_exceptions=False)


class TestVscodeGenerateProfile:
    def test_prints_profile_fields(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_ready_env(env_client, project_manifest, fake_python)
        runner = CliRunner()
        result = _invoke(runner, env_client, ["--env", str(env.id), "vscode", "generate"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        cfg = data["configurations"][0]
        assert cfg["name"] == f"Odoo {env.name}"
        assert cfg["type"] == "python"
        assert cfg["request"] == "launch"
        assert cfg["python"] == str(fake_python)
        assert cfg["program"] == str(fake_python.parent / "odoo-bin")
        assert cfg["cwd"] == env.worktree_path
        assert cfg["justMyCode"] is False
        assert cfg["console"] == "integratedTerminal"
        args = cfg["args"]
        assert "--config" in args
        assert "--database" in args
        assert env.source_db_name in args
        assert "--http-port" in args
        assert str(env.http_port) in args
        for forbidden in ("-u", "-i", "--stop-after-init"):
            assert forbidden not in args
        blob = json.dumps(cfg)
        for secret in ("admin_passwd", "db_password", "master_pwd"):
            assert secret not in blob
        assert (project_manifest / ".vscode" / "launch.json").exists() is False

    def test_non_ready_env_errors(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        from odoo_instance_sdk.resources.environment import EnvironmentState

        env = _checkout_ready_env(
            env_client, project_manifest, fake_python, branch="feat/vscode-notready"
        )
        env_client.get_catalog().update_environment_state(
            str(env.id), EnvironmentState.FAILED, last_error="forced"
        )
        runner = CliRunner()
        result = _invoke(runner, env_client, ["--env", str(env.id), "vscode", "generate"])
        assert result.exit_code == 1
        assert "not ready" in result.output


class TestVscodeGenerateWrite:
    def test_write_on_absent_creates_file(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        tmp_path: Path,
    ) -> None:
        env = _checkout_ready_env(
            env_client, project_manifest, fake_python, branch="feat/vscode-write"
        )
        runner = CliRunner()
        result = _invoke(
            runner,
            env_client,
            ["--project", str(project_manifest), "--env", str(env.id), "vscode", "generate"],
        )
        assert result.exit_code == 0
        printed = json.loads(result.output)["configurations"][0]
        result_write = _invoke(
            runner,
            env_client,
            [
                "--project",
                str(project_manifest),
                "--env",
                str(env.id),
                "vscode",
                "generate",
                "--write",
            ],
        )
        assert result_write.exit_code == 0
        written_path = project_manifest / ".vscode" / "launch.json"
        assert written_path.is_file()
        written = json.loads(written_path.read_text())
        assert written["configurations"][0] == printed

    def test_write_on_existing_errors(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_ready_env(
            env_client, project_manifest, fake_python, branch="feat/vscode-exists"
        )
        vscode_dir = project_manifest / ".vscode"
        vscode_dir.mkdir(parents=True, exist_ok=True)
        target = vscode_dir / "launch.json"
        original = textwrap.dedent("""\
            // existing jsonc
            {
              "version": "0.2.0",
              "configurations": []
            }
        """)
        target.write_text(original)
        runner = CliRunner()
        result = _invoke(
            runner,
            env_client,
            [
                "--project",
                str(project_manifest),
                "--env",
                str(env.id),
                "vscode",
                "generate",
                "--write",
            ],
        )
        assert result.exit_code == 1
        assert "refuses merge/rewrite" in result.output
        assert target.read_text() == original

    def test_default_does_not_write(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        tmp_path: Path,
    ) -> None:
        env = _checkout_ready_env(
            env_client, project_manifest, fake_python, branch="feat/vscode-nowrite"
        )
        runner = CliRunner()
        result = _invoke(
            runner,
            env_client,
            ["--env", str(env.id), "vscode", "generate"],
        )
        assert result.exit_code == 0
        assert not (project_manifest / ".vscode" / "launch.json").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
