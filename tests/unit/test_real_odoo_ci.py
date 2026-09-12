from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest

from odoo_instance_sdk.internal.dependency_sync import build_trusted_sync_argv
from scripts import real_odoo_acceptance as acceptance
from scripts import real_odoo_bootstrap as bootstrap
from scripts import real_odoo_ci as ci
from scripts import real_odoo_evidence as evidence
from scripts import real_odoo_timing as timing
from scripts.real_odoo_secrets import secret_variants, write_secret_registry
from tests.integration.real_odoo import test_smoke as smoke
from tests.integration.real_odoo.conftest import E2ERuntime


def _evidence_contract(
    source: Path, *, junit_failures: int = 0, source_cache_consumed: bool = True
) -> None:
    (source / "bootstrap.json").write_text(
        json.dumps(
            {
                "ok": True,
                "platform": "linux/amd64",
                "architecture": "X64",
                "cache": {
                    "class": "cold",
                    "source_hit": False,
                    "uv_hit": False,
                    "source_key": "odoo19-Linux-X64-pin",
                    "uv_key": "uv-Linux-X64-python-uv-digest",
                },
                "pins": {"uv": "0.10.8"},
            }
        )
        + "\n"
    )
    (source / "resource-manifest.json").write_text(
        json.dumps(
            {
                "resources": ["owned-run-resource"],
                "audit": {
                    "state": "clean",
                    "leaks": [],
                    "runs": [{"run_id": "owned-run", "state": "clean", "leaks": {}}],
                },
                "source_cache_consumed": source_cache_consumed,
            }
        )
        + "\n"
    )
    (source / "command-matrix.md").write_text(
        "# Public CLI traceability matrix\n\n| Public leaf | Evidence |\n",
        encoding="utf-8",
    )
    (source / "timing.json").write_text(
        json.dumps(
            {
                "schema": "odcli-real-odoo-timing-v1",
                "phases": {
                    "setup": {"duration_seconds": 1.0},
                    "test": {"duration_seconds": 2.0},
                    "cleanup": {"duration_seconds": 0.1},
                },
            }
        )
    )
    (source / "junit.xml").write_text(f"<testsuite tests='1' failures='{junit_failures}'/>\n")


def _acceptance_run(root: Path, tier: str, cache_class: str, *, failed: bool = False) -> None:
    run = root / f"{tier}-{cache_class}"
    run.mkdir(parents=True)
    (run / "bootstrap.json").write_text(
        json.dumps(
            {
                "ok": True,
                "platform": "linux/amd64",
                "cache": {"class": cache_class},
            }
        )
    )
    properties = "".join(
        f"<property name='{identifier.lower().replace('-', '_')}' value='passed'/>"
        for identifier in acceptance.REQUIRED_TIER_EVIDENCE[tier]
    )
    (run / "junit.xml").write_text(
        f"<testsuite tests='1' failures='{int(failed)}' errors='0'>"
        f"<properties>{properties}</properties></testsuite>\n"
    )
    (run / "timing.json").write_text(
        json.dumps(
            {
                "phases": {
                    "setup": {"duration_seconds": 1.0},
                    "test": {"duration_seconds": 1.0},
                    "cleanup": {"duration_seconds": 1.0},
                },
                "budget": {
                    "ok": not failed,
                    "artifact_bytes": 100,
                    "artifact_budget_bytes": acceptance.SUCCESS_ARTIFACT_LIMIT_BYTES,
                },
            }
        )
    )
    (run / "evidence-manifest.json").write_text(
        json.dumps(
            {
                "ok": not failed,
                "artifact_bytes": 100,
                "bundle_limit_bytes": acceptance.FAILURE_BUNDLE_LIMIT_BYTES,
                "success_limit_bytes": acceptance.SUCCESS_ARTIFACT_LIMIT_BYTES,
                "budget": {"ok": not failed, "artifact_bytes": 100},
            }
        )
    )
    (run / "resource-manifest.json").write_text(
        json.dumps(
            {
                "resources": ["owned"],
                "source_cache_consumed": True,
                "audit": {
                    "state": "failed" if failed else "clean",
                    "leaks": ["leak"] if failed else [],
                    "runs": [{"state": "failed" if failed else "clean"}],
                },
            }
        )
    )


