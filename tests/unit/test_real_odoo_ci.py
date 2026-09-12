from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import real_odoo_bootstrap as bootstrap
from scripts import real_odoo_evidence as evidence


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


def test_evidence_is_bounded_and_canary_safe(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "junit.xml").write_text("<testsuite/>\n", encoding="utf-8")
    canary_file = tmp_path / "canary"
    canary_file.write_text("canary-value-1234\n", encoding="utf-8")
    output = tmp_path / "evidence.tar.gz"
    result = evidence.package_evidence(source, output, status="success", canary_file=canary_file)
    assert result["artifact_bytes"] == output.stat().st_size
    assert result["retention_days"] == 7


def test_evidence_rejects_canary_and_writes_minimal_error(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "odoo.log").write_text("canary-value-1234\n", encoding="utf-8")
    canary_file = tmp_path / "canary"
    canary_file.write_text("canary-value-1234\n", encoding="utf-8")
    output = tmp_path / "evidence.tar.gz"
    with pytest.raises(ValueError, match="canary"):
        evidence.package_evidence(source, output, status="failure", canary_file=canary_file)
    assert (tmp_path / "packaging-error.json").is_file()
    assert "canary-value-1234" not in (tmp_path / "packaging-error.json").read_text()


def test_real_odoo_workflows_are_immutable_and_select_their_tier() -> None:
    root = Path(__file__).resolve().parents[2]
    smoke = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    full = (root / ".github/workflows/real-odoo-full.yml").read_text(encoding="utf-8")
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
