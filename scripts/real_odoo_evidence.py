#!/usr/bin/env python3
"""Sanitize, bound, scan, and package real-Odoo CI evidence."""

from __future__ import annotations

import argparse
import json
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal

from odoo_instance_sdk.internal.sanitize import sanitize_last_error, sanitize_terminal_text
from tests.integration.real_odoo.pins import PhaseBudget, budget_for

Status = Literal["success", "failure"]
TEXT_LIMIT_BYTES: Final[int] = 2 * 1024 * 1024
SUCCESS_LIMIT_BYTES: Final[int] = 2 * 1024 * 1024
FAILURE_BUNDLE_LIMIT_BYTES: Final[int] = 50 * 1024 * 1024
RETENTION_DAYS: Final[int] = 7
TEXT_SUFFIXES = frozenset({".json", ".log", ".txt", ".xml", ".md", ".yml", ".yaml"})
REQUIRED_SUCCESS = frozenset(
    {"bootstrap.json", "junit.xml", "resource-manifest.json", "timing.json"}
)
REQUIRED_FAILURE = REQUIRED_SUCCESS | frozenset({"compose.log", "odoo.log", "postgres.log"})


def _bounded_text(path: Path) -> bytes:
    text = sanitize_terminal_text(
        sanitize_last_error(path.read_text(encoding="utf-8", errors="replace")) or "",
        preserve_newlines=True,
    )
    return text.encode("utf-8")[:TEXT_LIMIT_BYTES]


def _copy_bounded(source: Path, destination: Path, canary: bytes) -> None:
    if source.is_symlink() or not source.is_file():
        return
    if source.suffix.lower() in TEXT_SUFFIXES:
        if source.stat().st_size > TEXT_LIMIT_BYTES:
            raise ValueError(f"text evidence input exceeds {TEXT_LIMIT_BYTES} bytes: {source.name}")
        content = _bounded_text(source)
    else:
        content = source.read_bytes()
    if canary and canary in content:
        raise ValueError("secret canary detected in evidence")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.write_bytes(content)
    destination.chmod(0o600)


def _include(source: Path, status: Status) -> bool:
    if not source.is_file():
        return False
    if source.suffix.lower() not in TEXT_SUFFIXES:
        return False
    if status == "failure":
        return True
    name = source.name.lower()
    return any(
        token in name
        for token in ("bootstrap", "junit", "timing", "phase", "pin", "resource", "manifest")
    )