def _complete_acceptance_evidence(root: Path) -> None:
    for tier, cache_class in acceptance.REQUIRED_RUNS:
        _acceptance_run(root, tier, cache_class)


def test_acceptance_missing_evidence_is_not_ready(tmp_path: Path) -> None:
    state = acceptance._e2e_bootstrap_state(tmp_path)
    assert state["status"] == "missing"


def test_acceptance_blocked_bootstrap_is_not_ready(tmp_path: Path) -> None:
    run = tmp_path / "smoke-cold"
    run.mkdir()
    (run / "bootstrap.json").write_text(json.dumps({"ok": False, "missing": ["docker"]}))
    state = acceptance._e2e_bootstrap_state(tmp_path)
    assert state["status"] == "blocked"


def test_acceptance_failed_evidence_is_not_ready(tmp_path: Path) -> None:
    _complete_acceptance_evidence(tmp_path)
    failed_run = tmp_path / "full-warm"
    (failed_run / "junit.xml").write_text("<testsuite tests='1' failures='1' errors='0'/>\n")
    state = acceptance._e2e_bootstrap_state(tmp_path)
    assert state["status"] == "failed"


def test_acceptance_requires_complete_smoke_and_full_runs(tmp_path: Path) -> None:
    _complete_acceptance_evidence(tmp_path)
    state = acceptance._e2e_bootstrap_state(tmp_path)
    assert state["status"] == "complete"
    runs = state["runs"]
    assert isinstance(runs, dict)
    assert set(runs) == {f"{tier}-{cache}" for tier, cache in acceptance.REQUIRED_RUNS}


def test_acceptance_expands_matrix_evidence_ranges() -> None:
    identifiers = acceptance._matrix_evidence_ids("E2E-FC-01..04 E2E-REC-01..03")
    assert identifiers == (
        "E2E-FC-01",
        "E2E-FC-02",
        "E2E-FC-03",
        "E2E-FC-04",
        "E2E-REC-01",
        "E2E-REC-02",
        "E2E-REC-03",
    )


def test_acceptance_rejects_wrong_pytest_selector() -> None:
    selector = "tests/integration/real_odoo/test_critical_path.py::wrong"
    errors = acceptance._validate_evidence_executors(
        ("E2E-CP-01",), {"E2E-CP-01": (selector,)}, {selector}
    )
    assert any("does not emit" in error for error in errors)


def test_acceptance_rejects_executor_without_emitted_evidence() -> None:
    selector = acceptance.EVIDENCE_EXECUTORS["E2E-CP-01"][0]
    errors = acceptance._validate_evidence_executors(
        ("E2E-FC-01",), {"E2E-FC-01": (selector,)}, {selector}
    )
    assert any("does not emit" in error for error in errors)


def test_acceptance_rejects_duplicate_scenario_mapping() -> None:
    errors = acceptance._scenario_mapping_errors(
        ("Scenario A", "Scenario A"), {"Scenario A": ("E2E-CP-01",)}
    )
    assert "duplicate scenario heading" in errors


def test_acceptance_rejects_stale_and_missing_scenario_mapping() -> None:
    errors = acceptance._scenario_mapping_errors(("Scenario A",), {"Scenario B": ("E2E-CP-01",)})
    assert "missing scenario mapping: Scenario A" in errors
    assert "stale scenario mapping: Scenario B" in errors


