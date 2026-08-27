from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.resources.environment import (
    EnvironmentDatabaseMode,
    EnvironmentState,
)

if TYPE_CHECKING:
    from click.testing import Result

    from odoo_instance_sdk import OdooClient


def _invoke(env_client: OdooClient, args: list[str]) -> Result:
    with (
        patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client),
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=env_client),
    ):
        return CliRunner().invoke(cli, args, catch_exceptions=False)


class TestOdcliLifecycle:
    def test_init_checkout_run_shell_remove(
        self,
        env_client: OdooClient,
        git_repo: Path,
        fake_python: Path,
        source_config: Path,
    ) -> None:
        fake_odoo = fake_python.parent / "odoo-bin"
        fake_odoo.write_text("#!/bin/sh\nexit 0\n")
        fake_odoo.chmod(0o755)
        init_result = CliRunner().invoke(
            cli,
            [
                "init",
                "--no-input",
                "--odoo-bin",
                str(fake_odoo),
                "--python",
                str(fake_python),
                "--config",
                str(source_config),
                "--database",
                "comerta",
                "--project",
                str(git_repo),
            ],
            catch_exceptions=False,
        )
        assert init_result.exit_code == 0, init_result.output
        assert (git_repo / ".odcli" / "project.toml").is_file()

        checkout_result = _invoke(
            env_client,
            ["--project", str(git_repo), "env", "checkout", "feat/lifecycle"],
        )
        assert checkout_result.exit_code == 0, checkout_result.output

        envs = env_client.environments.list(project=git_repo)
        assert len(envs) == 1
        env = envs[0]
        assert env.state == EnvironmentState.READY
        assert env.db_mode == EnvironmentDatabaseMode.SHARED
        assert Path(env.worktree_path).is_dir()
        assert Path(env.generated_config_path).is_file()

        env_id = str(env.id)
        with (
            patch("odoo_instance_sdk.internal.context._check_port_free", return_value=True),
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance.run_foreground",
                return_value=0,
            ),
            patch("odoo_instance_sdk.resources.instance.OdooInstance.shell", return_value=0),
        ):
            run_result = _invoke(env_client, ["--project", str(git_repo), "--env", env_id, "run"])
            assert run_result.exit_code == 0, run_result.output

            catalog = env_client.get_catalog()
            used = catalog.get_environment(env_id)
            assert used is not None
            assert used["last_used_at"] is not None
            use_events = catalog._conn.execute(
                "SELECT operation, outcome FROM environment_events WHERE environment_id = ?",
                (env_id,),
            ).fetchall()
            assert [(event["operation"], event["outcome"]) for event in use_events].count(
                ("use", "succeeded")
            ) == 1

            shell_result = _invoke(
                env_client, ["--project", str(git_repo), "--env", env_id, "shell"]
            )
            assert shell_result.exit_code == 0, shell_result.output
            assert (
                catalog._conn.execute(
                    "SELECT COUNT(*) FROM environment_events WHERE environment_id = ? AND operation = 'use'",
                    (env_id,),
                ).fetchone()[0]
                == 1
            )

        remove_result = _invoke(
            env_client,
            ["--project", str(git_repo), "env", "remove", env_id, "--yes"],
        )
        assert remove_result.exit_code == 0, remove_result.output

        removed = env_client.environments.get(env_id)
        assert removed.state == EnvironmentState.REMOVED
        assert not Path(removed.worktree_path).exists()
        assert not Path(removed.generated_config_path).exists()
        row = env_client.get_catalog().get_environment(env_id)
        assert row is not None
        assert row["state"] == EnvironmentState.REMOVED.value
