from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

if TYPE_CHECKING:
    import pytest
    from click.testing import Result

    from odoo_instance_sdk import OdooClient


def _invoke(runner: CliRunner, client: OdooClient, args: list[str]) -> Result:
    with patch("odoo_instance_sdk.cli._make_client", return_value=client):
        return runner.invoke(cli, args)


def test_nested_worktree_infers_remove_selector(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = env_client.environments.checkout(
        project_manifest,
        "feat/nested-context",
        options=EnvironmentCheckoutOptions(python=str(fake_python)),
    )
    nested = Path(env.worktree_path) / "nested" / "child"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = _invoke(CliRunner(), env_client, ["env", "remove", "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["result"]["id"] == str(env.id)
    assert envelope["dry_run"] is True
    assert Path(env.worktree_path).is_dir()


def test_outside_context_requires_explicit_project(
    env_client: OdooClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(CliRunner(), env_client, ["env", "list", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "env_list_failed"


def test_sync_rejects_root_env_as_usage_error(env_client: OdooClient) -> None:
    result = _invoke(CliRunner(), env_client, ["--env", "anything", "env", "sync", "--json"])

    assert result.exit_code == 2
    envelope = json.loads(result.output)
    assert envelope["error"]["code"] == "usage_error"


def test_checkout_dry_run_has_full_plan_and_no_catalog_mutation(
    env_client: OdooClient, project_manifest: Path, fake_python: Path
) -> None:
    before = env_client.environments.list(project=project_manifest, include_removed=True)
    result = _invoke(
        CliRunner(),
        env_client,
        [
            "--project",
            str(project_manifest),
            "env",
            "checkout",
            "feat/plan",
            "--python",
            str(fake_python),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)["result"]
    assert {
        "generated_config_path",
        "dependency_lock_path",
        "python_mode",
        "database",
        "ownership",
        "commands",
    } <= plan.keys()
    assert plan["database"]["mode"] == "shared"
    assert env_client.environments.list(project=project_manifest, include_removed=True) == before
    assert not Path(plan["worktree_path"]).exists()


def test_list_json_reconciles_and_human_has_required_columns(
    env_client: OdooClient, project_manifest: Path, fake_python: Path
) -> None:
    env = env_client.environments.checkout(
        project_manifest,
        "feat/list-cli",
        options=EnvironmentCheckoutOptions(python=str(fake_python)),
    )
    runner = CliRunner()
    args = ["--project", str(project_manifest), "env", "list"]
    human = _invoke(runner, env_client, args)
    data = _invoke(runner, env_client, [*args, "--json"])

    assert human.exit_code == 0
    assert (
        "OBSERVED" in human.output and "PYTHON_MODE" in human.output and "LAST_USED" in human.output
    )
    listed = json.loads(data.output)["result"]["environments"][0]
    assert listed["id"] == str(env.id)
    assert {
        "observed",
        "reconciliation",
        "source_database",
        "target_database",
        "last_used",
    } <= listed.keys()


def test_list_reports_occupied_port(
    env_client: OdooClient, project_manifest: Path, fake_python: Path
) -> None:
    env = env_client.environments.checkout(
        project_manifest,
        "feat/list-port",
        options=EnvironmentCheckoutOptions(python=str(fake_python)),
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((env.http_interface, env.http_port))
    listener.listen()
    try:
        result = _invoke(
            CliRunner(), env_client, ["--project", str(project_manifest), "env", "list", "--json"]
        )
    finally:
        listener.close()

    assert result.exit_code == 0
    assert json.loads(result.output)["result"]["environments"][0]["observed"] == "port-occupied"


def test_list_all_projects_works_outside_a_project(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = env_client.environments.checkout(
        project_manifest,
        "feat/all-projects",
        options=EnvironmentCheckoutOptions(python=str(fake_python)),
    )
    monkeypatch.chdir(tmp_path)

    result = _invoke(CliRunner(), env_client, ["env", "list", "--all-projects", "--json"])

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)["result"]["environments"]] == [
        str(env.id)
    ]


def test_list_excludes_removed_unless_all(
    env_client: OdooClient, project_manifest: Path, fake_python: Path
) -> None:
    env = env_client.environments.checkout(
        project_manifest,
        "feat/list-removed",
        options=EnvironmentCheckoutOptions(python=str(fake_python)),
    )
    env_client.environments.remove(env)
    runner = CliRunner()
    base = ["--project", str(project_manifest), "env", "list", "--json"]

    default = _invoke(runner, env_client, base)
    all_rows = _invoke(runner, env_client, [*base[:-1], "--all", "--json"])

    assert default.exit_code == all_rows.exit_code == 0
    assert json.loads(default.output)["result"]["environments"] == []
    assert [row["id"] for row in json.loads(all_rows.output)["result"]["environments"]] == [
        str(env.id)
    ]