def test_scope_proof_uses_frozen_planning_base(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output = "src/introduced-before-review.py\n" if command[1] == "diff" else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr("scripts.real_odoo_acceptance.subprocess.run", fake_run)
    scope = acceptance._scope_contract()
    assert scope["base"] == "0ff164636617c03a51277055af45cef009277368"
    assert scope["out_of_scope"] is False
    assert scope["forbidden_changed_files"] == ["src/introduced-before-review.py"]
    assert scope["scope_base"] == "origin/main...HEAD"
    assert calls[0][-1] == scope["scope_base"]


def test_real_pytest_plugins_load_through_collection(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        path for path in (str(root), environment.get("PYTHONPATH", "")) if path
    )
    environment["ODCLI_E2E_EVIDENCE_ROOT"] = str(tmp_path / "evidence")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "scripts.real_odoo_timing",
            "-p",
            "scripts.real_odoo_ci",
            str(root / "tests/integration/real_odoo"),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_container_smoke_public_path" in result.stdout


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


def test_full_critical_path_uses_only_hash_required_trusted_sync() -> None:
    argv = build_trusted_sync_argv(Path("/venv/bin/python"), Path("/lock.txt"))

    assert argv == (
        "uv",
        "pip",
        "sync",
        "--python",
        "/venv/bin/python",
        "--require-hashes",
        "/lock.txt",
    )
    assert "compile" not in argv
    assert "install" not in argv


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


def test_evidence_is_bounded_and_canary_safe(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source)
    canary_file = tmp_path / "canary"
    canary_file.write_text("canary-value-1234\n", encoding="utf-8")
    output = tmp_path / "evidence.tar.gz"
    result = evidence.package_evidence(
        source,
        output,
        status="success",
        canary_file=canary_file,
        tier="smoke",
        cache_class="cold",
    )
    assert result["artifact_bytes"] == output.stat().st_size
    budget = result["budget"]
    assert isinstance(budget, dict)
    assert budget["artifact_bytes"] == output.stat().st_size
    assert result["retention_days"] == 7
    assert '"ok": true' in (source / "timing.json").read_text()
    assert "setup_seconds" in (source / "junit.xml").read_text()
    with tarfile.open(output, "r:gz") as archive:
        names = archive.getnames()
        assert "command-matrix.md" not in names
        assert "odoo.log" not in names
        assert "postgres.log" not in names
        assert "compose.log" not in names


def test_evidence_rejects_canary_and_writes_minimal_error(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source, junit_failures=1)
    for name in ("odoo.log", "postgres.log", "compose.log"):
        (source / name).write_text("safe bounded tail\n", encoding="utf-8")
    (source / "odoo.log").write_text("canary-value-1234\n", encoding="utf-8")
    canary_file = tmp_path / "canary"
    canary_file.write_text("canary-value-1234\n", encoding="utf-8")
    output = tmp_path / "evidence.tar.gz"
    with pytest.raises(ValueError, match="canary"):
        evidence.package_evidence(
            source,
            output,
            status="failure",
            canary_file=canary_file,
            tier="smoke",
            cache_class="cold",
        )
    assert (tmp_path / "packaging-error.json").is_file()
    assert "canary-value-1234" not in (tmp_path / "packaging-error.json").read_text()
    assert 'tests="0" failures="1"' in (source / "junit.xml").read_text()


@pytest.mark.parametrize(
    "variant",
    secret_variants("runtime/db?password=123&token=/safe"),
    ids=("raw", "sha256", "base64", "urlencoded"),
)
def test_evidence_rejects_every_runtime_secret_variant_from_registry(
    tmp_path: Path, variant: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source, junit_failures=1)
    secret = "runtime/db?password=123&token=/safe"
    (source / "odoo.log").write_text(f"service evidence={variant}\n", encoding="utf-8")
    for name in ("postgres.log", "compose.log"):
        (source / name).write_text("bounded tail\n", encoding="utf-8")
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    registry = tmp_path / "secret-registry.json"
    write_secret_registry(registry, (secret,))
    with pytest.raises(ValueError, match="runtime secret"):
        evidence.package_evidence(
            source,
            tmp_path / "secret.tar.gz",
            status="failure",
            canary_file=canary,
            secret_registry_file=registry,
            tier="smoke",
            cache_class="cold",
        )
    assert secret not in (tmp_path / "packaging-error.json").read_text()


def test_evidence_rejects_manifest_canary_and_replaces_junit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source)
    bootstrap_value = json.loads((source / "bootstrap.json").read_text())
    bootstrap_value["canary"] = "canary-value-1234"
    (source / "bootstrap.json").write_text(json.dumps(bootstrap_value))
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match="canary"):
        evidence.package_evidence(
            source,
            tmp_path / "manifest-canary.tar.gz",
            status="success",
            canary_file=canary,
            tier="smoke",
            cache_class="cold",
        )
    assert "canary-value-1234" not in (source / "junit.xml").read_text()
    assert 'tests="0" failures="1"' in (source / "junit.xml").read_text()


