from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import mutation_report


def _target() -> mutation_report.Target:
    return mutation_report.load_targets()[0]


def _result(target: mutation_report.Target, statuses: list[str]) -> str:
    return "\n".join(
        f"    {target.module}.mutant_{index}: {status}" for index, status in enumerate(statuses)
    )


def _write_shards(directory: Path, counts: mutation_report.Counts) -> None:
    for index, target in enumerate(mutation_report.load_targets()):
        (directory / f"shard-{index}.txt").write_text(
            mutation_report.render_report(target, "raw result", counts), encoding="utf-8"
        )


def test_matrix_is_exactly_the_canonical_target_set() -> None:
    targets = mutation_report.load_targets()
    generated = mutation_report.matrix()["include"]

    assert [entry["target"] for entry in generated] == [target.path for target in targets]
    assert len({entry["shard"] for entry in generated}) == len(targets)
    assert [entry["filter"] for entry in generated] == [target.filter for target in targets]


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["killed", "survived", "timeout", "suspicious"], (4, 1, 1, 1, 1, 0)),
        (["killed", "killed"], (2, 2, 0, 0, 0, 0)),
        (["no tests"], (1, 0, 0, 0, 0, 1)),
    ],
)
def test_parse_mutmut_result_lines(statuses: list[str], expected: tuple[int, ...]) -> None:
    counts = mutation_report.parse_results(_result(_target(), statuses), _target())
    assert (
        counts.total,
        counts.killed,
        counts.survived,
        counts.timeout,
        counts.suspicious,
        counts.not_checked,
    ) == expected


def test_parse_count_summary_and_reject_bad_arithmetic() -> None:
    text = "total: 3\nkilled: 2\nsurvived: 1\ntimeout: 0\nsuspicious: 0\nnot checked: 0\n"
    assert mutation_report.parse_results(text).total == 3

    with pytest.raises(mutation_report.MutationReportError, match="reconcile"):
        mutation_report.parse_results(text.replace("killed: 2", "killed: 1"))


def test_parse_rejects_empty_and_incomplete_results() -> None:
    with pytest.raises(mutation_report.MutationReportError, match="no classified"):
        mutation_report.parse_results("mutmut produced no output")
    incomplete = mutation_report.parse_results(
        _result(_target(), ["killed", "not checked"]), _target()
    )
    assert incomplete.not_checked == 1


def test_aggregate_is_deterministic_and_retains_summary_on_regression(tmp_path: Path) -> None:
    _write_shards(tmp_path, mutation_report.Counts(2, 1, 1, 0, 0, 0))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"survived": 5, "timeout": 0}), encoding="utf-8")
    summary = tmp_path / "summary.txt"

    assert (
        mutation_report.aggregate(report_dir=tmp_path, summary_path=summary, baseline_path=baseline)
        == 0
    )
    first = summary.read_text(encoding="utf-8")
    assert first.index("src/odoo_instance_sdk/internal/redact.py") < first.index(
        "src/odoo_instance_sdk/internal/sanitize.py"
    )
    assert "TOTAL" in first

    baseline.write_text(json.dumps({"survived": 4, "timeout": 0}), encoding="utf-8")
    with pytest.raises(mutation_report.MutationReportError, match="baseline regression"):
        mutation_report.aggregate(report_dir=tmp_path, summary_path=summary, baseline_path=baseline)
    assert "baseline regression" in summary.read_text(encoding="utf-8")


@pytest.mark.parametrize("bad_kind", ["missing", "duplicate", "unknown"])
def test_aggregate_rejects_incomplete_coverage(tmp_path: Path, bad_kind: str) -> None:
    targets = mutation_report.load_targets()
    if bad_kind != "missing":
        (tmp_path / "one.txt").write_text(
            mutation_report.render_report(
                targets[0], "raw", mutation_report.Counts(1, 1, 0, 0, 0, 0)
            ),
            encoding="utf-8",
        )
    if bad_kind == "duplicate":
        (tmp_path / "two.txt").write_text(
            mutation_report.render_report(
                targets[0], "raw", mutation_report.Counts(1, 1, 0, 0, 0, 0)
            ),
            encoding="utf-8",
        )
    if bad_kind == "unknown":
        (tmp_path / "unknown.txt").write_text(
            "=== mutation report ===\ntarget=src/unknown.py\ntotal=1\nkilled=1\nsurvived=0\ntimeout=0\nsuspicious=0\nnot checked=0\n=== end mutation report ===\n",
            encoding="utf-8",
        )
    with pytest.raises(mutation_report.MutationReportError, match=r"missing|duplicate|unknown"):
        mutation_report.aggregate(
            report_dir=tmp_path,
            summary_path=tmp_path / "summary.txt",
            baseline_path=tmp_path / "missing-baseline.json",
        )
