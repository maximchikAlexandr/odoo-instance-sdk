"""Binding, progress, and checkout composition against a fake OpenSpec CLI."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from odcli_openspec.adapter import implementation_percent
from odcli_openspec.checkout import compose_checkout_command
from odcli_openspec.errors import OpenSpecError
from odcli_openspec.facts import OpenSpecFactsProvider
from odcli_openspec.models import OpenSpecBinding
from odcli_openspec.store import read_binding, write_binding

from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import (
    RunContext,
    SubprocessExecutor,
    prepared_command,
    prepared_step,
)
from odoo_instance_sdk.models import CheckoutRow

_FAKE = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

log = Path(os.environ["ODCLI_FAKE_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
if Path(sys.argv[0]).name != "openspec":
    raise SystemExit(0)
mode = os.environ.get("ODCLI_FAKE_OPENSPEC", "ok")
args = sys.argv[1:]
if args == ["--version"]:
    print("not-a-version" if mode == "bad-version" else "1.13.2")
    raise SystemExit(0)
if args[:1] == ["validate"]:
    print(json.dumps({"items": [{"valid": mode != "invalid"}]}))
    raise SystemExit(1 if mode == "invalid" else 0)
if args[:1] == ["status"]:
    if mode == "malformed" and os.getcwd().endswith("broken"):
        print("{")
        raise SystemExit(0)
    print(json.dumps({
        "changeName": "demo-change",
        "isPlanningComplete": True,
        "artifacts": [
            {"id": "proposal", "status": "done"},
            {"id": "tasks", "status": "done"},
        ],
        "root": {"path": os.getcwd()},
    }))
    raise SystemExit(0)
if args == ["list", "--json"]:
    total = 0 if mode == "no-tasks" else 4
    completed = 0 if mode == "no-tasks" else 1
    print(json.dumps({
        "changes": [{
            "name": "demo-change",
            "completedTasks": completed,
            "totalTasks": total,
            "status": "in-progress",
        }]
    }))
    raise SystemExit(0)
print("unsupported")
raise SystemExit(2)
"""


@dataclass(frozen=True, slots=True)
class _Target:
    id: uuid.UUID
    name: str
    worktree_path: str


def _install_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tool = bindir / "openspec"
    tool.write_text(_FAKE, encoding="utf-8")
    tool.chmod(0o755)
    log = tmp_path / "log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("ODCLI_FAKE_LOG", str(log))
    monkeypatch.setenv("ODCLI_USER_ROOT", str(tmp_path / "odcli"))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    return log


def _checkout(worktree: Path, target: _Target) -> Command[_Target]:
    step = prepared_step(
        "true",
        ["-C", str(worktree.parent), "worktree", "add", str(worktree), "FEATURE"],
        step_id="checkout.worktree",
        read_only=True,
    )

    def run(context: RunContext[_Target]) -> _Target:
        context.process("checkout.worktree")
        return target

    return Command.from_prepared(
        ExecutionPlan(steps=(step.public_projection(),)).with_fingerprint(),
        prepared_command(run, (step,), executor=SubprocessExecutor()),
    )


def _row(environment_id: str, worktree: Path) -> CheckoutRow:
    return CheckoutRow(
        kind="environment",
        project_id="project",
        environment_id=environment_id,
        name="demo",
        worktree_path=str(worktree),
        odoo_status="stopped",
    )


def test_percentage_is_half_up_and_absent_without_tasks() -> None:
    assert implementation_percent(1, 4) == 25
    assert implementation_percent(0, 0) is None
    with pytest.raises(OpenSpecError, match="outside"):
        implementation_percent(5, 4)


def test_checkout_binding_failure_keeps_the_created_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fakes(tmp_path, monkeypatch)
    monkeypatch.setenv("ODCLI_FAKE_OPENSPEC", "invalid")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    target = _Target(uuid.uuid4(), "demo", str(worktree))
    result = compose_checkout_command(_checkout(worktree, target), "demo-change").run()

    assert result.environment_created is True
    assert result.binding_failed is True
    assert result.worktree_path == str(worktree)
    assert result.reason == "invalid_change"
    assert read_binding(target.id) is None


