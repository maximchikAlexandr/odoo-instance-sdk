"""Canonical mutation targets, shard reports, and aggregate validation."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
CONFIG: Final = ROOT / "pyproject.toml"
BASELINE: Final = ROOT / ".github" / "mutation-baseline.json"
REPORT_FIELDS: Final = ("total", "killed", "survived", "timeout", "suspicious", "not checked")
TERMINAL_FIELDS: Final = REPORT_FIELDS[1:5]
KNOWN_STATUSES: Final = frozenset(
    (
        *TERMINAL_FIELDS,
        "not checked",
        "no tests",
        "skipped",
        "check was interrupted by user",
        "caught by type check",
        "segfault",
    )
)
STATUS_LINE: Final = re.compile(r"^\s*(?P<name>[^\s:]+):\s*(?P<status>[a-z ]+)\s*$")
VALUE_LINE: Final = re.compile(
    r"^\s*(?P<key>total|killed|survived|timeout|suspicious|not checked)\s*(?::|=)\s*(?P<value>\d+)\s*$"
)
REPORT_START: Final = "=== mutation report ==="
REPORT_END: Final = "=== end mutation report ==="


class MutationReportError(ValueError):
    """Raised when mutation configuration or evidence is not fail-closed."""


@dataclass(frozen=True)
class Target:
    path: str
    module: str
    shard: str

    @property
    def filter(self) -> str:
        return f"{self.module}*"


@dataclass(frozen=True)
class Counts:
    total: int
    killed: int
    survived: int
    timeout: int
    suspicious: int
    not_checked: int

    def as_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "killed": self.killed,
            "survived": self.survived,
            "timeout": self.timeout,
            "suspicious": self.suspicious,
            "not checked": self.not_checked,
        }


def _fail(message: str) -> MutationReportError:
    return MutationReportError(message)


def _target_from_path(path: str, *, config_path: Path) -> Target:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.suffix != ".py":
        raise _fail(f"invalid mutation target path: {path!r}")
    if not path.startswith("src/") or not (config_path.parent / candidate).is_file():
        raise _fail(f"mutation target is not a repository Python file: {path!r}")
    relative = path.removeprefix("src/").removesuffix(".py")
    module = relative.replace("/", ".")
    shard = re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-").replace("/", "-")
    if not shard:
        raise _fail(f"mutation target has no stable shard id: {path!r}")
    return Target(path, module, shard)


def load_targets(config_path: Path = CONFIG) -> tuple[Target, ...]:
    """Read and validate the sole canonical target list from pyproject.toml."""
    try:
        document = tomllib.loads(config_path.read_text(encoding="utf-8"))
        raw_targets = document["tool"]["mutmut"]["only_mutate"]
    except (KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise _fail(f"cannot read [tool.mutmut].only_mutate: {exc}") from exc
    if not isinstance(raw_targets, list) or not raw_targets:
        raise _fail("[tool.mutmut].only_mutate must be a non-empty list")
    if any(not isinstance(value, str) or not value for value in raw_targets):
        raise _fail("mutation targets must be non-empty strings")
    if len(set(raw_targets)) != len(raw_targets):
        raise _fail("[tool.mutmut].only_mutate contains duplicate targets")
    targets = tuple(_target_from_path(value, config_path=config_path) for value in raw_targets)
    if len({target.shard for target in targets}) != len(targets):
        raise _fail("mutation targets produce duplicate shard ids")
    return targets


def resolve_target(value: str, config_path: Path = CONFIG) -> Target:
    targets = load_targets(config_path)
    matches = tuple(target for target in targets if target.path == value)
    if len(matches) != 1:
        raise _fail(f"unknown or ambiguous mutation target: {value!r}")
    return matches[0]


def matrix(config_path: Path = CONFIG) -> dict[str, list[dict[str, str]]]:
    return {
        "include": [
            {"target": target.path, "shard": target.shard, "filter": target.filter}
            for target in load_targets(config_path)
        ]
    }


def _validate_counts(values: dict[str, int]) -> Counts:
    if set(values) != set(REPORT_FIELDS):
        missing = sorted(set(REPORT_FIELDS) - set(values))
        extra = sorted(set(values) - set(REPORT_FIELDS))
        raise _fail(f"mutation report fields mismatch; missing={missing}, extra={extra}")
    if any(value < 0 for value in values.values()):
        raise _fail("mutation report counts must be non-negative")
    total = values["total"]
    if total <= 0:
        raise _fail("mutation report total must be positive")
    if sum(values[field] for field in REPORT_FIELDS[1:]) != total:
        raise _fail("mutation report counts do not reconcile with total")
    return Counts(
        total=total,
        killed=values["killed"],
        survived=values["survived"],
        timeout=values["timeout"],
        suspicious=values["suspicious"],
        not_checked=values["not checked"],
    )


def _status_counts(statuses: list[str]) -> Counts:
    values = dict.fromkeys(REPORT_FIELDS, 0)
    values["total"] = len(statuses)
    for status in statuses:
        if status not in KNOWN_STATUSES:
            raise _fail(f"unsupported mutation status: {status!r}")
        if status in TERMINAL_FIELDS:
            values[status] += 1
        else:
            values["not checked"] += 1
    return _validate_counts(values)


def _machine_block(text: str) -> tuple[str | None, Counts | None]:
    if REPORT_START not in text:
        return None, None
    block = text.split(REPORT_START, 1)[1].split(REPORT_END, 1)[0]
    target: str | None = None
    values: dict[str, int] = {}
    for line in block.splitlines():
        if line.startswith("target="):
            target = line.removeprefix("target=").strip()
            continue
        match = VALUE_LINE.match(line)
        if match:
            key = match.group("key")
            if key in values:
                raise _fail(f"duplicate mutation report field: {key}")
            values[key] = int(match.group("value"))
    if target is None or not values:
        raise _fail("incomplete mutation report block")
    return target, _validate_counts(values)


def _raw_results(text: str, target: Target | None) -> tuple[dict[str, int], list[str]]:
    values: dict[str, int] = {}
    statuses: list[str] = []
    expected_prefix = f"{target.module}." if target else None
    for line in text.splitlines():
        value_match = VALUE_LINE.match(line)
        if value_match:
            key = value_match.group("key")
            if key in values:
                raise _fail(f"duplicate mutation result field: {key}")
            values[key] = int(value_match.group("value"))
            continue
        status_match = STATUS_LINE.match(line)
        if not status_match:
            continue
        name = status_match.group("name")
        status = status_match.group("status")
        if expected_prefix is None or name.startswith(expected_prefix) or name == target.module:
            statuses.append(status)
    return values, statuses


def parse_results(text: str, target: Target | None = None) -> Counts:
    """Parse mutmut's per-mutant result lines or a strict count summary."""
    machine_target, machine_counts = _machine_block(text)
    if machine_counts is not None:
        if target is not None and machine_target != target.path:
            raise _fail(f"report target {machine_target!r} does not match {target.path!r}")
        return machine_counts
    values, statuses = _raw_results(text, target)
    if values:
        if len(values) != len(REPORT_FIELDS):
            raise _fail("mutation count summary is missing one or more required fields")
        counts = _validate_counts(values)
        if statuses and counts != _status_counts(statuses):
            raise _fail("mutation count summary disagrees with per-mutant statuses")
        return counts
    if not statuses:
        raise _fail("mutation results contain no classified mutants")
    return _status_counts(statuses)


