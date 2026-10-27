from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.execution import ExecutionPlan
from odoo_instance_sdk.resources.git import GitResource
from odoo_instance_sdk.resources.instance import OdooInstance
from tests.unit.test_git_resource import _git, _repo


@pytest.mark.parametrize(
    "command",
    [
        ("git", "commit"),
        ("git", "sync"),
        ("git", "clone"),
        ("git", "mr", "create"),
    ],
)
def test_member_id_help_names_multica_account_user_id(command: tuple[str, ...]) -> None:
    result = CliRunner().invoke(cli, [*command, "--help"])

    assert result.exit_code == 0, result.output
    help_text = " ".join(result.output.replace("│", " ").replace("`", "").split())
    assert "multica workspace member list --output json" in help_text
    assert "user_id" in help_text
    assert "membership-row" in help_text
    assert "agent ID" in help_text
    assert "squad ID" in help_text


def _context(root: Path) -> SimpleNamespace:
    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=root)
    )
    instance.git = GitResource(cast("OdooInstance", instance))
    return SimpleNamespace(instance=instance)


@pytest.mark.parametrize("tag", ["IMP", "VIEW", "DEMO", "DATA", "ACC", "UI"])
def test_git_commit_dry_run_has_shared_message_and_no_commit(tmp_path: Path, tag: str) -> None:
    _repo(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD").strip()
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")

    with patch(
        "odoo_instance_sdk.commands.git.cli_context.ready_instance", return_value=_context(tmp_path)
    ):
        result = CliRunner().invoke(
            cli,
            [
                "git",
                "commit",
                "change",
                "--ticket",
                "PROJ-123",
                "--tag",
                tag,
                "--dry-run",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["result"]["steps"][-1]["argv"][-2:] == [
        "-m",
        f"[{tag}] {tmp_path.name}: PROJ-123 change",
    ]
    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head


def test_git_commit_hook_failure_is_nonzero_typed_cli_error(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    hook = tmp_path / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\necho rejected >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    head = _git(tmp_path, "rev-parse", "HEAD").strip()

    with patch(
        "odoo_instance_sdk.commands.git.cli_context.ready_instance", return_value=_context(tmp_path)
    ):
        result = CliRunner().invoke(
            cli,
            [
                "git",
                "commit",
                "change",
                "--ticket",
                "PROJ-123",
                "--tag",
                "IMP",
                "--yes",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "git_workflow_error"
    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head


def test_git_sync_dry_run_does_not_fetch_rebase_or_publish(tmp_path: Path) -> None:
    _repo(tmp_path)
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    _git(tmp_path, "commit", "-qm", "[IMP] feature: change")
    head = _git(tmp_path, "rev-parse", "HEAD").strip()

    with patch(
        "odoo_instance_sdk.commands.git.cli_context.ready_instance", return_value=_context(tmp_path)
    ):
        result = CliRunner().invoke(
            cli,
            ["git", "sync", "--base", "main", "--dry-run", "--yes", "--format", "json"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    planned_ids = {step["step_id"] for step in payload["result"]["steps"]}
    assert {
        "git.sync.status",
        "git.sync.branch",
        "git.sync.upstream",
        "git.sync.remote",
        "git.sync.fetch",
        "git.sync.fetched-sha",
        "git.sync.integrate",
        "git.sync.rebase",
        "git.sync.check",
        "git.sync.remote-ancestry",
        "git.sync.push-fast-forward",
    } <= planned_ids
    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head


def test_git_commit_explicit_initialized_repo_uses_odoo_policy(tmp_path: Path) -> None:
    _repo(tmp_path)
    _git(tmp_path, "remote", "add", "origin", "https://gitlab.example/team/repo")
    (tmp_path / ".odcli").mkdir()
    (tmp_path / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(tmp_path, "add", ".odcli/project.toml")
    _git(tmp_path, "commit", "-qm", "project config")
    module = tmp_path / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "models.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "addons/sale")

    with patch(
        "odoo_instance_sdk.internal.git_explicit.explicit_commit_environment",
        return_value={
            "GIT_AUTHOR_NAME": "Selected Member",
            "GIT_AUTHOR_EMAIL": "selected@example.test",
            "GIT_COMMITTER_NAME": "Selected Member",
            "GIT_COMMITTER_EMAIL": "selected@example.test",
        },
    ):
        result = CliRunner().invoke(
            cli,
            [
                "git",
                "commit",
                "add model",
                "--repo",
                str(tmp_path),
                "--member-id",
                "11111111-1111-4111-8111-111111111111",
                "--ticket",
                "PROJ-12",
                "--yes",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    assert _git(tmp_path, "log", "-1", "--format=%s").strip() == "[ADD] sale: PROJ-12 add model"


def test_git_commit_project_member_does_not_require_repo(tmp_path: Path) -> None:
    _repo(tmp_path)
    _git(tmp_path, "remote", "add", "origin", "https://gitlab.example/team/repo")
    (tmp_path / ".odcli").mkdir()
    (tmp_path / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(tmp_path, "add", ".odcli/project.toml")
    _git(tmp_path, "commit", "-qm", "project config")
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    identity = {
        "GIT_AUTHOR_NAME": "Selected Member",
        "GIT_AUTHOR_EMAIL": "selected@example.test",
        "GIT_COMMITTER_NAME": "Selected Member",
        "GIT_COMMITTER_EMAIL": "selected@example.test",
    }

    with (
        patch(
            "odoo_instance_sdk.commands.git.cli_context.ready_instance",
            return_value=_context(tmp_path),
        ),
        patch(
            "odoo_instance_sdk.internal.git_explicit.explicit_commit_environment",
            return_value=identity,
        ) as capture,
    ):
        result = CliRunner().invoke(
            cli,
            [
                "git",
                "commit",
                "change",
                "--member-id",
                "11111111-1111-4111-8111-111111111111",
                "--tag",
                "DOC",
                "--dry-run",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    commit = next(step for step in payload["result"]["steps"] if step["step_id"] == "git.commit")
    assert dict(commit["environment_overrides"]) == identity
    assert commit["argv"][-2:] == ["-m", f"[DOC] {tmp_path.name}: change"]
    capture.assert_called_once_with(
        "11111111-1111-4111-8111-111111111111",
        "gitlab.example",
        project_root=tmp_path,
    )


def test_git_sync_explicit_repo_passes_selected_member_to_dispatcher(tmp_path: Path) -> None:
    _repo(tmp_path)
    fake_resource = Mock()
    fake_resource.sync_command.return_value = SimpleNamespace(
        plan=ExecutionPlan(steps=()).with_fingerprint()
    )

    with patch("odoo_instance_sdk.commands.git._generic_resource", return_value=fake_resource):
        result = CliRunner().invoke(
            cli,
            [
                "git",
                "sync",
                "--repo",
                str(tmp_path),
                "--member-id",
                "11111111-1111-4111-8111-111111111111",
                "--base",
                "main",
                "--dry-run",
                "--yes",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    fake_resource.sync_command.assert_called_once_with(
        repository=str(tmp_path),
        member_id="11111111-1111-4111-8111-111111111111",
        base="main",
        push=False,
    )
