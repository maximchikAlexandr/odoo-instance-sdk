"""evidence tests for the real-Odoo CI contract."""

import json
import tarfile
from pathlib import Path

import pytest

from scripts import real_odoo_evidence as evidence
from scripts import real_odoo_timing as timing
from scripts.real_odoo_secrets import secret_variants, write_secret_registry
from tests.unit.real_odoo_ci_support import _evidence_contract


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


def test_full_failure_evidence_accepts_audited_unconsumed_cache_before_scenario(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source, junit_failures=1, source_cache_consumed=False)
    for name in ("compose.log", "odoo.log", "postgres.log", "target-odoo.log"):
        (source / name).write_text("bounded pre-scenario tail\n")
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    result = evidence.package_evidence(
        source,
        tmp_path / "full-failure.tar.gz",
        status="failure",
        canary_file=canary,
        tier="full",
        cache_class="cold",
    )
    assert result["ok"] is True
    resource = json.loads((source / "resource-manifest.json").read_text(encoding="utf-8"))
    assert resource["source_cache_consumed"] is False