def test_evidence_rejects_empty_success_and_oversized_failure_input(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="missing evidence contract"):
        evidence.package_evidence(
            empty,
            tmp_path / "empty.tar.gz",
            status="success",
            tier="smoke",
            cache_class="cold",
        )

    source = tmp_path / "oversized"
    source.mkdir()
    _evidence_contract(source, junit_failures=1)
    for name in ("odoo.log", "postgres.log", "compose.log"):
        (source / name).write_text("safe\n", encoding="utf-8")
    (source / "odoo.log").write_bytes(b"x" * (evidence.TEXT_LIMIT_BYTES + 1))
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds"):
        evidence.package_evidence(
            source,
            tmp_path / "oversized.tar.gz",
            status="failure",
            canary_file=canary,
            tier="smoke",
            cache_class="cold",
        )
    assert (tmp_path / "packaging-error.json").is_file()


def test_timing_ledger_uses_monotonic_phase_durations(tmp_path: Path) -> None:
    path = tmp_path / "timing.json"
    timing.start(path, "setup", now=10.0)
    timing.finish(path, "setup", now=12.5)
    assert json.loads(path.read_text())["phases"]["setup"]["duration_seconds"] == 2.5


def test_evidence_rejects_oversized_failure_bundle(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.tar.gz"
    with oversized.open("wb") as stream:
        stream.truncate(evidence.FAILURE_BUNDLE_LIMIT_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds"):
        evidence._check_archive_size(oversized, "failure")


def test_evidence_enforces_phase_budget_after_cleanup(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source)
    timing_value = json.loads((source / "timing.json").read_text(encoding="utf-8"))
    timing_value["phases"]["setup"]["duration_seconds"] = 361
    (source / "timing.json").write_text(json.dumps(timing_value), encoding="utf-8")
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match="budget"):
        evidence.package_evidence(
            source,
            tmp_path / "budget.tar.gz",
            status="success",
            canary_file=canary,
            tier="smoke",
            cache_class="cold",
        )


def test_evidence_rejects_placeholder_success(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "bootstrap.json").write_text('{"ok": false, "pins": {}}\n')
    (source / "junit.xml").write_text("<testsuite tests='0' failures='1'/>\n")
    (source / "command-matrix.md").write_text("# Public CLI traceability matrix\n")
    (source / "resource-manifest.json").write_text('{"resources": [], "leaks": []}\n')
    (source / "timing.json").write_text(
        json.dumps(
            {
                "phases": {
                    "setup": {"duration_seconds": 0},
                    "test": {"duration_seconds": 0},
                    "cleanup": {"duration_seconds": 0},
                }
            }
        )
    )
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bootstrap"):
        evidence.package_evidence(
            source,
            tmp_path / "placeholder.tar.gz",
            status="success",
            canary_file=canary,
            tier="smoke",
            cache_class="cold",
        )
    assert (tmp_path / "packaging-error.json").is_file()
    assert (source / "junit.xml").is_file()


def test_evidence_records_runtime_and_audit_properties(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source)
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    result = evidence.package_evidence(
        source,
        tmp_path / "evidence.tar.gz",
        status="success",
        canary_file=canary,
        tier="smoke",
        cache_class="cold",
    )
    resource = json.loads((source / "resource-manifest.json").read_text())
    assert resource["evidence"]["platform"] == "linux/amd64"
    assert resource["evidence"]["architecture"] == "X64"
    assert resource["evidence"]["cache"]["source_hit"] is False
    assert resource["evidence"]["pins"]["uv"] == "0.10.8"
    assert resource["evidence"]["timing"]["cleanup_seconds"] == 0.1
    assert resource["evidence"]["artifact_bytes"] == result["artifact_bytes"]
    assert resource["evidence"]["audit_state"] == "clean"
    junit = (source / "junit.xml").read_text()
    for property_name in (
        "platform",
        "architecture",
        "cache_source_hit",
        "cache_uv_hit",
        "pin_uv",
        "timing_cleanup_seconds",
        "artifact_bytes",
        "audit_state",
    ):
        assert f'name="{property_name}"' in junit


def test_failure_evidence_keeps_non_clean_completed_audit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source, junit_failures=1)
    resource = json.loads((source / "resource-manifest.json").read_text())
    resource["audit"] = {
        "state": "failed",
        "leaks": [{"run_id": "owned-run", "state": "leaked"}],
        "runs": [{"run_id": "owned-run", "state": "leaked", "leaks": {"port": ["1"]}}],
    }
    (source / "resource-manifest.json").write_text(json.dumps(resource))
    for name in ("compose.log", "odoo.log", "postgres.log"):
        (source / name).write_text("bounded service tail\n")
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    result = evidence.package_evidence(
        source,
        tmp_path / "failure.tar.gz",
        status="failure",
        canary_file=canary,
        tier="smoke",
        cache_class="cold",
    )
    assert result["ok"] is True
    assert (
        json.loads((source / "resource-manifest.json").read_text())["evidence"]["audit_state"]
        == "failed"
    )
    with tarfile.open(tmp_path / "failure.tar.gz", "r:gz") as archive:
        assert "command-matrix.md" in archive.getnames()


