from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import UnplannedStepError
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    RunContext,
    prepared_command,
)
from odoo_instance_sdk.resources.git import GitResource


def test_git_resource_keeps_all_mutations_behind_captured_steps() -> None:
    source_path = Path(GitResource.__module__.replace(".", "/") + ".py")
    source = Path(__file__).parents[2] / "src" / source_path
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "subprocess" not in calls
    assert "_run_probe" not in source.read_text(encoding="utf-8")
    assert "_script_step" not in source.read_text(encoding="utf-8")
    assert '"sh", "-c"' not in source.read_text(encoding="utf-8")
    assert "process_composed" not in Path(__file__).parents[2].joinpath(
        "src/odoo_instance_sdk/internal/proc/__init__.py"
    ).read_text(encoding="utf-8")


def test_git_public_operations_are_present() -> None:
    names = {name for name in dir(GitResource) if not name.startswith("_")}
    assert {
        "commit_context",
        "commit_context_command",
        "commit",
        "commit_command",
        "check",
        "check_command",
        "absorb",
        "absorb_command",
        "sync",
        "sync_command",
    } <= names


def test_git_process_boundary_rejects_same_id_substitution() -> None:
    step = PreparedStep(step_id="git.probe", argv=("git", "status"), cwd="/captured")
    result = ProcessResult(step.argv, 0, "", "", 0.0, step.cwd, ())
    executor = RecordingExecutor(results={step.step_id: result})

    def callback(context: RunContext[object]) -> None:
        with pytest.raises(UnplannedStepError):
            context.process_prepared(replace(step, argv=("git", "commit")))
        context.skip(step.step_id)

    prepared_command(callback, (step,), executor=executor).run()
    assert executor.executed == []
