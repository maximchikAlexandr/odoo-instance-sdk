#!/usr/bin/env python3
"""Validate zonal statement and branch coverage against pyproject thresholds."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tomllib
from typing import cast


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def _parse_thresholds(values: dict[str, object], label: str) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for name, value in values.items():
        if isinstance(value, (int, float, str)):
            parsed[str(name)] = float(value)
        else:
            raise SystemExit(f"invalid {label} for {name!r}")
    return parsed


def _load_coverage_config(
    repo_root: pathlib.Path,
) -> tuple[dict[str, str], dict[str, float], dict[str, float]]:
    parsed: object = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise SystemExit("pyproject.toml root must be a table")
    tool = parsed.get("tool")
    coverage = tool.get("coverage") if isinstance(tool, dict) else None
    if not isinstance(coverage, dict):
        raise SystemExit("pyproject.toml missing [tool.coverage]")
    regexs_obj = coverage.get("regexs")
    thresholds_obj = coverage.get("thresholds")
    branch_obj = coverage.get("branch_thresholds")
    if (
        not isinstance(regexs_obj, dict)
        or not isinstance(thresholds_obj, dict)
        or not isinstance(branch_obj, dict)
    ):
        raise SystemExit(
            "pyproject.toml missing [tool.coverage.regexs], [tool.coverage.thresholds], "
            "or [tool.coverage.branch_thresholds]"
        )
    return (
        {str(name): str(pattern) for name, pattern in regexs_obj.items()},
        _parse_thresholds(thresholds_obj, "threshold"),
        _parse_thresholds(branch_obj, "branch threshold"),
    )


def _statement_counts(file_data: dict[str, object]) -> tuple[int, int]:
    summary = file_data.get("summary")
    if not isinstance(summary, dict):
        return 0, 0
    covered = summary.get("covered_lines")
    missing = summary.get("missing_lines")
    if isinstance(covered, int) and isinstance(missing, int):
        return covered, missing
    num_statements = summary.get("num_statements")
    if isinstance(num_statements, int) and isinstance(covered, int):
        return covered, num_statements - covered
    return 0, 0


def _branch_counts(file_data: dict[str, object]) -> tuple[int, int] | None:
    summary = file_data.get("summary")
    if not isinstance(summary, dict):
        return None
    covered = summary.get("covered_branches")
    missing = summary.get("missing_branches")
    if not isinstance(covered, int) or not isinstance(missing, int):
        return None
    return covered, missing


def _zone_percentages(
    coverage_data: dict[str, object],
    zone_regexs: dict[str, str],
    *,
    branch: bool = False,
) -> tuple[dict[str, float], set[str], set[str]]:
    files = coverage_data.get("files")
    if not isinstance(files, dict):
        raise SystemExit("coverage JSON missing files mapping")
    compiled = {zone: re.compile(pattern) for zone, pattern in zone_regexs.items()}
    covered_by_zone = dict.fromkeys(zone_regexs, 0)
    missing_by_zone = dict.fromkeys(zone_regexs, 0)
    matched_zones: set[str] = set()
    missing_metrics: set[str] = set()
    for path, file_data in files.items():
        if not isinstance(file_data, dict):
            continue
        for zone, pattern in compiled.items():
            if pattern.search(str(path)):
                matched_zones.add(zone)
                counts = _branch_counts(file_data) if branch else _statement_counts(file_data)
                if counts is None:
                    missing_metrics.add(zone)
                    continue
                covered, missing = counts
                covered_by_zone[zone] += covered
                missing_by_zone[zone] += missing
    percentages = {
        zone: (
            100.0
            if (covered_by_zone[zone] + missing_by_zone[zone]) == 0
            else (covered_by_zone[zone] / (covered_by_zone[zone] + missing_by_zone[zone])) * 100.0
        )
        for zone in zone_regexs
    }
    return percentages, matched_zones, missing_metrics


def _has_branch_coverage(coverage_data: dict[str, object]) -> bool:
    meta = coverage_data.get("meta")
    return isinstance(meta, dict) and meta.get("branch_coverage") is True


def _fail_zone(zone: str, message: str) -> bool:
    print(f"{zone}: {message}", file=sys.stderr)
    return True


def _check_zone(
    zone: str,
    *,
    percentages: dict[str, float],
    statement_zones: set[str],
    zone_thresholds: dict[str, float],
    zone_branch_thresholds: dict[str, float],
    branch_enabled: bool,
    branch_percentages: dict[str, float],
    branch_zones: set[str],
    missing_branch_metrics: set[str],
) -> bool:
    if zone not in statement_zones:
        return _fail_zone(zone, "no matching coverage files")
    percent = percentages[zone]
    threshold = zone_thresholds.get(zone)
    if threshold is None:
        return _fail_zone(zone, f"{percent:.2f}% (missing threshold)")
    print(f"{zone}: {percent:.2f}%")
    failed = percent < threshold
    if failed:
        print(f"{zone} below threshold {threshold:.2f}% (got {percent:.2f}%)", file=sys.stderr)
    branch_threshold = zone_branch_thresholds.get(zone)
    if branch_threshold is None:
        return _fail_zone(zone, "missing branch threshold")
    if not branch_enabled:
        return _fail_zone(zone, "coverage JSON was not generated with branch coverage")
    if zone in missing_branch_metrics:
        return _fail_zone(zone, "matching coverage file is missing branch metrics")
    if zone not in branch_zones:
        return _fail_zone(zone, "no matching branch coverage files")
    branch_percent = branch_percentages[zone]
    print(f"{zone} branches: {branch_percent:.2f}%")
    if branch_percent < branch_threshold:
        print(
            f"{zone} branches below threshold {branch_threshold:.2f}% (got {branch_percent:.2f}%)",
            file=sys.stderr,
        )
        return True
    return failed


def check_coverage(coverage_path: pathlib.Path, repo_root: pathlib.Path) -> int:
    """Validate zonal coverage and print sorted zone lines.

    Args:
        coverage_path: Path to coverage.py JSON report.
        repo_root: Repository root containing pyproject.toml.

    Returns:
        Exit code 0 on success, 1 when a zone is missing or below threshold.
    """
    zone_regexs, zone_thresholds, zone_branch_thresholds = _load_coverage_config(repo_root)
    parsed: object = json.loads(coverage_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise SystemExit("coverage JSON root must be an object")
    payload = cast("dict[str, object]", parsed)
    percentages, statement_zones, _ = _zone_percentages(payload, zone_regexs)
    branch_percentages, branch_zones, missing_branch_metrics = _zone_percentages(
        payload, zone_regexs, branch=True
    )
    failed = any(
        _check_zone(
            zone,
            percentages=percentages,
            statement_zones=statement_zones,
            zone_thresholds=zone_thresholds,
            zone_branch_thresholds=zone_branch_thresholds,
            branch_enabled=_has_branch_coverage(payload),
            branch_percentages=branch_percentages,
            branch_zones=branch_zones,
            missing_branch_metrics=missing_branch_metrics,
        )
        for zone in sorted(zone_regexs)
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate zonal statement coverage.")
    parser.add_argument("--coverage-json", required=True, type=pathlib.Path)
    namespace = parser.parse_args(argv)
    return check_coverage(cast("pathlib.Path", namespace.coverage_json).resolve(), _repo_root())


if __name__ == "__main__":
    raise SystemExit(main())
