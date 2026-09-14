"""acceptance tests for the real-Odoo CI contract."""

import json
import sys
from pathlib import Path

import pytest

from scripts import real_odoo_acceptance as acceptance
from tests.unit.real_odoo_ci_support import _complete_acceptance_evidence


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


@pytest.mark.parametrize(
    ("complete", "mapping_valid", "expected_exit"),
    [(True, True, 0), (False, True, 2), (True, False, 2)],
    ids=["complete", "missing-runs", "invalid-traceability"],
)
def test_acceptance_cli_reports_actual_run_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    complete: bool,
    mapping_valid: bool,
    expected_exit: int,
) -> None:
    if complete:
        _complete_acceptance_evidence(tmp_path)
    static = {
        "evidence": {
            "missing_ids": [],
            "smoke_marker_present": True,
            "scenario_mapping_valid": mapping_valid,
            "scenario_missing_evidence": [],
        }
    }
    monkeypatch.setattr(acceptance, "_static_contract", lambda: static)
    monkeypatch.setattr(sys, "argv", ["acceptance", "--artifact-root", str(tmp_path)])

    assert acceptance.main() == expected_exit

    report = json.loads((tmp_path / "acceptance.json").read_text())
    assert json.loads(capsys.readouterr().out) == report
    assert report["e2e"]["status"] == ("complete" if complete else "missing")
    assert (tmp_path / "acceptance.json").stat().st_mode & 0o777 == 0o600


def test_acceptance_rejects_stale_reused_pin_manifest(tmp_path: Path) -> None:
    _complete_acceptance_evidence(tmp_path)
    path = tmp_path / "full-warm" / "bootstrap.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["pins"]["pip_audit"] = "pip-audit==2.10.0"
    path.write_text(json.dumps(value), encoding="utf-8")

    state = acceptance._e2e_bootstrap_state(tmp_path)

    assert state["status"] == "failed"
    runs = state["runs"]
    assert isinstance(runs, dict)
    assert runs["full-warm"]["reason"] == "bootstrap pin manifest is stale or incomplete"


def test_acceptance_rejects_full_evidence_without_audited_bootstrap(tmp_path: Path) -> None:
    _complete_acceptance_evidence(tmp_path)
    path = tmp_path / "full-cold" / "bootstrap.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    del value["prerequisites"]["python_resolution_audit"]
    path.write_text(json.dumps(value), encoding="utf-8")

    state = acceptance._e2e_bootstrap_state(tmp_path)

    assert state["status"] == "failed"
    runs = state["runs"]
    assert isinstance(runs, dict)
    assert runs["full-cold"]["reason"] == (
        "full bootstrap lacks a successful audited source prerequisite"
    )


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
