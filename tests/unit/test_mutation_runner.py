from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import mutation_report, run_mutation

pytestmark = pytest.mark.serial


def _executor(responses: list[tuple[int, str]], calls: list[list[str]]) -> Any:
    def execute(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        status, output = responses.pop(0)
        return subprocess.CompletedProcess(command, status, output, "")

    return execute


def _result(target: mutation_report.Target) -> str:
    return "\n".join(
        (
            f"    {target.module}.first: killed",
            f"    {target.module}.second: survived",
        )
    )


def test_target_runner_uses_matching_filter_and_writes_complete_report(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    target = mutation_report.load_targets()[0]
    report = tmp_path / "results.txt"
    status = run_mutation.run_audit(
        report=report,
        target=target.path,
        commands=(
            ("integration", ("smoke",)),
            ("mutmut run", ("mutmut", "run", target.filter)),
            ("mutmut results", ("mutmut", "results")),
        ),
        execute=_executor([(0, "smoke ok"), (0, "run ok"), (0, _result(target))], calls),
    )

    content = report.read_text(encoding="utf-8")
    assert status == 0
    assert calls[1][-1] == target.filter
    assert "=== mutation report ===" in content
    assert "not checked=0" in content


def test_unknown_target_fails_before_any_child_and_retains_diagnostic(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    report = tmp_path / "results.txt"
    status = run_mutation.run_audit(
        report=report,
        target="src/unknown.py",
        execute=_executor([], calls),
    )

    assert status != 0
    assert calls == []
    assert "target validation failed" in report.read_text(encoding="utf-8")


def test_unfiltered_runner_keeps_exact_child_failure(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    report = tmp_path / "results.txt"
    status = run_mutation.run_audit(
        report=report,
        commands=(
            ("integration", ("smoke",)),
            ("mutation", ("mutmut", "run")),
            ("results", ("mutmut", "results")),
        ),
        execute=_executor([(73, "collection failed")], calls),
    )

    content = report.read_text(encoding="utf-8")
    assert status == 73
    assert len(calls) == 1
    assert "collection failed" in content
    assert "failed with exit code 73" in content
    assert "=== mutation report ===" not in content


def test_target_runner_fails_on_incomplete_results_and_keeps_label(tmp_path: Path) -> None:
    target = mutation_report.load_targets()[0]
    report = tmp_path / "results.txt"
    status = run_mutation.run_audit(
        report=report,
        target=target.path,
        commands=(("mutmut results", ("mutmut", "results")),),
        execute=_executor([(0, f"    {target.module}.one: not checked")], []),
    )

    assert status != 0
    assert "[result completeness] failed" in report.read_text(encoding="utf-8")
