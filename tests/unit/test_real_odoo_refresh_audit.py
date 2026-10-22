from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from scripts.real_odoo_refresh_audit import AuditRefreshError, updated_manifest

AUDIT_PATH = Path("tests/fixtures/real_odoo/odoo19-linux-amd64-py3.12.audit.json")
LOCK_PATH = Path("tests/fixtures/real_odoo/odoo19-linux-amd64-py3.12.lock")
FIXED_VERSIONS = {
    "certifi": "2026.10.19",
    "pillow": "13.0.0",
    "urllib3": "2.8.0",
    "werkzeug": "3.1.9",
}


def _refreshed_lock() -> bytes:
    content = LOCK_PATH.read_bytes()
    for package, version in FIXED_VERSIONS.items():
        old_version = {
            "certifi": b"2026.7.22",
            "pillow": b"12.3.0",
            "urllib3": b"2.6.3",
            "werkzeug": b"3.1.6",
        }[package]
        old_pin = package.encode() + b"==" + old_version
        new_pin = package.encode() + b"==" + version.encode()
        content = content.replace(old_pin, new_pin)
    return content


def _scanner_payload(*, add_unapproved: bool = False) -> dict[str, object]:
    manifest = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    dependencies: dict[tuple[str, str], list[dict[str, object]]] = {}
    for exception in manifest["exceptions"]:
        if exception["package"] == "urllib3":
            continue
        key = (exception["package"], exception["version"])
        dependencies[key] = [
            {"id": advisory, "fix_versions": []} for advisory in exception["advisories"]
        ]
    if add_unapproved:
        dependencies[("pillow", FIXED_VERSIONS["pillow"])] = [
            {"id": "CVE-2026-114501", "fix_versions": []}
        ]
    return {
        "dependencies": [
            {"name": package, "version": version, "vulns": vulns}
            for (package, version), vulns in dependencies.items()
        ],
        "fixes": [],
    }


def test_refresh_audit_updates_only_requested_overrides_and_existing_hashes() -> None:
    current = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    manifest, findings = updated_manifest(current, _refreshed_lock(), _scanner_payload())

    reviewed_overrides = cast("dict[str, str]", manifest["reviewed_overrides"])
    assert {name: reviewed_overrides[name] for name in FIXED_VERSIONS} == FIXED_VERSIONS
    current_exceptions = cast(list[dict[str, object]], current["exceptions"])
    assert manifest["exceptions"] == [
        item for item in current_exceptions if item["package"] != "urllib3"
    ]
    assert len(findings) == sum(len(item["advisories"]) for item in manifest["exceptions"])
    assert manifest["lock_sha256"] != current["lock_sha256"]


def test_refresh_audit_fails_closed_on_unapproved_residual_advisory() -> None:
    current = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))

    with pytest.raises(AuditRefreshError, match="CVE-2026-114501"):
        updated_manifest(current, _refreshed_lock(), _scanner_payload(add_unapproved=True))
