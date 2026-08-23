from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_coverage import check_coverage
from tests.cases.coverage import COVERAGE_GATE_CASES, CoverageGateCase


def _write_project(root: Path, *, statement: int = 80, branch: int = 60) -> None:
    (root / "pyproject.toml").write_text(
        "\n".join(
            (
                "[tool.coverage.regexs]",
                'critical = "critical\\\\.py$"',
                "[tool.coverage.thresholds]",
                f"critical = {statement}",
                "[tool.coverage.branch_thresholds]",
                f"critical = {branch}",
            )
        ),
        encoding="utf-8",
    )


def _write_coverage(root: Path, case: CoverageGateCase) -> Path:
    summary: dict[str, int] = {
        "covered_lines": 8,
        "missing_lines": 2,
        "covered_branches": 3,
        "missing_branches": 1,
    }
    if case.omit_missing_branches:
        del summary["missing_branches"]
    coverage_path = root / "coverage.json"
    coverage_path.write_text(
        json.dumps(
            {
                "meta": {}
                if case.branch_coverage is None
                else {"branch_coverage": case.branch_coverage},
                "files": {case.path: {"summary": summary}},
            }
        ),
        encoding="utf-8",
    )
    return coverage_path


@pytest.mark.parametrize("case", COVERAGE_GATE_CASES, ids=lambda case: case.id)
def test_coverage_gate(tmp_path: Path, case: CoverageGateCase) -> None:
    _write_project(tmp_path, statement=case.statement_threshold, branch=case.branch_threshold)
    assert check_coverage(_write_coverage(tmp_path, case), tmp_path) == case.expected_exit_code
