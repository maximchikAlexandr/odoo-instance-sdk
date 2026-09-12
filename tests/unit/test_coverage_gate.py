from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.check_coverage import _load_coverage_config, check_coverage
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


def test_developer_workflow_paths_are_covered_by_a_floor() -> None:
    root = Path(__file__).parents[2]
    regexs, thresholds, branch_thresholds = _load_coverage_config(root)
    assert thresholds["developer_workflow"] > 0
    assert branch_thresholds["developer_workflow"] > 0
    pattern = re.compile(regexs["developer_workflow"])
    for path in (
        "odoo_instance_sdk/resources/git.py",
        "odoo_instance_sdk/resources/module.py",
        "odoo_instance_sdk/internal/storage_migration.py",
        "odoo_instance_sdk/commands/translations.py",
    ):
        assert pattern.search(path), path


def _write_developer_workflow_project(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        "[tool.coverage.regexs]\n"
        'developer_workflow = "odoo_instance_sdk/resources/git\\\\.py$"\n'
        "[tool.coverage.thresholds]\n"
        "developer_workflow = 80\n"
        "[tool.coverage.branch_thresholds]\n"
        "developer_workflow = 80\n",
        encoding="utf-8",
    )


def _write_developer_workflow_coverage(
    root: Path,
    *,
    covered_lines: int,
    missing_lines: int,
    covered_branches: int,
    missing_branches: int,
) -> Path:
    coverage_path = root / "coverage.json"
    coverage_path.write_text(
        json.dumps(
            {
                "meta": {"branch_coverage": True},
                "files": {
                    "odoo_instance_sdk/resources/git.py": {
                        "summary": {
                            "covered_lines": covered_lines,
                            "missing_lines": missing_lines,
                            "covered_branches": covered_branches,
                            "missing_branches": missing_branches,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return coverage_path


def test_developer_workflow_statement_floor_rejects_its_regression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_developer_workflow_project(tmp_path)
    coverage_path = _write_developer_workflow_coverage(
        tmp_path, covered_lines=1, missing_lines=99, covered_branches=99, missing_branches=1
    )
    assert check_coverage(coverage_path, tmp_path) == 1
    assert "developer_workflow below threshold" in capsys.readouterr().err


def test_developer_workflow_branch_floor_rejects_its_regression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_developer_workflow_project(tmp_path)
    coverage_path = _write_developer_workflow_coverage(
        tmp_path, covered_lines=99, missing_lines=1, covered_branches=1, missing_branches=99
    )
    assert check_coverage(coverage_path, tmp_path) == 1
    assert "developer_workflow branches below threshold" in capsys.readouterr().err
