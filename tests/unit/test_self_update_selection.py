"""Focused update-selection, ancestry, and CLI boundary regressions."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan, ProcessStep
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    PreparedStep,
    ProcessResultLike,
    RecordingExecutor,
    StepEvent,
)
from odoo_instance_sdk.internal.self_update import update_command
from odoo_instance_sdk.internal.self_update_ancestry import git_revision_relation
from odoo_instance_sdk.internal.self_update_commands import _preflight_error
from odoo_instance_sdk.models.update import UpdateResult
from tests.unit.test_self_update import (
    _SHA_A,
    _SHA_B,
    _executor_factory,
    _FakeDist,
    _patch_distribution,
    _process_result,
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
        "odoo_instance_sdk.internal.self_update_policy._preflight_disk_check",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_policy._catalog_schema_version",
        lambda: "head",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_policy._validate_catalog_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_policy._validate_storage_migration_path",
        lambda: None,
    )
    error = _preflight_error(
        ref="b" * 40,
        provenance=replace(_provenance(), source_repo=source_repo),
        allow_downgrade=False,
    )
    assert error == "cannot verify revision ancestry; source provenance is unsupported"


@pytest.mark.parametrize("storage_state", ("in-progress", "complete"))
def test_downgrade_fails_when_snapshot_state_is_not_restorable(
    monkeypatch: pytest.MonkeyPatch,
    storage_state: str,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_policy._preflight_disk_check",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_policy._catalog_schema_version",
        lambda: "head",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_policy._validate_catalog_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_policy._validate_storage_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_policy._storage_migration_state",
        lambda: storage_state,
    )
    error = _preflight_error(
        ref="b" * 40,
        provenance=_provenance(),
        allow_downgrade=True,
        relation="ancestor",
    )
    assert error == f"downgrade snapshot cannot restore storage state '{storage_state}'"


@pytest.mark.parametrize(
    ("source_url", "sentinels"),
    (
        (
            "https://user-sentinel:password-sentinel@github.com/"
            "maximchikAlexandr/odoo-instance-sdk.git",
            ("user-sentinel", "password-sentinel"),
        ),
        (
            "https://github.com/maximchikAlexandr/odoo-instance-sdk.git?token=query-sentinel",
            ("token=query-sentinel",),
        ),
        (
            "https://github.com/maximchikAlexandr/odoo-instance-sdk.git#fragment-sentinel",
            ("fragment-sentinel",),
        ),
    ),
)
def test_public_structured_failure_redacts_provenance_secrets(
    monkeypatch: pytest.MonkeyPatch,
    source_url: str,
    sentinels: tuple[str, ...],
) -> None:
    from odoo_instance_sdk.commands.update import update_command_cli

    direct_url = json.dumps(
        {
            "url": source_url,
            "vcs_info": {"vcs": "git", "commit_id": _SHA_A},
        }
    )
    _patch_distribution(monkeypatch, _FakeDist(direct_url=direct_url))
    command_result = update_command(check=True).run()
    result = CliRunner().invoke(
        update_command_cli,
        ["--check", "--format", "json"],
        prog_name="odcli",
    )
    assert result.exit_code == 1, result.output
    assert command_result.outcome == "unsupported_install"
    rendered = result.output + (command_result.next_step or "")
    assert all(sentinel not in rendered for sentinel in sentinels)
    assert "unsupported source repository" in result.output


def test_preflight_requires_captured_ancestry_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in (
        ("_preflight_disk_check", lambda: None),
        ("_catalog_schema_version", lambda: "head"),
        ("_validate_catalog_migration_path", lambda: None),
        ("_validate_storage_migration_path", lambda: None),
    ):
        monkeypatch.setattr(f"odoo_instance_sdk.internal.self_update_policy.{name}", value)
    error = _preflight_error(
        ref=_SHA_B,
        provenance=_provenance(),
        allow_downgrade=False,
    )
    assert error == "cannot verify revision ancestry; target history is unavailable"


def test_ancestry_cleanup_is_declared_mutating_action(
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.internal.self_update_commands import _build_mutating_command

    executable = tmp_path / "odcli"
    provenance = _provenance(executable=executable)
    command = _build_mutating_command(
        ref=_SHA_B,
        provenance=provenance,
        executor=_executor_factory({}),
        allow_downgrade=False,
    )
    cleanup = next(
        step for step in command.plan.steps if step.step_id == "update.inspect.ancestry-cleanup"
    )
    assert isinstance(cleanup, ActionStep)
    assert cleanup.mutating


def _preflight_executor(*, init_rc: int = 0, fetch_rc: int = 0) -> RecordingExecutor:
    def factory(step: PreparedProcess) -> ProcessResultLike:
        prepared = cast("PreparedStep", step)
        if prepared.step_id == "update.inspect.ancestry-init":
            return _process_result(prepared, returncode=init_rc)
        if prepared.step_id == "update.inspect.ancestry-fetch":
            return _process_result(prepared, returncode=fetch_rc)
        if prepared.step_id in {
            "update.inspect.ancestry-installed",
        }:
            return _process_result(prepared, returncode=0)
        if prepared.step_id == "update.inspect.ancestry-target":
            return _process_result(prepared, returncode=1)
        return _process_result(prepared)

    return RecordingExecutor(result_factory=factory)


@pytest.mark.parametrize(
    ("scenario", "init_rc", "fetch_rc"),
    (
        ("success", 0, 0),
        ("absent-store", 0, 0),
        ("failed-init", 1, 0),
        ("failed-fetch", 0, 1),
        ("filesystem-error", 0, 0),
    ),
)
def test_ancestry_cleanup_ledger_covers_probe_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    init_rc: int,
    fetch_rc: int,
) -> None:
    from odoo_instance_sdk.internal.self_update_commands import _build_preflight_command

    for name, value in (
        ("_preflight_disk_check", lambda: None),
        ("_catalog_schema_version", lambda: "head"),
        ("_validate_catalog_migration_path", lambda: None),
        ("_validate_storage_migration_path", lambda: None),
    ):
        monkeypatch.setattr(f"odoo_instance_sdk.internal.self_update_policy.{name}", value)

    cleanup_calls: list[Path] = []

    def cleanup(path: str | Path) -> None:
        cleanup_calls.append(Path(path))
        if scenario == "absent-store":
            raise FileNotFoundError(path)
        if scenario == "filesystem-error":
            raise OSError("cleanup sentinel")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_ancestry.shutil.rmtree",
        cleanup,
    )
    executor = _preflight_executor(init_rc=init_rc, fetch_rc=fetch_rc)
    command = _build_preflight_command(
        ref=_SHA_B,
        provenance=_provenance(),
        allow_downgrade=False,
        executor=executor,
    )
    events: list[StepEvent] = []
    if scenario == "filesystem-error":
        with pytest.raises(OSError, match="cleanup sentinel"):
            command.run(observer=events.append)
    else:
        result = command.run(observer=events.append)
        expected_outcome = (
            "updated" if scenario in {"success", "absent-store"} else "preflight_failed"
        )
        assert result.outcome == expected_outcome

    assert cleanup_calls
    cleanup_events = [
        event.kind for event in events if event.step_id == "update.inspect.ancestry-cleanup"
    ]
    assert cleanup_events == (
        ["started", "failed"] if scenario == "filesystem-error" else ["started", "completed"]
    )
    executed = [step.step_id for step in executor.executed]
    if init_rc:
        assert executed == ["update.inspect.ancestry-init"]
    elif fetch_rc:
        assert executed == [
            "update.inspect.ancestry-init",
            "update.inspect.ancestry-fetch",
        ]
    else:
        assert executed == [
            "update.inspect.ancestry-init",
            "update.inspect.ancestry-fetch",
            "update.inspect.ancestry-installed",
            "update.inspect.ancestry-target",
        ]


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
