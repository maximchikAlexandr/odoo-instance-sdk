from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from scripts import real_odoo_bootstrap as bootstrap
from scripts import real_odoo_evidence as evidence
from scripts import real_odoo_timing as timing


def _evidence_contract(source: Path) -> None:
    (source / "bootstrap.json").write_text('{"pins": {"uv": "0.10.8"}}\n')
    (source / "resource-manifest.json").write_text('{"leaks": []}\n')
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
    (source / "junit.xml").write_text("<testsuite tests='1' failures='0'/>\n")


def test_cache_keys_include_all_immutable_inputs() -> None:
    source = bootstrap.source_cache_key("Linux", "amd64")
    uv = bootstrap.uv_cache_key("Linux", "amd64", b"odoo requirements", b"uv lock")
    assert source == "odoo19-Linux-amd64-cd992ceebbaf343c03e1941d39cfe423d35ba6c6"
    assert uv.startswith("uv-Linux-amd64-3.12.13-0.10.8-")
    assert len(uv.rsplit("-", 1)[1]) == 64


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


def test_evidence_rejects_canary_and_writes_minimal_error(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source)
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
    _evidence_contract(source)
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


def test_real_odoo_workflows_are_immutable_and_select_their_tier() -> None:
    root = Path(__file__).resolve().parents[2]
    smoke = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    full = (root / ".github/workflows/real-odoo-full.yml").read_text(encoding="utf-8")
    docs = (root / "docs/real-odoo-e2e.md").read_text(encoding="utf-8")
    smoke_job = smoke[smoke.index("  real-odoo-smoke:") : smoke.index("\n  lint:")]
    for workflow in (smoke_job, full):
        assert "@v" not in workflow
        assert "retention-days: 7" in workflow
        assert "--output .artifacts/real-odoo-e2e/bootstrap.json" in workflow
    assert "timeout-minutes: 10" in smoke_job
    assert "real_odoo and e2e_smoke" in smoke
    assert "real_odoo and e2e_full" in full
    assert ".cache/odoo-source" in full
    assert ".cache/uv" in full
    assert "backups" not in full.lower()
    assert (
        "uv run pytest -o addopts='' -m 'real_odoo and e2e_smoke' tests/integration/real_odoo"
        in docs
    )
    assert (
        "uv run pytest -o addopts='' -m 'real_odoo and e2e_full' tests/integration/real_odoo"
        in docs
    )
    assert (
        "uv run python scripts/real_odoo_bootstrap.py --tier smoke --output .artifacts/real-odoo-e2e/bootstrap.json"
        in docs
    )
    assert (
        "uv run python scripts/real_odoo_bootstrap.py --tier full --output .artifacts/real-odoo-e2e/bootstrap.json"
        in docs
    )
    assert "--junitxml=.artifacts/real-odoo-e2e/junit.xml" in smoke_job
    assert "--junitxml=.artifacts/real-odoo-e2e/junit.xml" in full
    assert "phase setup" in smoke_job and "phase test" in smoke_job and "phase cleanup" in smoke_job
    assert "phase setup" in full and "phase test" in full and "phase cleanup" in full
    uv_key = "uv-${{ runner.os }}-${{ runner.arch }}-3.12.13-0.10.8-${{ hashFiles('uv.lock', '.cache/odoo-requirements.txt') }}"
    source_key = (
        "odoo19-${{ runner.os }}-${{ runner.arch }}-cd992ceebbaf343c03e1941d39cfe423d35ba6c6"
    )
    assert full.count(uv_key) == 4
    assert full.count(source_key) == 4
    assert f"ODCLI_E2E_UV_CACHE_KEY: {uv_key}" in full
    assert f"ODCLI_E2E_SOURCE_CACHE_KEY: {source_key}" in full
    action_refs = re.findall(r"uses:\s+[^@\s]+@([0-9a-f]{40})", smoke_job + full)
    assert action_refs and all(len(reference) == 40 for reference in action_refs)
