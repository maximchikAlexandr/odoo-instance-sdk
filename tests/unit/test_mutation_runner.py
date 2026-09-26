from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import run_mutation

pytestmark = pytest.mark.serial


def _executor(responses: list[tuple[int, str]], calls: list[list[str]]) -> Any:
    def execute(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        status, output = responses.pop(0)
        return subprocess.CompletedProcess(command, status, output, "")

    return execute


@pytest.mark.parametrize(
    ("responses", "expected_status"),
    [
        ([(0, "run ok"), (0, "killed: one")], 0),
        ([(0, ""), (0, "")], 0),
    ],
    ids=["success", "empty-child-output"],
)
def test_runner_success_keeps_stage_labelled_report(
    tmp_path: Path, responses: list[tuple[int, str]], expected_status: int
) -> None:
    calls: list[list[str]] = []
    report = tmp_path / "results.txt"
    status = run_mutation.run_audit(
        report=report,
        commands=(("mutation", ("mutmut", "run")), ("results", ("mutmut", "results"))),
        execute=_executor(responses, calls),
    )

    content = report.read_text(encoding="utf-8")
    assert status == expected_status
    assert content.strip()
    assert "=== mutation ===" in content
    assert "=== results ===" in content
    assert len(calls) == 2


def test_runner_preserves_distinctive_failure_and_stops(
    tmp_path: Path,
) -> None:
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
    assert "=== results ===" not in content


def test_runner_preserves_result_collection_failure(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    report = tmp_path / "results.txt"
    status = run_mutation.run_audit(
        report=report,
        commands=(("mutation", ("mutmut", "run")), ("results", ("mutmut", "results"))),
        execute=_executor([(0, "mutants ran"), (91, "results failed")], calls),
    )

    content = report.read_text(encoding="utf-8")
    assert status == 91
    assert len(calls) == 2
    assert "mutants ran" in content
    assert "results failed" in content
    assert "failed with exit code 91" in content
