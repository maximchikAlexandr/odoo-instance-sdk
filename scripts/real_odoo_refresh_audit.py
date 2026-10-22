"""Build the reviewed fixture audit manifest from an actual pinned scanner report."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from scripts.real_odoo_bootstrap import (
    _canonical_package,
    _locked_package_versions,
    _parse_scanner_payload,
)

SCHEMA = "odoo19-python-resolution-audit-v1"
SCANNER = "pip-audit 2.10.1"
FIXED_OVERRIDES = {
    "certifi": "2026.10.19",
    "pillow": "13.0.0",
    "urllib3": "2.8.0",
    "werkzeug": "3.1.9",
}


class AuditRefreshError(ValueError):
    """Raised when the real scanner result cannot be accepted without a waiver."""


def _exception_fields(entry: object) -> tuple[str, str, list[object]]:
    if not isinstance(entry, Mapping):
        raise AuditRefreshError("existing exception entry is malformed")
    package = _canonical_package(entry.get("package"))
    version = entry.get("version")
    advisories = entry.get("advisories")
    if package is None or not isinstance(version, str) or not isinstance(advisories, list):
        raise AuditRefreshError("existing exception identity is malformed")
    return package, version, advisories


def _check_retired_urllib_exception(entries: list[Mapping[str, object]]) -> None:
    if not entries:
        return
    if len(entries) != 1:
        raise AuditRefreshError("expected no more than one existing urllib3 exception entry")
    urllib = entries[0]
    package, version, advisories = _exception_fields(urllib)
    if (
        package != "urllib3"
        or version != "2.6.3"
        or set(advisories) != {"PYSEC-2026-141", "PYSEC-2026-142"}
    ):
        raise AuditRefreshError(
            "urllib3 exception differs from the two explicitly retired findings"
        )


def _expected_findings(entries: object) -> tuple[set[tuple[str, str, str]], list[object]]:
    if not isinstance(entries, list):
        raise AuditRefreshError("existing exceptions are malformed")
    expected: set[tuple[str, str, str]] = set()
    retained: list[object] = []
    urllib_entries: list[Mapping[str, object]] = []
    for entry in entries:
        package, version, advisories = _exception_fields(entry)
        if package == "urllib3":
            urllib_entries.append(entry)
            continue
        retained.append(copy.deepcopy(dict(entry)))
        for advisory in advisories:
            if not isinstance(advisory, str):
                raise AuditRefreshError("existing exception advisory is malformed")
            expected.add((package, version, advisory))
    _check_retired_urllib_exception(urllib_entries)
    return expected, retained


def updated_manifest(
    current: Mapping[str, object], lock_bytes: bytes, scanner_payload: object
) -> tuple[dict[str, object], set[tuple[str, str, str]]]:
    """Update hashes and fixed pins only when scanner findings exactly match existing exceptions."""
    if current.get("schema") != SCHEMA or current.get("scanner") != SCANNER:
        raise AuditRefreshError("current audit schema or scanner pin is unexpected")
    actual_findings = _parse_scanner_payload(scanner_payload)
    if actual_findings is None:
        raise AuditRefreshError("pinned scanner returned an invalid report schema")
    expected_findings, retained_entries = _expected_findings(current.get("exceptions"))
    unexpected = sorted(actual_findings - expected_findings)
    missing = sorted(expected_findings - actual_findings)
    if unexpected or missing:
        raise AuditRefreshError(
            "scanner finding mismatch: "
            f"unexpected_count={len(unexpected)};unexpected={unexpected[:64]};"
            f"missing_count={len(missing)};missing={missing[:64]}"
        )

    locked_versions = _locked_package_versions(lock_bytes)
    if any(locked_versions.get(package) != version for package, version in FIXED_OVERRIDES.items()):
        raise AuditRefreshError(
            "generated fixture lock does not contain all required fixed versions"
        )

    result = copy.deepcopy(dict(current))
    overrides = result.get("reviewed_overrides")
    if not isinstance(overrides, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in overrides.items()
    ):
        raise AuditRefreshError("reviewed override map is malformed")
    overrides.update(FIXED_OVERRIDES)
    result["lock_sha256"] = hashlib.sha256(lock_bytes).hexdigest()
    result["exceptions"] = retained_entries
    return result, actual_findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--scanner-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    current_path = Path("tests/fixtures/real_odoo/odoo19-linux-amd64-py3.12.audit.json")
    current = json.loads(current_path.read_text(encoding="utf-8"))
    lock_bytes = args.lock.read_bytes()
    payload = json.loads(args.scanner_report.read_text(encoding="utf-8"))
    try:
        manifest, findings = updated_manifest(current, lock_bytes, payload)
    except AuditRefreshError as error:
        print(str(error), file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"unexpected_count=0;missing_count=0;deduplicated_findings={len(findings)}")
    print(f"lock_sha256={manifest['lock_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