def _write_packaging_error(output: Path, message: str) -> None:
    output.unlink(missing_ok=True)
    error_path = output.with_name("packaging-error.json")
    error_path.write_text(
        json.dumps(
            {
                "schema": "odcli-real-odoo-evidence-v1",
                "ok": False,
                "error": sanitize_last_error(message) or "evidence packaging failed",
                "retention_days": RETENTION_DAYS,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    error_path.chmod(0o600)


def _check_bundle_size(files: tuple[Path, ...], status: Status) -> None:
    total = sum(path.stat().st_size for path in files)
    limit = SUCCESS_LIMIT_BYTES if status == "success" else FAILURE_BUNDLE_LIMIT_BYTES
    if total > limit:
        raise ValueError(f"evidence input exceeds {limit} bytes")


def _check_archive_size(output: Path, status: Status) -> None:
    limit = SUCCESS_LIMIT_BYTES if status == "success" else FAILURE_BUNDLE_LIMIT_BYTES
    if output.stat().st_size > limit:
        raise ValueError(f"evidence bundle exceeds {limit} bytes")


def _archive_staging(staging: Path, output: Path, status: Status) -> None:
    output.unlink(missing_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for path in staging.rglob("*"):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(staging))
    _check_archive_size(output, status)


def _read_canary(canary_file: Path | None) -> bytes:
    canary = canary_file.read_bytes().strip() if canary_file is not None else b""
    if canary_file is not None and len(canary) < 8:
        raise ValueError("secret canary file is missing or too short")
    return canary


def _required_files(source: Path, status: Status) -> None:
    required = REQUIRED_SUCCESS if status == "success" else REQUIRED_FAILURE
    missing = sorted(
        name
        for name in required
        if not (source / name).is_file()
        or (source / name).is_symlink()
        or (source / name).stat().st_size == 0
    )
    if missing:
        raise ValueError(f"missing evidence contract files: {', '.join(missing)}")


def _load_json(path: Path, label: str) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _validate_bootstrap(source: Path) -> dict[str, object]:
    manifest = _load_json(source / "bootstrap.json", "bootstrap manifest")
    if manifest.get("ok") is not True:
        raise ValueError("evidence requires successful bootstrap")
    platform = manifest.get("platform")
    architecture = manifest.get("architecture")
    cache = manifest.get("cache")
    pins = manifest.get("pins")
    if not isinstance(platform, str) or not isinstance(architecture, str):
        raise ValueError("bootstrap manifest lacks platform identity")  # noqa: TRY004
    if not isinstance(cache, dict) or not isinstance(pins, dict) or not pins:
        raise ValueError("bootstrap manifest lacks cache or pin identity")
    if cache.get("class") not in {"cold", "warm"}:
        raise ValueError("bootstrap manifest lacks cache class")
    if not isinstance(cache.get("source_hit"), bool) or not isinstance(cache.get("uv_hit"), bool):
        raise ValueError("bootstrap manifest lacks individual cache hits")  # noqa: TRY004
    if not isinstance(cache.get("source_key"), str) or not isinstance(cache.get("uv_key"), str):
        raise ValueError("bootstrap manifest lacks cache keys")  # noqa: TRY004
    return manifest


def _junit_counts(junit: Path) -> tuple[int, int]:
    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    if not suites:
        raise ValueError("JUnit has no testsuite element")
    tests = sum(int(suite.get("tests", "0")) for suite in suites)
    failures = sum(
        int(suite.get("failures", "0")) + int(suite.get("errors", "0")) for suite in suites
    )
    return tests, failures


def _validate_junit(junit: Path, status: Status) -> None:
    tests, failures = _junit_counts(junit)
    if tests <= 0:
        raise ValueError("evidence JUnit has no executed tests")
    if status == "success" and failures:
        raise ValueError("successful evidence JUnit reports test failures")
    if status == "failure" and not failures:
        raise ValueError("failure evidence JUnit has no failed tests")


def _validate_resource_manifest(source: Path, tier: str) -> dict[str, object]:
    manifest = _load_json(source / "resource-manifest.json", "resource manifest")
    resources = manifest.get("resources")
    audit = manifest.get("audit")
    if not isinstance(resources, list) or not resources:
        raise ValueError("resource manifest lacks owned resource records")
    if not isinstance(audit, dict) or audit.get("state") != "clean" or audit.get("leaks") != []:
        raise ValueError("resource manifest lacks a clean final leak audit")
    if not isinstance(audit.get("runs"), list) or not audit["runs"]:
        raise ValueError("resource manifest lacks final audit runs")
    if tier == "full" and manifest.get("source_cache_consumed") is not True:
        raise ValueError("full evidence lacks source-cache consumption audit")
    return manifest


def _validate_semantic_contract(
    source: Path, *, status: Status, tier: Literal["smoke", "full"]
) -> tuple[dict[str, object], dict[str, object]]:
    bootstrap = _validate_bootstrap(source)
    _validate_junit(source / "junit.xml", status)
    resource = _validate_resource_manifest(source, tier)
    return bootstrap, resource


def _phase_seconds(timing: Path) -> dict[str, float]:
    value = json.loads(timing.read_text(encoding="utf-8"))
    phases = value.get("phases") if isinstance(value, dict) else None
    if not isinstance(phases, dict):
        raise TypeError("timing manifest has no phases")
    durations: dict[str, float] = {}
    for phase in ("setup", "test", "cleanup"):
        record = phases.get(phase)
        duration = record.get("duration_seconds") if isinstance(record, dict) else None
        if not isinstance(duration, (int, float)) or duration < 0:
            raise ValueError(f"timing manifest is missing completed phase: {phase}")
        durations[phase] = float(duration)
    return durations


def _budget_report(
    timing: Path,
    *,
    status: Status,
    tier: Literal["smoke", "full"],
    cache_class: Literal["cold", "warm"],
    artifact_bytes: int,
) -> dict[str, object]:
    durations = _phase_seconds(timing)
    budget: PhaseBudget = budget_for(tier, cache_class)
    total = sum(durations.values())
    artifact_budget = SUCCESS_LIMIT_BYTES if status == "success" else FAILURE_BUNDLE_LIMIT_BYTES
    return {
        "status": status,
        "tier": tier,
        "cache_class": cache_class,
        "setup_seconds": durations["setup"],
        "test_seconds": durations["test"],
        "cleanup_seconds": durations["cleanup"],
        "job_seconds": total,
        "artifact_bytes": artifact_bytes,
        "setup_budget_seconds": budget.setup_seconds,
        "test_budget_seconds": budget.test_seconds,
        "job_budget_seconds": budget.job_seconds,
        "artifact_budget_bytes": artifact_budget,
        "ok": (
            durations["setup"] <= budget.setup_seconds
            and durations["test"] <= budget.test_seconds
            and total <= budget.job_seconds
            and artifact_bytes <= artifact_budget
        ),
    }


def _write_junit_properties(junit: Path, properties: Mapping[str, object]) -> None:
    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    if not suites:
        raise ValueError("JUnit has no testsuite element")
    for suite in suites:
        existing = suite.find("properties")
        if existing is None:
            existing = ET.SubElement(suite, "properties")
        for child in list(existing):
            existing.remove(child)
        for key, value in sorted(properties.items()):
            ET.SubElement(existing, "property", name=key, value=str(value))
    ET.ElementTree(root).write(junit, encoding="utf-8", xml_declaration=True)
    junit.chmod(0o600)


def _update_metrics(
    source: Path,
    *,
    status: Status,
    timing: Path,
    junit: Path,
    tier: Literal["smoke", "full"],
    cache_class: Literal["cold", "warm"],
    artifact_bytes: int,
) -> dict[str, object]:
    bootstrap, resource = _validate_semantic_contract(source, status=status, tier=tier)
    report = _budget_report(
        timing,
        status=status,
        tier=tier,
        cache_class=cache_class,
        artifact_bytes=artifact_bytes,
    )
    timing_value = json.loads(timing.read_text(encoding="utf-8"))
    if not isinstance(timing_value, dict):
        raise TypeError("invalid timing manifest")
    timing_value["budget"] = report
    timing.write_text(json.dumps(timing_value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    timing.chmod(0o600)
    pins = bootstrap.get("pins", {})
    properties: dict[str, object] = {
        f"budget_{key}": value
        for key, value in report.items()
        if isinstance(value, (str, int, float, bool))
    }
    properties.update(
        {
            f"timing_{key}": value
            for key, value in report.items()
            if key.endswith("_seconds") and isinstance(value, (int, float))
        }
    )
    if isinstance(pins, dict):
        properties.update({f"pin_{key}": value for key, value in pins.items()})
    cache = bootstrap["cache"]
    audit = resource["audit"]
    if not isinstance(cache, dict) or not isinstance(audit, dict):
        raise TypeError("validated evidence identity is malformed")
    properties.update(
        {
            "platform": bootstrap["platform"],
            "architecture": bootstrap["architecture"],
            "cache_class": cache["class"],
            "cache_source_hit": cache["source_hit"],
            "cache_uv_hit": cache["uv_hit"],
            "cache_source_key": cache["source_key"],
            "cache_uv_key": cache["uv_key"],
            "audit_state": audit["state"],
            "artifact_bytes": report["artifact_bytes"],
        }
    )
    _write_junit_properties(junit, properties)
    resource["evidence"] = {
        "platform": bootstrap["platform"],
        "architecture": bootstrap["architecture"],
        "cache": cache,
        "pins": pins,
        "timing": report,
        "artifact_bytes": artifact_bytes,
        "audit_state": audit["state"],
    }
    resource_path = source / "resource-manifest.json"
    resource_path.write_text(
        json.dumps(resource, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    resource_path.chmod(0o600)
    return report


def package_evidence(  # noqa: C901
    source: Path,
    output: Path,
    *,
    status: Status,
    canary_file: Path | None = None,
    tier: Literal["smoke", "full"] | None = None,
    cache_class: Literal["cold", "warm"] | None = None,
) -> dict[str, object]:
    """Create a bounded tar.gz and return its redacted packaging manifest."""
    if status not in {"success", "failure"}:
        raise ValueError(f"unsupported status: {status}")
    try:
        _required_files(source, status)
        if canary_file is None:
            raise ValueError("secret canary file is required")  # noqa: TRY301
        canary = _read_canary(canary_file)
        with tempfile.TemporaryDirectory(prefix="odcli-e2e-evidence-") as temporary:
            staging = Path(temporary) / "evidence"
            for path in sorted(source.rglob("*")):
                relative = path.relative_to(source)
                if relative.parts and any(part == ".git" for part in relative.parts):
                    continue
                if not _include(path, status):
                    continue
                _copy_bounded(path, staging / relative, canary)
            files = tuple(path for path in staging.rglob("*") if path.is_file())
            _check_bundle_size(files, status)
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _archive_staging(staging, output, status)
            if tier is None or cache_class is None:
                raise ValueError(  # noqa: TRY301
                    "tier and cache class are required for budget evidence"
                )
            timing = next((path for path in files if path.name == "timing.json"), None)
            junit = next((path for path in files if path.name == "junit.xml"), None)
            if timing is None or junit is None:
                raise ValueError(  # noqa: TRY301
                    "timing.json and junit.xml are required for budget evidence"
                )
            report = _update_metrics(
                source,
                status=status,
                timing=source / "timing.json",
                junit=source / "junit.xml",
                tier=tier,
                cache_class=cache_class,
                artifact_bytes=output.stat().st_size,
            )
            for _ in range(2):
                _copy_bounded(source / "timing.json", staging / "timing.json", canary)
                _copy_bounded(source / "junit.xml", staging / "junit.xml", canary)
                _copy_bounded(
                    source / "resource-manifest.json",
                    staging / "resource-manifest.json",
                    canary,
                )
                _archive_staging(staging, output, status)
                if report["artifact_bytes"] == output.stat().st_size:
                    break
                report = _update_metrics(
                    source,
                    status=status,
                    timing=source / "timing.json",
                    junit=source / "junit.xml",
                    tier=tier,
                    cache_class=cache_class,
                    artifact_bytes=output.stat().st_size,
                )
            if not bool(report["ok"]):
                raise ValueError("E2E phase or artifact budget exceeded")  # noqa: TRY301
            if report["artifact_bytes"] != output.stat().st_size:
                raise ValueError("artifact size changed while packaging")  # noqa: TRY301
    except (OSError, ET.ParseError, TypeError, UnicodeError, ValueError) as error:
        junit = source / "junit.xml"
        junit.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        junit.write_text(
            '<testsuite tests="0" failures="1"><properties>'
            '<property name="packaging_error" value="redacted"/>'
            "</properties></testsuite>\n",
            encoding="utf-8",
        )
        junit.chmod(0o600)
        _write_packaging_error(output, str(error))
        raise
    manifest = {
        "schema": "odcli-real-odoo-evidence-v1",
        "ok": True,
        "status": status,
        "artifact": output.name,
        "artifact_bytes": output.stat().st_size,
        "text_limit_bytes": TEXT_LIMIT_BYTES,
        "bundle_limit_bytes": FAILURE_BUNDLE_LIMIT_BYTES,
        "success_limit_bytes": SUCCESS_LIMIT_BYTES,
        "retention_days": RETENTION_DAYS,
        "secret_canary": "scanned-not-recorded",
        "budget": report,
    }
    manifest_path = output.with_name("evidence-manifest.json")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path.chmod(0o600)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status", choices=("success", "failure"), required=True)
    parser.add_argument("--canary-file", type=Path)
    parser.add_argument("--tier", choices=("smoke", "full"), required=True)
    parser.add_argument("--cache-class", choices=("cold", "warm"), required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            package_evidence(
                args.source,
                args.output,
                status=args.status,
                canary_file=args.canary_file,
                tier=args.tier,
                cache_class=args.cache_class,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