def test_full_failure_evidence_requires_host_target_log(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source, junit_failures=1)
    for name in ("compose.log", "odoo.log", "postgres.log"):
        (source / name).write_text("bounded service tail\n")
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"target-odoo\.log"):
        evidence.package_evidence(
            source,
            tmp_path / "missing-target-log.tar.gz",
            status="failure",
            canary_file=canary,
            tier="full",
            cache_class="cold",
        )


@pytest.mark.parametrize("cache_class", ["cold", "warm"])
def test_evidence_exercises_smoke_budget_classification(
    tmp_path: Path, cache_class: Literal["cold", "warm"]
) -> None:
    timing_path = tmp_path / "timing.json"
    timing_path.write_text(
        json.dumps(
            {
                "phases": {
                    "setup": {"duration_seconds": 1},
                    "test": {"duration_seconds": 2},
                    "cleanup": {"duration_seconds": 1},
                }
            }
        )
    )
    report = evidence._budget_report(
        timing_path,
        status="success",
        tier="smoke",
        cache_class=cache_class,
        artifact_bytes=10,
    )
    assert report["cache_class"] == cache_class
    assert report["ok"] is True


@pytest.mark.parametrize("cache_class", ["cold", "warm"])
def test_smoke_bootstrap_and_evidence_emit_cache_class_and_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_class: Literal["cold", "warm"],
) -> None:
    """Exercise the smoke cache matrix through bootstrap and packaging."""
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "_cache_os_arch", lambda: ("Linux", "X64"))
    monkeypatch.setattr(bootstrap, "prerequisite_checks", lambda _tier, _platform: {})
    (tmp_path / "uv.lock").write_bytes(b"repository lock")
    requirements = tmp_path / ".cache" / "odoo-requirements.txt"
    requirements.parent.mkdir()
    requirements.write_bytes(b"pinned requirements")
    source = tmp_path / "evidence"
    source.mkdir()
    _evidence_contract(source)
    monkeypatch.setenv("ODCLI_E2E_SOURCE_CACHE_HIT", "false")
    monkeypatch.setenv("ODCLI_E2E_UV_CACHE_HIT", str(cache_class == "warm").lower())
    bootstrap_manifest = bootstrap.bootstrap(
        "smoke", source / "bootstrap.json", platform_name="linux/amd64", artifact_root=source
    )
    emitted_cache = bootstrap_manifest["cache"]
    assert isinstance(emitted_cache, dict)
    emitted_class = cast("Literal['cold', 'warm']", emitted_cache["class"])
    assert emitted_class == cache_class
    canary = tmp_path / "canary"
    canary.write_text("smoke-canary\n", encoding="utf-8")
    packaged = evidence.package_evidence(
        source,
        tmp_path / "smoke.tar.gz",
        status="success",
        canary_file=canary,
        tier="smoke",
        cache_class=emitted_class,
    )
    packaged_budget = packaged["budget"]
    assert isinstance(packaged_budget, dict)
    assert packaged_budget["cache_class"] == cache_class
    assert packaged_budget["ok"] is True


