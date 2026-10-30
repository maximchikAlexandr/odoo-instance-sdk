from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.resources.environment import (
    EnvironmentDatabaseMode,
    EnvironmentState,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from click.testing import Result

    from odoo_instance_sdk import OdooClient


def _invoke(env_client: OdooClient, args: list[str]) -> Result:
    with (
        patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client),
        patch("odoo_instance_sdk.commands.env.checkout.OdooClient", return_value=env_client),
        patch("odoo_instance_sdk.commands.env.list.OdooClient", return_value=env_client),
        patch("odoo_instance_sdk.client.OdooClient", return_value=env_client),
    ):
        return CliRunner().invoke(cli, args, catch_exceptions=False)


def _init_project(git_repo: Path, fake_python: Path, source_config: Path) -> Result:
    fake_odoo = fake_python.parent / "odoo-bin"
    fake_odoo.write_text("#!/bin/sh\nexit 0\n")
    fake_odoo.chmod(0o755)
    return CliRunner().invoke(
        cli,
        [
            "init",
            "--allow-partial",
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


@pytest.fixture
def checked_out_environment(
    env_client: OdooClient,
    git_repo: Path,
    fake_python: Path,
    source_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[OdooClient, Path, str]]:
    """Create a fresh initialized project and checkout for each public CLI check."""
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.remote_branch_names", lambda *_args: ()
    )

    def revalidate_ticket(*_args: object, context: Any = None, **_kwargs: object) -> None:
        if context is not None:
            context.process("checkout.ticket.local-heads")
            context.process("checkout.ticket.remote-heads")

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.checkout._revalidate_ticket_absence", revalidate_ticket
    )
    init_result = _init_project(git_repo, fake_python, source_config)
    assert init_result.exit_code == 0, init_result.output
    assert (git_repo / ".odcli" / "project.toml").is_file()
    env_client.get_catalog()._conn.execute("SELECT 1")

    checkout_result = _invoke(
        env_client,
        [
            "--project",
            str(git_repo),
            "env",
            "checkout",
            "PROJ-123",
            "--source-db",
            "comerta",
        ],
    )
    assert checkout_result.exit_code == 0, checkout_result.output
    environments = env_client.environments.list(project=git_repo)
    assert len(environments) == 1
    environment_id = str(environments[0].id)
    yield env_client, git_repo, environment_id


@pytest.fixture
def successful_runtime_commands() -> Iterator[None]:
    run_command = MagicMock()
    run_command.run.return_value = 0
    shell_command = MagicMock()
    shell_command.run.return_value = 0
    with ExitStack() as stack:
        stack.enter_context(
            patch("odoo_instance_sdk.internal.context._check_port_free", return_value=True)
        )
        stack.enter_context(
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance.run_foreground_command",
                return_value=run_command,
            )
        )
        stack.enter_context(
            patch("odoo_instance_sdk.resources.instance.OdooInstance.shell", return_value=0)
        )
        stack.enter_context(
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance.shell_command",
                return_value=shell_command,
            )
        )
        yield


def test_init_registers_project_manifest(
    git_repo: Path, fake_python: Path, source_config: Path
) -> None:
    result = _init_project(git_repo, fake_python, source_config)

    assert result.exit_code == 0, result.output
    assert (git_repo / ".odcli" / "project.toml").is_file()


def test_checkout_creates_ready_shared_environment_and_artifacts(
    checked_out_environment: tuple[OdooClient, Path, str],
) -> None:
    env_client, _project, environment_id = checked_out_environment

    environment = env_client.environments.get(environment_id)
    assert environment.state == EnvironmentState.READY
    assert environment.db_mode == EnvironmentDatabaseMode.SHARED
    assert Path(environment.worktree_path).is_dir()
    assert Path(environment.generated_config_path).is_file()


def test_run_records_last_used_and_one_successful_use(
    checked_out_environment: tuple[OdooClient, Path, str], successful_runtime_commands: None
) -> None:
    env_client, project, environment_id = checked_out_environment
    result = _invoke(env_client, ["--project", str(project), "--env", environment_id, "run"])

    assert result.exit_code == 0, result.output
    catalog = env_client.get_catalog()
    used = catalog.get_environment(environment_id)
    assert used is not None
    assert used["last_used_at"] is not None
    events = catalog._conn.execute(
        "SELECT operation, outcome FROM environment_events WHERE environment_id = ?",
        (environment_id,),
    ).fetchall()
    assert [(event["operation"], event["outcome"]) for event in events].count(
        ("use", "succeeded")
    ) == 1


def test_shell_success_does_not_add_use_event(
    checked_out_environment: tuple[OdooClient, Path, str], successful_runtime_commands: None
) -> None:
    env_client, project, environment_id = checked_out_environment
    run_result = _invoke(env_client, ["--project", str(project), "--env", environment_id, "run"])
    assert run_result.exit_code == 0, run_result.output

    catalog = env_client.get_catalog()
    before = catalog._conn.execute(
        "SELECT operation, outcome FROM environment_events WHERE environment_id = ?",
        (environment_id,),
    ).fetchall()
    before_use_events = [
        (event["operation"], event["outcome"]) for event in before if event["operation"] == "use"
    ]
    assert before_use_events == [("use", "succeeded")]

    shell_result = _invoke(
        env_client, ["--project", str(project), "--env", environment_id, "shell"]
    )
    assert shell_result.exit_code == 0, shell_result.output
    after = catalog._conn.execute(
        "SELECT operation, outcome FROM environment_events WHERE environment_id = ?",
        (environment_id,),
    ).fetchall()
    after_use_events = [
        (event["operation"], event["outcome"]) for event in after if event["operation"] == "use"
    ]
    assert after_use_events == before_use_events


def test_remove_marks_environment_and_deletes_owned_artifacts(
    checked_out_environment: tuple[OdooClient, Path, str],
) -> None:
    env_client, project, environment_id = checked_out_environment
    environment = env_client.environments.get(environment_id)
    worktree = Path(environment.worktree_path)
    generated_config = Path(environment.generated_config_path)

    result = _invoke(
        env_client, ["--project", str(project), "env", "remove", environment_id, "--yes"]
    )

    assert result.exit_code == 0, result.output
    removed = env_client.environments.get(environment_id)
    assert removed.state == EnvironmentState.REMOVED
    assert not worktree.exists()
    assert not generated_config.exists()
    row = env_client.get_catalog().get_environment(environment_id)
    assert row is not None
    assert row["state"] == EnvironmentState.REMOVED.value
