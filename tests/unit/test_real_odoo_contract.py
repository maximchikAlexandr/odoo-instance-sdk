from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.integration.real_odoo.contracts import (
    CANONICAL_INVENTORY_BASE,
    ORIGINAL_AUDIT_BASE,
    ContractError,
    check_matrix_document,
)
from tests.integration.real_odoo.pins import (
    E2E_PINS,
    PHASE_BUDGETS,
    PrerequisiteError,
    budget_for,
    classify_cache,
    normalize_platform,
    pin_manifest_dict,
    prerequisite_manifest,
    require_prerequisites,
    validate_pins,
    validate_platform,
)
from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES


def test_generated_matrix_matches_canonical_inventory() -> None:
    path = Path("openspec/changes/add-reproducible-odoo19-e2e-harness/command-matrix.md")
    actual = path.read_bytes()
    check_matrix_document(str(path), PUBLIC_LEAF_CASES)
    assert actual == path.read_bytes()
    assert actual.count(b"| `") == 50


def test_new_leaf_without_metadata_fails_closed() -> None:
    incomplete = replace(
        PUBLIC_LEAF_CASES[0], e2e_disposition=None, e2e_evidence=(), e2e_rationale=""
    )
    with pytest.raises(ContractError, match="missing E2E disposition"):
        from tests.integration.real_odoo.contracts import validate_leaf_metadata

        validate_leaf_metadata((incomplete,))


def test_pins_are_exact_and_immutable() -> None:
    validate_pins()
    assert E2E_PINS.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert pin_manifest_dict()["odoo_source_commit"] == "cd992ceebbaf343c03e1941d39cfe423d35ba6c6"
    assert pin_manifest_dict()["pip_audit"] == "pip-audit==2.10.1"
    with pytest.raises((AttributeError, TypeError)):
        E2E_PINS.odoo_image = "latest"  # type: ignore[misc]
    with pytest.raises(PrerequisiteError, match="image pin"):
        validate_pins(replace(E2E_PINS, odoo_image="docker.io/library/odoo:latest"))
    with pytest.raises(PrerequisiteError, match="audit must be pinned"):
        validate_pins(replace(E2E_PINS, odoo_python_audit_sha256="not-a-sha"))
    with pytest.raises(PrerequisiteError, match=r"pip-audit==2\.10\.1"):
        validate_pins(replace(E2E_PINS, pip_audit="pip-audit==2.10.0"))


def test_matrix_provenance_uses_both_reviewed_bases() -> None:
    matrix = Path(
        "openspec/changes/add-reproducible-odoo19-e2e-harness/command-matrix.md"
    ).read_text(encoding="utf-8")
    assert f"canonical-inventory base `{CANONICAL_INVENTORY_BASE}`" in matrix
    assert f"full-change audit base remains `{ORIGINAL_AUDIT_BASE}`" in matrix


def test_platforms_and_phase_budgets_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    assert normalize_platform("Linux", "x86_64") == "linux/amd64"
    assert normalize_platform("linux", "aarch64") == "linux/arm64"
    assert normalize_platform("Darwin", "arm64") == "linux/arm64"
    assert validate_platform("LINUX/AMD64") == "linux/amd64"
    assert budget_for("smoke", "cold").setup_seconds == 360
    assert budget_for("full", "warm").setup_seconds == 420
    assert classify_cache(source_hit=True, uv_hit=True) == "warm"
    assert classify_cache(source_hit=True, uv_hit=False) == "cold"
    with pytest.raises(PrerequisiteError, match="unsupported platform"):
        validate_platform("darwin/arm64")
    with pytest.raises(PrerequisiteError, match="unsupported platform"):
        normalize_platform("Darwin", "x86_64")
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    assert prerequisite_manifest({"docker": True}) == {
        "platform": "linux/arm64",
        "missing": [],
        "ok": True,
    }
    with pytest.raises(TypeError):
        PHASE_BUDGETS[("smoke", "cold")] = budget_for("smoke", "cold")  # type: ignore[index]


def test_missing_prerequisites_report_machine_readable_failure() -> None:
    with pytest.raises(PrerequisiteError, match=r'"missing": \["docker"\]'):
        require_prerequisites({"docker": False, "compose": True}, platform="linux/amd64")
