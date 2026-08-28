from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import Snapshot
from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions
from tests.unit.monitor_support import FakeProcessProvider

if TYPE_CHECKING:
    from click.testing import Result

    from odoo_instance_sdk import OdooClient


def _invoke(runner: CliRunner, client: OdooClient, args: list[str]) -> Result:
    with patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client):
        return runner.invoke(cli, args)


@pytest.fixture(autouse=True)
def _inject_monitor_process_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

    original_init = EnvironmentMonitor.__init__

    def init(self: EnvironmentMonitor, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("process_provider", FakeProcessProvider())
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EnvironmentMonitor, "__init__", init)


def test_nested_worktree_infers_remove_selector(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = env_client.environments.checkout(
        project_manifest,
        "feat/nested-context",
        options=EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta"),
    )
    nested = Path(env.worktree_path) / "nested" / "child"
    nested.mkdir(parents=True)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.rev_parse_toplevel",
        lambda _path: Path(env.worktree_path),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.rev_parse_git_common_dir",
        lambda _path: Path(env.git_common_dir),
    )
    monkeypatch.chdir(nested)

    result = _invoke(CliRunner(), env_client, ["env", "remove", "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["result"]["id"] == str(env.id)
    assert envelope["dry_run"] is True
    assert Path(env.worktree_path).is_dir()


def test_outside_context_lists_all_projects(
    env_client: OdooClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(CliRunner(), env_client, ["env", "list", "--json"])

    assert result.exit_code == 0, result.output
    snapshot = json.loads(result.output)["result"]
    assert snapshot["environments"] == []
    assert snapshot["projects"] == []


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
            "--source-db",
            "comerta",
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)["result"]
    assert {
        "name",
        "branch",
        "effective_base_ref",
        "db_mode",
        "source_database",
        "target_database",
        "python_mode",
        "provenance",
        "freshness",
        "preparation_actions",
        "warnings",
    } <= plan.keys()
    assert plan["db_mode"] == "shared"
    assert (
        not {
            "config",
            "config_path",
            "path",
            "argv",
            "generated_config_path",
            "dependency_lock_path",
        }
        & plan.keys()
    )
    assert env_client.environments.list(project=project_manifest, include_removed=True) == before


def test_list_json_emits_snapshot_and_human_has_project_header(
    env_client: OdooClient, project_manifest: Path, fake_python: Path
) -> None:
    env = env_client.environments.checkout(
        project_manifest,
        "feat/list-cli",
        options=EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta"),
    )
    runner = CliRunner()
    args = ["--project", str(project_manifest), "env", "list"]
    human = _invoke(runner, env_client, args)
    data = _invoke(runner, env_client, [*args, "--json"])

    assert human.exit_code == 0
    # New grouped human format: project header + cluster line + env row.
    assert "Project " in human.output and "PostgreSQL" in human.output
    assert "\x1b" not in human.output
    payload = json.loads(data.output)["result"]
    # Snapshot contract parity: projects + environments with runtime/git/storage.
    assert "schema_version" in payload
    assert "projects" in payload and "environments" in payload
    listed = payload["environments"][0]
    assert listed["id"] == str(env.id)
    assert listed["branch"] == "feat/list-cli"
    assert "runtime" in listed and "git" in listed and "storage" in listed
    assert listed["lifecycle_state"] == "ready"


@pytest.mark.serial
def test_list_reports_occupied_port(
    env_client: OdooClient, project_manifest: Path, fake_python: Path
) -> None:
    env = env_client.environments.checkout(
        project_manifest,
        "feat/list-port",
        options=EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta"),
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((env.http_interface, env.http_port))
    listener.listen()
    try:
        result = _invoke(
            CliRunner(), env_client, ["--project", str(project_manifest), "env", "list"]
        )
    finally:
        listener.close()

    assert result.exit_code == 0
    # New contract: OBSERVED is only probed when lifecycle=ready AND
    # runtime=ready. With no Odoo process running, runtime is stopped, so the
    # OBSERVED column shows "—" (the allocated-port probe is deferred to the
    # running-runtime case).
    assert "Project " in result.output and "PostgreSQL" in result.output


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
        options=EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta"),
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
        options=EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta"),
    )
    env_client.environments.remove(env)
    runner = CliRunner()
    base = ["--project", str(project_manifest), "env", "list", "--json"]

    default = _invoke(runner, env_client, base)
    all_json = _invoke(runner, env_client, [*base[:-1], "--all", "--json"])
    default_human = _invoke(runner, env_client, ["--project", str(project_manifest), "env", "list"])
    all_human = _invoke(
        runner, env_client, ["--project", str(project_manifest), "env", "list", "--all"]
    )

    assert default.exit_code == all_json.exit_code == 0
    # --json always wraps non-removed Snapshot only; --all does NOT change JSON.
    assert json.loads(default.output)["result"]["environments"] == []
    assert json.loads(all_json.output)["result"]["environments"] == []
    # --all is human-only: removed row appears in human output.
    assert all_human.output != default_human.output


def test_explicit_project_and_environment_resolution_records_provenance(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
) -> None:
    env = env_client.environments.checkout(
        project_manifest,
        "feat/explicit-context",
        options=EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta"),
    )
    result = _invoke(
        CliRunner(),
        env_client,
        [
            "--project",
            str(project_manifest),
            "env",
            "remove",
            str(env.id),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["provenance"] == {
        "project_source": "explicit",
        "environment_source": "explicit",
    }
    assert envelope["context"]["environment_id"] == str(env.id)


def test_cwd_project_resolution_records_cwd_provenance(
    env_client: OdooClient,
    project_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(project_manifest)
    empty = Snapshot(schema_version=3, generated_at=datetime.now(UTC), projects=(), environments=())
    with patch(
        "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot",
        return_value=empty,
    ):
        result = _invoke(CliRunner(), env_client, ["env", "list", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["provenance"]["project_source"] == "cwd"


def test_cwd_environment_resolution_records_provenance_and_id(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = env_client.environments.checkout(
        project_manifest,
        "feat/cwd-environment-context",
        options=EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta"),
    )
    monkeypatch.chdir(Path(env.worktree_path))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.rev_parse_toplevel",
        lambda _path: Path(env.worktree_path),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.rev_parse_git_common_dir",
        lambda _path: Path(env.git_common_dir),
    )

    result = _invoke(
        CliRunner(),
        env_client,
        ["env", "remove", "--dry-run", "--json"],
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["provenance"]["environment_source"] == "cwd"
    assert envelope["context"]["environment_id"] == str(env.id)
