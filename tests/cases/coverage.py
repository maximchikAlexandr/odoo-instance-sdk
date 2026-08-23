from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoverageGateCase:
    id: str
    statement_threshold: int = 80
    branch_threshold: int = 60
    path: str = "src/critical.py"
    branch_coverage: bool | None = True
    omit_missing_branches: bool = False
    expected_exit_code: int = 0


COVERAGE_GATE_CASES: tuple[CoverageGateCase, ...] = (
    CoverageGateCase("accepts-statement-and-branch-thresholds"),
    CoverageGateCase(
        "rejects-below-threshold-statement", statement_threshold=90, expected_exit_code=1
    ),
    CoverageGateCase("rejects-below-threshold-branch", branch_threshold=80, expected_exit_code=1),
    CoverageGateCase("rejects-missing-zone", path="src/other.py", expected_exit_code=1),
    CoverageGateCase("rejects-missing-branch-coverage", branch_coverage=None, expected_exit_code=1),
    CoverageGateCase(
        "rejects-disabled-branch-coverage", branch_coverage=False, expected_exit_code=1
    ),
    CoverageGateCase(
        "rejects-missing-branch-metrics", omit_missing_branches=True, expected_exit_code=1
    ),
)