def test_checkout_binding_records_distinct_planning_and_task_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = _install_fakes(tmp_path, monkeypatch)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    target = _Target(uuid.uuid4(), "demo", str(worktree))
    command = compose_checkout_command(_checkout(worktree, target), "demo-change")
    assert "openspec.checkout.validate" in [step.step_id for step in command.plan.steps]
    result = command.run()

    assert result.binding_failed is False
    binding = read_binding(target.id)
    assert binding is not None
    assert binding.change_id == "demo-change"
    assert binding.version == "1.13.2"
    assert log.read_text(encoding="utf-8").count("validate") == 1
    summary = OpenSpecFactsProvider().collect((_row(str(target.id), worktree),))
    details = dict(summary[str(target.id)].details)
    assert details["planning_complete"] == 2
    assert details["planning_total"] == 2
    assert details["tasks_completed"] == 1
    assert details["tasks_total"] == 4
    assert details["implementation_percent"] == 25


def test_no_tasks_percent_is_null(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fakes(tmp_path, monkeypatch)
    monkeypatch.setenv("ODCLI_FAKE_OPENSPEC", "no-tasks")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    target = _Target(uuid.uuid4(), "demo", str(worktree))
    result = compose_checkout_command(_checkout(worktree, target), "demo-change").run()
    assert result.binding_failed is False
    summary = OpenSpecFactsProvider().collect((_row(str(target.id), worktree),))
    details = dict(summary[str(target.id)].details)
    assert details["implementation_percent"] is None
    assert "no tasks" in summary[str(target.id)].text


def test_incompatible_version_does_not_invent_a_percentage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fakes(tmp_path, monkeypatch)
    monkeypatch.setenv("ODCLI_FAKE_OPENSPEC", "bad-version")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    environment_id = str(uuid.uuid4())
    write_binding(
        OpenSpecBinding(
            schema_version=1,
            environment_id=environment_id,
            change_id="demo-change",
            openspec_root=str(worktree),
            executable="openspec",
            version="1.13.2",
            bound_at="2026-11-15T00:00:00+00:00",
            observation_id="abc",
        )
    )
    summary = OpenSpecFactsProvider().collect((_row(environment_id, worktree),))
    details = dict(summary[environment_id].details)
    assert details["reason"] == "openspec_incompatible"
    assert details["implementation_percent"] is None


def test_one_broken_change_does_not_hide_another_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fakes(tmp_path, monkeypatch)
    healthy = tmp_path / "healthy"
    broken = tmp_path / "broken"
    healthy.mkdir()
    broken.mkdir()
    healthy_id = str(uuid.uuid4())
    broken_id = str(uuid.uuid4())
    for environment_id, worktree in ((healthy_id, healthy), (broken_id, broken)):
        write_binding(
            OpenSpecBinding(
                schema_version=1,
                environment_id=environment_id,
                change_id="demo-change",
                openspec_root=str(worktree),
                executable="openspec",
                version="1.13.2",
                bound_at="2026-11-15T00:00:00+00:00",
                observation_id="abc",
            )
        )
    monkeypatch.setenv("ODCLI_FAKE_OPENSPEC", "malformed")
    summary = OpenSpecFactsProvider().collect((_row(broken_id, broken), _row(healthy_id, healthy)))

    assert summary[healthy_id].state == "available"
    assert dict(summary[broken_id].details)["reason"] == "malformed_json"
    assert set(summary) == {healthy_id, broken_id}


def test_probe_budget_skips_openspec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = _install_fakes(tmp_path, monkeypatch)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    environment_id = str(uuid.uuid4())
    write_binding(
        OpenSpecBinding(
            schema_version=1,
            environment_id=environment_id,
            change_id="demo-change",
            openspec_root=str(worktree),
            executable="openspec",
            version="1.13.2",
            bound_at="2026-11-15T00:00:00+00:00",
            observation_id="abc",
        )
    )
    summary = OpenSpecFactsProvider(budget_seconds=0).collect((_row(environment_id, worktree),))

    assert dict(summary[environment_id].details)["reason"] == "probe_budget_exceeded"
    assert log.read_text(encoding="utf-8") == ""


def test_package_does_not_import_core_internals() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    offenders = [
        str(path)
        for path in root.rglob("*.py")
        if "odoo_instance_sdk.internal" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_dry_run_plan_does_not_write_a_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fakes(tmp_path, monkeypatch)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    target = _Target(uuid.uuid4(), "demo", str(worktree))
    command = compose_checkout_command(_checkout(worktree, target), "demo-change")
    rendered = json.dumps(command.plan.steps, default=str)

    assert "openspec" in rendered
    assert read_binding(target.id) is None