def test_smoke_bootstrap_ignores_label_only_cache_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bootstrap, "prerequisite_checks", lambda _tier, _platform: {})
    monkeypatch.setenv("ODCLI_E2E_CACHE_CLASS", "warm")
    monkeypatch.setenv("ODCLI_E2E_SOURCE_CACHE_HIT", "false")
    monkeypatch.setenv("ODCLI_E2E_UV_CACHE_HIT", "false")
    manifest = bootstrap.bootstrap(
        "smoke", tmp_path / "bootstrap.json", platform_name="linux/amd64"
    )
    cache = manifest["cache"]
    assert isinstance(cache, dict)
    assert cache["class"] == "cold"


def test_smoke_target_wiring_keeps_source_and_target_endpoints_distinct(tmp_path: Path) -> None:
    source_password = tmp_path / "source-password"
    target_password = tmp_path / "target-password"
    source_password.write_text("source-master\n", encoding="utf-8")
    target_password.write_text("target-master\n", encoding="utf-8")
    target_secret = tmp_path / "target-pg-password"
    target_secret.write_text("pg-secret\n", encoding="utf-8")
    target = SimpleNamespace(
        master_password_file=target_password,
        secret_file=target_secret,
        container_config_file=tmp_path / "container.conf",
        config_file=tmp_path / "host.conf",
        root=tmp_path / "target-root",
        topology=SimpleNamespace(target_postgres_port=15432),
        reservations=(None, None, None, SimpleNamespace(port=18069)),
    )
    source = SimpleNamespace(master_password_file=source_password)
    smoke._align_target_master_password(cast("E2ERuntime", target), cast("E2ERuntime", source))
    host_config = (tmp_path / "host.conf").read_text(encoding="utf-8")
    container_config = (tmp_path / "container.conf").read_text(encoding="utf-8")
    assert "admin_passwd = source-master" in host_config
    assert "db_host = 127.0.0.1" in host_config
    assert "db_port = 15432" in host_config
    assert "http_port = 18069" in host_config
    assert "admin_passwd = source-master" in container_config
    assert "db_host = target_postgres" in container_config
    assert target.root.joinpath("target-data").as_posix() in host_config
    assert "source_postgres" not in host_config


def test_smoke_releases_target_port_before_compose_and_waits_for_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class Reservation:
        port = 18069

        def release(self) -> None:
            events.append("release")

    class Lifecycle:
        def __init__(self, _compose_file: Path, _project_name: str) -> None:
            pass

        def run(self, *_args: str) -> subprocess.CompletedProcess[str]:
            events.append("compose")
            return subprocess.CompletedProcess([], 0, "", "")

    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text(
        '  target_init:\n    command: ["odoo", '
        '"--database=odcli_e2e_sentinel_run", "--init=base", "--stop-after-init"]\n',
        encoding="utf-8",
    )
    runtime = SimpleNamespace(
        compose_file=compose_file,
        topology=SimpleNamespace(
            project_name="odcli-e2e-project-run",
            target_sentinel_database="odcli_e2e_sentinel_run",
        ),
        reservations=(None, None, None, Reservation()),
    )
    monkeypatch.setattr(smoke, "ComposeLifecycle", Lifecycle)
    monkeypatch.setattr(smoke, "wait_for_http", lambda *_args, **_kwargs: events.append("health"))
    smoke._start_target_odoo(cast("E2ERuntime", runtime))
    assert events == ["release", "compose", "health", "health"]


def test_timing_plugin_measures_fixture_cleanup_after_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "timing.json"
    timing.start(path, "test", now=10.0)
    monkeypatch.setenv("ODCLI_E2E_TIMING_FILE", str(path))
    clock = iter((12.0, 20.0, 23.0))
    monkeypatch.setattr("scripts.real_odoo_timing.time.monotonic", lambda: next(clock))
    hook = timing.pytest_sessionfinish(None, 0)
    next(hook)
    value = json.loads(path.read_text())
    assert "duration_seconds" not in value["phases"]["test"]
    assert "cleanup" not in value["phases"]
    with pytest.raises(StopIteration):
        next(hook)
    value = json.loads(path.read_text())
    assert value["phases"]["test"]["duration_seconds"] == 2.0


