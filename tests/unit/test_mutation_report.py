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


def _write_config(directory: Path, targets: list[str]) -> Path:
    for target in set(targets):
        if isinstance(target, str) and target.startswith("src/") and target.endswith(".py"):
            path = directory / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n", encoding="utf-8")
    config = directory / "pyproject.toml"
    config.write_text(
        "[tool.mutmut]\nonly_mutate = " + json.dumps(targets) + "\n", encoding="utf-8"
    )
    return config


def test_matrix_is_exactly_the_canonical_target_set() -> None:
    assert mutation_report.matrix()["include"] == [
        {
            "target": "src/odoo_instance_sdk/internal/redact.py",
            "shard": "src-odoo_instance_sdk-internal-redact.py",
            "filter": "odoo_instance_sdk.internal.redact*",
        },
        {
            "target": "src/odoo_instance_sdk/internal/sanitize.py",
            "shard": "src-odoo_instance_sdk-internal-sanitize.py",
            "filter": "odoo_instance_sdk.internal.sanitize*",
        },
        {
            "target": "src/odoo_instance_sdk/internal/db_name.py",
            "shard": "src-odoo_instance_sdk-internal-db_name.py",
            "filter": "odoo_instance_sdk.internal.db_name*",
        },
        {
            "target": "src/odoo_instance_sdk/internal/urls.py",
            "shard": "src-odoo_instance_sdk-internal-urls.py",
            "filter": "odoo_instance_sdk.internal.urls*",
        },
        {
            "target": "src/odoo_instance_sdk/internal/address.py",
            "shard": "src-odoo_instance_sdk-internal-address.py",
            "filter": "odoo_instance_sdk.internal.address*",
        },
    ]


@pytest.mark.parametrize(
    ("targets", "message"),
    [
        (["src/example.py", "src/example.py"], "duplicate"),
        (["src/missing.py"], "not a repository Python file"),
        (["src/../escape.py"], "invalid mutation target path"),
    ],
)
def test_load_targets_rejects_duplicate_and_invalid_configuration(
    tmp_path: Path, targets: list[str], message: str
) -> None:
    config = _write_config(tmp_path, targets)
    if "src/missing.py" in targets:
        (tmp_path / "src/missing.py").unlink()

    with pytest.raises(mutation_report.MutationReportError, match=message):
        mutation_report.load_targets(config)


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


@pytest.mark.parametrize("baseline", [(5, 0), (6, 1)])
def test_aggregate_accepts_baseline_equality_or_improvement(
    tmp_path: Path, baseline: tuple[int, int]
) -> None:
    _write_shards(tmp_path, mutation_report.Counts(2, 1, 1, 0, 0, 0))
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"survived": baseline[0], "timeout": baseline[1]}), encoding="utf-8"
    )

    summary = tmp_path / "summary.txt"
    assert (
        mutation_report.aggregate(
            report_dir=tmp_path, summary_path=summary, baseline_path=baseline_path
        )
        == 0
    )
    contents = summary.read_text(encoding="utf-8")
    assert f"baseline survived: {baseline[0]}" in contents
    assert f"baseline timeout: {baseline[1]}" in contents


def test_aggregate_rejects_timeout_regression(tmp_path: Path) -> None:
    _write_shards(tmp_path, mutation_report.Counts(2, 1, 0, 1, 0, 0))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"survived": 0, "timeout": 0}), encoding="utf-8")
    summary = tmp_path / "summary.txt"

    with pytest.raises(
        mutation_report.MutationReportError,
        match=r"mutation baseline regression: timeout 5 > baseline 0",
    ):
        mutation_report.aggregate(
            report_dir=tmp_path,
            summary_path=summary,
            baseline_path=baseline,
        )
    assert "timeout 5 > baseline 0" in summary.read_text(encoding="utf-8")


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


@pytest.mark.parametrize("bad_kind", ["empty", "malformed", "not checked"])
def test_aggregate_rejects_invalid_shard_report(tmp_path: Path, bad_kind: str) -> None:
    _write_shards(tmp_path, mutation_report.Counts(2, 1, 1, 0, 0, 0))
    report = tmp_path / "shard-0.txt"
    target = mutation_report.load_targets()[0]
    if bad_kind == "empty":
        report.write_text("", encoding="utf-8")
    elif bad_kind == "malformed":
        report.write_text(
            mutation_report.render_report(
                target, "raw", mutation_report.Counts(2, 1, 1, 0, 0, 0)
            ).replace("suspicious=0\n", ""),
            encoding="utf-8",
        )
    else:
        report.write_text(
            mutation_report.render_report(target, "raw", mutation_report.Counts(2, 1, 0, 0, 0, 1)),
            encoding="utf-8",
        )

    with pytest.raises(
        mutation_report.MutationReportError, match=r"missing complete|incomplete|fields mismatch"
    ):
        mutation_report.aggregate(
            report_dir=tmp_path,
            summary_path=tmp_path / "summary.txt",
            baseline_path=tmp_path / "missing-baseline.json",
        )