def render_report(target: Target | None, raw_output: str, counts: Counts) -> str:
    target_path = target.path if target else "full-scope"
    shard = target.shard if target else "full-scope"
    lines = [
        REPORT_START,
        f"target={target_path}",
        f"shard={shard}",
        *(f"{key}={value}" for key, value in counts.as_dict().items()),
        REPORT_END,
        "",
        "=== mutmut results ===",
        raw_output.rstrip(),
        "",
    ]
    return "\n".join(lines)


def _report_files(report_dir: Path, *, exclude: Path | None = None) -> list[Path]:
    excluded = exclude.resolve() if exclude else None
    return sorted(
        path for path in report_dir.rglob("*.txt") if path.is_file() and path.resolve() != excluded
    )


def _baseline(path: Path) -> tuple[int, int]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        counts = document.get("counts", document)
        survived = counts["survived"]
        timeout = counts["timeout"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _fail(f"invalid mutation baseline: {path}: {exc}") from exc
    if not isinstance(survived, int) or not isinstance(timeout, int) or survived < 0 or timeout < 0:
        raise _fail(f"invalid mutation baseline: {path}: counts must be non-negative integers")
    return survived, timeout


def _summary(
    targets: tuple[Target, ...], reports: dict[str, Counts], baseline: tuple[int, int]
) -> str:
    totals = {
        field: sum(report.as_dict()[field] for report in reports.values())
        for field in REPORT_FIELDS
    }
    lines = ["Mutation aggregate report", ""]
    for target in targets:
        counts = reports[target.path]
        values = counts.as_dict()
        lines.append(f"{target.path} ({target.shard})")
        lines.extend(f"  {field}: {values[field]}" for field in REPORT_FIELDS)
    lines.extend(["", "TOTAL"])
    lines.extend(f"{field}: {totals[field]}" for field in REPORT_FIELDS)
    lines.extend(["", f"baseline survived: {baseline[0]}", f"baseline timeout: {baseline[1]}"])
    lines.extend(
        [f"current survived: {totals['survived']}", f"current timeout: {totals['timeout']}"]
    )
    return "\n".join(lines) + "\n"


def _collect_reports(
    report_dir: Path, summary_path: Path, target_by_path: dict[str, Target]
) -> tuple[dict[str, Counts], list[str]]:
    reports: dict[str, Counts] = {}
    failures: list[str] = []
    paths = _report_files(report_dir, exclude=summary_path) if report_dir.is_dir() else []
    for path in paths:
        try:
            target_path, counts = _machine_block(path.read_text(encoding="utf-8"))
            if target_path is None or counts is None:
                raise _fail("missing complete mutation report block")
            if target_path == "full-scope" or target_path not in target_by_path:
                raise _fail(f"unknown mutation report target: {target_path!r}")
            if counts.not_checked:
                raise _fail(f"incomplete mutation report: not checked={counts.not_checked}")
            if target_path in reports:
                raise _fail(f"duplicate mutation report target: {target_path!r}")
            reports[target_path] = counts
        except (OSError, MutationReportError) as exc:
            failures.append(f"{path}: {exc}")
    return reports, failures


def _baseline_regressions(reports: dict[str, Counts], baseline: tuple[int, int]) -> list[str]:
    totals = {
        field: sum(report.as_dict()[field] for report in reports.values())
        for field in REPORT_FIELDS
    }
    regressions = []
    if totals["survived"] > baseline[0]:
        regressions.append(f"survived {totals['survived']} > baseline {baseline[0]}")
    if totals["timeout"] > baseline[1]:
        regressions.append(f"timeout {totals['timeout']} > baseline {baseline[1]}")
    return regressions


def aggregate(
    *,
    report_dir: Path,
    summary_path: Path,
    baseline_path: Path = BASELINE,
    config_path: Path = CONFIG,
) -> int:
    targets = load_targets(config_path)
    target_by_path = {target.path: target for target in targets}
    reports, failures = _collect_reports(report_dir, summary_path, target_by_path)
    missing = [target.path for target in targets if target.path not in reports]
    if missing:
        failures.append(f"missing mutation reports: {', '.join(missing)}")
    baseline: tuple[int, int] | None = None
    if not failures:
        try:
            baseline = _baseline(baseline_path)
            summary = _summary(targets, reports, baseline)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(summary, encoding="utf-8")
            failures.extend(
                f"mutation baseline regression: {item}"
                for item in _baseline_regressions(reports, baseline)
            )
        except MutationReportError as exc:
            failures.append(str(exc))
    if failures:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        existing = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
        prefix = existing + ("\n" if existing and not existing.endswith("\n") else "")
        summary_path.write_text(
            prefix
            + "Mutation aggregate validation failed\n"
            + "\n".join(f"- {item}" for item in failures)
            + "\n",
            encoding="utf-8",
        )
        raise _fail("; ".join(failures))
    return 0


def _cli() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    matrix_parser = subparsers.add_parser("matrix")
    matrix_parser.add_argument("--config", type=Path, default=CONFIG)
    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("--reports-dir", type=Path, required=True)
    aggregate_parser.add_argument("--summary", type=Path, required=True)
    aggregate_parser.add_argument("--baseline", type=Path, default=BASELINE)
    aggregate_parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()
    try:
        if args.command == "matrix":
            print(json.dumps(matrix(args.config), separators=(",", ":")))
        else:
            aggregate(
                report_dir=args.reports_dir,
                summary_path=args.summary,
                baseline_path=args.baseline,
                config_path=args.config,
            )
    except MutationReportError as exc:
        print(f"mutation report error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