def test_declared_plugins_reach_pytest_collection_and_execution(tmp_path: Path) -> None:
    test_file = tmp_path / "test_declared_plugin.py"
    test_file.write_text(
        "def test_declared_plugin_executes():\n    assert True\n",
        encoding="utf-8",
    )
    timing_file = tmp_path / "timing.json"
    timing.start(timing_file, "test")
    environment = {
        **os.environ,
        "ODCLI_E2E_TIMING_FILE": str(timing_file),
        "ODCLI_E2E_EVIDENCE_ROOT": str(tmp_path / "evidence"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "scripts.real_odoo_timing",
            "-p",
            "scripts.real_odoo_ci",
            str(test_file),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    manifest = json.loads(timing_file.read_text(encoding="utf-8"))
    assert manifest["phases"]["test"]["duration_seconds"] >= 0.0
    assert (tmp_path / "evidence" / "command-matrix.md").is_file()


def test_test_timing_excludes_recorded_cleanup_segments(tmp_path: Path) -> None:
    path = tmp_path / "timing.json"
    timing.start(path, "test", now=0.0)
    timing.record(path, "cleanup", 10.0, 20.0)
    assert timing.finish_test_excluding_cleanup(path, now=50.0) == 40.0
    value = json.loads(path.read_text())
    assert value["phases"]["test"]["duration_seconds"] == 40.0
    assert value["phases"]["cleanup"]["duration_seconds"] == 10.0


def test_bootstrap_rejects_injected_cache_key_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bootstrap,
        "prerequisite_checks",
        lambda _tier, _platform: {"docker": True},
    )
    monkeypatch.setenv("ODCLI_E2E_SOURCE_CACHE_KEY", "injected-wrong-key")
    output = tmp_path / "bootstrap.json"
    manifest = bootstrap.bootstrap("smoke", output, platform_name="linux/amd64")
    assert manifest["ok"] is False
    assert "does not match computed cache key" in str(manifest["error"])


def test_cleanup_instrumentation_wraps_finalize_and_writes_post_audit_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Record:
        def __init__(self) -> None:
            self.kind = "container"
            self.name = "owned-run-container"
            self.metadata = {"owner": "owned-run"}

    class Ledger:
        records = (Record(),)

    runtime = type(
        "Runtime",
        (),
        {
            "run_id": "owned-run",
            "scope": "source",
            "ledger": Ledger(),
            "topology": type(
                "Topology",
                (),
                {
                    "project_name": "owned-run-project",
                    "source_postgres_port": 1,
                    "target_postgres_port": 2,
                    "source_odoo_port": 3,
                },
            )(),
            "reservations": (None, None, None, type("Reservation", (), {"port": 4})()),
            "compose_file": tmp_path / "compose.yaml",
            "root": tmp_path / "runtime",
        },
    )()
    timing_path = tmp_path / "timing.json"
    timing.start(timing_path, "test", now=0.0)
    monkeypatch.setenv("ODCLI_E2E_TIMING_FILE", str(timing_path))
    monkeypatch.setattr(ci, "_evidence_root", lambda: tmp_path / "evidence")
    monkeypatch.setattr(ci, "_capture_service_logs", lambda _runtime: None)
    monkeypatch.setattr(
        ci,
        "_audit",
        lambda _runtime: {"run_id": "owned-run", "state": "clean", "leaks": {}},
    )
    monkeypatch.setattr("scripts.real_odoo_ci.time.monotonic", iter((10.0, 15.0)).__next__)
    finalized: list[str] = []
    monkeypatch.setattr(
        ci,
        "_ORIGINAL_FINALIZE",
        lambda _runtime, _failure=None: finalized.append("done"),
    )
    ci._RUNTIMES.clear()
    ci._RESOURCE_SNAPSHOTS.clear()
    ci._SOURCE_CACHE_CONSUMED.clear()
    ci._instrumented_finalize(runtime)
    assert finalized == ["done"]
    timing_value = json.loads(timing_path.read_text())
    assert timing_value["phases"]["cleanup"]["duration_seconds"] == 5.0
    resource = json.loads((tmp_path / "evidence" / "resource-manifest.json").read_text())
    assert resource["resources"][0]["name"] == "owned-run-container"
    assert resource["audit"]["state"] == "clean"
