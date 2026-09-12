from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.resources.git import GitResource
from odoo_instance_sdk.resources.instance import OdooInstance
from tests.unit.test_git_resource import _git, _repo


def _context(root: Path) -> SimpleNamespace:
    instance = SimpleNamespace(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=root)
    )
    instance.git = GitResource(cast("OdooInstance", instance))
    return SimpleNamespace(instance=instance)


def test_git_commit_dry_run_has_shared_message_and_no_commit(tmp_path: Path) -> None:
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
                "IMP",
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
        f"[IMP] {tmp_path.name}: PROJ-123 change",
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
