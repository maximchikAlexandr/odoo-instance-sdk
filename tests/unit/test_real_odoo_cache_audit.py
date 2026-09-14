"""cache tests for the real-Odoo CI contract."""

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import real_odoo_bootstrap as bootstrap
from scripts.real_odoo_pins import E2E_PINS


def test_cache_keys_include_all_immutable_inputs() -> None:
    source = bootstrap.source_cache_key("Linux", "amd64")
    uv = bootstrap.uv_cache_key("Linux", "amd64", b"odoo requirements", b"uv lock")
    assert source == "odoo19-Linux-amd64-cd992ceebbaf343c03e1941d39cfe423d35ba6c6"
    assert uv.startswith("uv-Linux-amd64-3.12.13-0.10.8-")
    assert len(uv.rsplit("-", 1)[1]) == 64


def test_full_python_resolution_lock_rejects_regeneration_or_hash_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "odoo.lock"
    lock.write_text("package==1.0\n--hash=sha256:" + "0" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "PYTHON_RESOLUTION_LOCK", lock)
    assert bootstrap.python_resolution_lock_is_valid() is False


def test_full_python_resolution_audit_rejects_lock_or_report_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-SEC-06: pinned audit metadata and expiry remain fail-closed."""
    "E2E-SEC-06"
    lock = tmp_path / "odoo.lock"
    audit = tmp_path / "odoo.audit.json"
    lock.write_bytes(bootstrap.PYTHON_RESOLUTION_LOCK.read_bytes())
    audit.write_bytes(bootstrap.PYTHON_RESOLUTION_AUDIT.read_bytes())
    monkeypatch.setattr(bootstrap, "PYTHON_RESOLUTION_LOCK", lock)
    monkeypatch.setattr(bootstrap, "PYTHON_RESOLUTION_AUDIT", audit)
    expected = {
        (item["package"], item["version"], advisory)
        for item in json.loads(audit.read_text(encoding="utf-8"))["exceptions"]
        for advisory in item["advisories"]
    }
    monkeypatch.setattr(bootstrap, "_run_pinned_python_audit", lambda _lock: expected)
    assert bootstrap.python_resolution_audit_is_valid()
    lock.write_bytes(lock.read_bytes() + b"\n")
    assert bootstrap.python_resolution_audit_is_valid() is False
    lock.write_bytes(bootstrap.PYTHON_RESOLUTION_LOCK.read_bytes())
    audit.write_bytes(audit.read_bytes() + b"\n")
    assert bootstrap.python_resolution_audit_is_valid() is False


def test_python_resolution_audit_rejects_scanner_metadata_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "odoo.lock"
    audit = tmp_path / "odoo.audit.json"
    lock.write_bytes(bootstrap.PYTHON_RESOLUTION_LOCK.read_bytes())
    value = json.loads(bootstrap.PYTHON_RESOLUTION_AUDIT.read_text(encoding="utf-8"))
    value["scanner"] = "pip-audit==2.10.1"
    audit.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "PYTHON_RESOLUTION_LOCK", lock)
    monkeypatch.setattr(bootstrap, "PYTHON_RESOLUTION_AUDIT", audit)
    monkeypatch.setattr(
        bootstrap,
        "E2E_PINS",
        replace(
            E2E_PINS,
            odoo_python_audit_sha256=hashlib.sha256(audit.read_bytes()).hexdigest(),
        ),
    )

    assert bootstrap.python_resolution_audit_is_valid() is False


def test_pinned_scanner_normalizes_package_names_and_uses_exact_distribution_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            1,
            json.dumps(
                {
                    "dependencies": [
                        {
                            "name": "Py_Pdf2",
                            "version": "2.12.1",
                            "vulns": [
                                {"id": "PYSEC-2026-1835", "fix_versions": ["2.12.2"]},
                                {"id": "PYSEC-2026-1835", "fix_versions": ["2.12.2"]},
                            ],
                        }
                    ],
                    "fixes": [],
                }
            ),
            "",
        )

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    result = bootstrap._run_pinned_python_audit(tmp_path / "odoo.lock")

    assert result == {("py-pdf2", "2.12.1", "PYSEC-2026-1835")}
    assert calls[0][calls[0].index("--from") + 1] == "pip-audit==2.10.1"
    assert "--strict" in calls[0]
    assert "--disable-pip" in calls[0]
    assert "--require-hashes" in calls[0]
    assert calls[0][calls[0].index("--desc") + 1] == "off"
    assert calls[0][calls[0].index("--aliases") + 1] == "off"


@pytest.mark.parametrize(
    "payload",
    [
        [{"name": "pypdf2", "version": "2.12.1", "vulns": []}],
        {"dependencies": [], "fixes": {"name": "pypdf2"}},
        {"dependencies": [{"name": "pypdf2", "version": "2.12.1", "vulns": [{}]}], "fixes": []},
    ],
    ids=("legacy-list", "fixes-not-list", "malformed-vulnerability"),
)
def test_pinned_scanner_rejects_legacy_or_malformed_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, json.dumps(payload), ""),
    )

    assert bootstrap._run_pinned_python_audit(tmp_path / "odoo.lock") is None


def test_resolution_audit_rejects_skipped_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependencies: dict[tuple[str, str], list[dict[str, object]]] = {}
    for package, version, advisory in _fixture_audit_findings():
        dependencies.setdefault((package, version), []).append({"id": advisory, "fix_versions": []})
    payload = {
        "dependencies": [
            {"name": package, "version": version, "vulns": vulnerabilities}
            for (package, version), vulnerabilities in sorted(dependencies.items())
        ]
        + [{"name": "un-audited-package", "skip_reason": "not supported"}],
        "fixes": [],
    }
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, json.dumps(payload), "")

    monkeypatch.setattr(bootstrap, "_run", fake_run)

    assert bootstrap.python_resolution_audit_is_valid() is False
    assert calls and "--strict" in calls[0]


def test_resolution_audit_accepts_repeated_live_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependencies: dict[tuple[str, str], list[dict[str, object]]] = {}
    for package, version, advisory in _fixture_audit_findings():
        dependencies.setdefault((package, version), []).append({"id": advisory, "fix_versions": []})
    first_package = min(dependencies)
    dependencies[first_package].append(dict(dependencies[first_package][0]))
    payload = {
        "dependencies": [
            {"name": package, "version": version, "vulns": vulnerabilities}
            for (package, version), vulnerabilities in sorted(dependencies.items())
        ],
        "fixes": [],
    }
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, json.dumps(payload), "")

    monkeypatch.setattr(bootstrap, "_run", fake_run)

    assert bootstrap.python_resolution_audit_is_valid()
    assert calls and calls[0][calls[0].index("--desc") + 1] == "off"
    assert calls[0][calls[0].index("--aliases") + 1] == "off"


def _fixture_audit_findings() -> set[tuple[str, str, str]]:
    value = json.loads(bootstrap.PYTHON_RESOLUTION_AUDIT.read_text(encoding="utf-8"))
    return {
        (item["package"], item["version"], advisory)
        for item in value["exceptions"]
        for advisory in item["advisories"]
    }


def test_python_resolution_audit_rejects_unknown_scanner_finding() -> None:
    """E2E-SEC-04: an unknown live scanner tuple stops provisioning."""
    "E2E-SEC-04"
    findings = _fixture_audit_findings()
    findings.add(("unexpected", "1.0", "CVE-unknown"))
    assert bootstrap.python_resolution_audit_is_valid(scanner_result=findings) is False


def test_python_resolution_audit_rejects_missing_scanner_finding() -> None:
    """E2E-SEC-05: a missing live scanner tuple stops provisioning."""
    "E2E-SEC-05"
    findings = _fixture_audit_findings()
    findings.pop()
    assert bootstrap.python_resolution_audit_is_valid(scanner_result=findings) is False


def test_bootstrap_emits_fail_closed_machine_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bootstrap,
        "prerequisite_checks",
        lambda _tier, _platform: {"docker": False, "compose": True},
    )
    output = tmp_path / "bootstrap.json"
    manifest = bootstrap.bootstrap("smoke", output, platform_name="linux/amd64", run_id="a" * 32)
    assert manifest["ok"] is False
    assert manifest["missing"] == ["docker"]
    assert json.loads(output.read_text(encoding="utf-8"))["pins"]
    assert not (tmp_path / ("a" * 32)).exists()


def test_full_bootstrap_verifies_an_arbitrary_commit_from_bare_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "odoo.git"
    cache.mkdir()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "requirements\n", "")

    monkeypatch.setattr(bootstrap, "_source_cache_path", lambda: cache)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path / "root")
    monkeypatch.setattr(bootstrap, "_run", fake_run)
    assert bootstrap._source_revision_is_available()
    assert not any("ls-remote" in command for command in calls)
    assert any("cat-file" in command for command in calls)
