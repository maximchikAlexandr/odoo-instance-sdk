"""Focused update-selection, ancestry, and CLI boundary regressions."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan, ProcessStep
from odoo_instance_sdk.internal.self_update_ancestry import git_revision_relation
from odoo_instance_sdk.internal.self_update_commands import _preflight_error
from odoo_instance_sdk.models.update import UpdateResult
from tests.unit.test_self_update import (
    _SHA_A,
    _SHA_B,
    _executor_factory,
    _provenance,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_history(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "state.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "state.txt")
    _git(repo, "commit", "-q", "-m", "base")
    ancestor = _git(repo, "rev-parse", "HEAD")
    (repo / "state.txt").write_text("descendant\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "descendant")
    descendant = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "divergent", ancestor)
    (repo / "state.txt").write_text("divergent\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "divergent")
    divergent = _git(repo, "rev-parse", "HEAD")
    return repo, ancestor, descendant, divergent


@pytest.mark.parametrize("relation", ("same", "descendant", "ancestor", "divergent"))
def test_git_revision_relation_uses_real_history(
    git_history: tuple[Path, str, str, str],
    relation: str,
) -> None:
    repo, ancestor, descendant, divergent = git_history
    pairs = {
        "same": (descendant, descendant),
        "descendant": (ancestor, descendant),
        "ancestor": (descendant, ancestor),
        "divergent": (descendant, divergent),
    }
    installed, target = pairs[relation]
    assert (
        git_revision_relation(
            source_repo=str(repo),
            installed_sha=installed,
            target_sha=target,
        )
        == relation
    )


def test_git_revision_relation_fails_closed_without_source() -> None:
    assert (
        git_revision_relation(
            source_repo=None,
            installed_sha="a" * 40,
            target_sha="b" * 40,
        )
        == "unknown"
    )


@pytest.mark.parametrize(
    "source_repo",
    (
        "https://attacker.invalid/github.com/maximchikAlexandr/odoo-instance-sdk",
        "https://github.com.evil.invalid/maximchikAlexandr/odoo-instance-sdk.git",
        "https://user:secret@github.com/maximchikAlexandr/odoo-instance-sdk.git",
        "file:///tmp/github.com/maximchikAlexandr/odoo-instance-sdk.git",
    ),
)
def test_unsupported_provenance_is_rejected_before_ancestry_fetch(
    monkeypatch: pytest.MonkeyPatch,
    source_repo: str,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._preflight_disk_check",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._catalog_schema_version",
        lambda: "head",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._validate_catalog_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._validate_storage_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._git_revision_relation",
        lambda **_kwargs: pytest.fail("unsupported provenance must fail before fetch"),
    )
    error = _preflight_error(
        ref="b" * 40,
        provenance=replace(_provenance(), source_repo=source_repo),
        allow_downgrade=False,
    )
    assert error == "cannot verify revision ancestry; source provenance is unsupported"


def test_downgrade_fails_when_snapshot_state_is_not_restorable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._preflight_disk_check",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._catalog_schema_version",
        lambda: "head",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._validate_catalog_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._validate_storage_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._storage_migration_state",
        lambda: "in-progress",
    )
    error = _preflight_error(
        ref="b" * 40,
        provenance=_provenance(),
        allow_downgrade=True,
        relation="ancestor",
    )
    assert error == "downgrade snapshot cannot restore storage state 'in-progress'"


def test_update_no_input_without_yes_exits_before_mutations() -> None:
    from odoo_instance_sdk.commands.update import update_command_cli

    result = CliRunner().invoke(update_command_cli, ["--no-input"], prog_name="odcli")
    assert result.exit_code == 1
    assert "requires --yes" in result.output


def test_structured_update_requires_yes_before_command_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.commands.update import update_command_cli

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.update.update_command",
        lambda **_kwargs: pytest.fail("structured update must stop before command selection"),
    )
    result = CliRunner().invoke(
        update_command_cli,
        ["--format", "json"],
        prog_name="odcli",
    )
    assert result.exit_code == 1, result.output
    assert "requires --yes" in result.output


def test_exact_sha_dry_run_runs_preflight_and_shows_immutable_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.commands.update import update_command_cli

    install = ProcessStep(
        step_id="update.install",
        argv=("uv", "tool", "install", "--force", f"odoo-instance-sdk@{_SHA_B}"),
        display="uv tool install",
        executable="uv",
        mutating=True,
    )
    migrate = ProcessStep(
        step_id="update.migrate",
        argv=("odcli", "update", "--format", "json"),
        display="odcli update",
        executable="odcli",
        mutating=True,
    )
    plan = ExecutionPlan(
        steps=(
            ActionStep(
                step_id="update.inspect",
                action="inspect",
                description="Inspect installed OdCLI provenance",
                read_only=True,
            ),
            install,
            migrate,
        )
    )
    candidate = Command.create(
        plan,
        lambda _context: UpdateResult(outcome="updated", target_sha=_SHA_B),
    )
    preflight_calls: list[str] = []

    def fake_preflight(**_kwargs: object) -> Command[UpdateResult]:
        def run(_context: object) -> UpdateResult:
            preflight_calls.append("run")
            return UpdateResult(outcome="updated", target_sha=_SHA_B)

        return Command.create(ExecutionPlan(), run)

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.update.update_command", lambda **_kwargs: candidate
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.update.preflight_update_command", fake_preflight
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.update.prepare_maintenance_environment", lambda: None
    )
    result = CliRunner().invoke(
        update_command_cli,
        ["--ref", _SHA_B, "--dry-run", "--format", "json"],
        prog_name="odcli",
    )
    assert result.exit_code == 0, result.output
    assert preflight_calls == ["run"]
    assert _SHA_B in result.output
    assert "update.migrate" in result.output


def test_mutable_ref_already_current_has_no_mutation_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.commands.update import update_command_cli
    from odoo_instance_sdk.internal.self_update_commands import (
        _build_already_current_command,
        _build_staged_command,
    )

    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    provenance = _provenance(executable=executable)
    executor = _executor_factory({"uv_stdout": f"resolved {_SHA_A}\n"})
    calls: list[str] = []

    def fake_update_command(*, ref: str, **_kwargs: object) -> Command[UpdateResult]:
        calls.append(ref)
        if ref == "main":
            return _build_staged_command(ref=ref, provenance=provenance, executor=executor)
        return _build_already_current_command(provenance)

    monkeypatch.setattr("odoo_instance_sdk.commands.update.update_command", fake_update_command)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.update.prepare_maintenance_environment", lambda: None
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.update.preflight_update_command",
        lambda **_kwargs: pytest.fail("already-current resolution must skip preflight"),
    )
    result = CliRunner().invoke(
        update_command_cli,
        ["--yes", "--format", "json"],
        prog_name="odcli",
    )
    assert result.exit_code == 0, result.output
    assert calls == ["main", _SHA_A]
    assert [step.step_id for step in executor.executed] == ["update.resolve"]
    assert "update.install" not in result.output
    assert "update.migrate" not in result.output
