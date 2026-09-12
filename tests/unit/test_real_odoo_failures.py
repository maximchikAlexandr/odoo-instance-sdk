from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.real_odoo.archive import ArchiveValidationError, archive_identity
from tests.integration.real_odoo.cleanup import FailureEvidence, ResourceLedger, audit_no_leaks
from tests.integration.real_odoo.failures import (
    FOCUSED_EVIDENCE,
    RECOVERY_EVIDENCE,
    SECURITY_EVIDENCE,
    assert_secret_free,
    run_injected_failure,
    secret_variants,
    write_archive_variant,
)


def test_focused_contract_has_all_reviewed_evidence_ids() -> None:
    assert tuple(f"E2E-FC-{index:02d}" for index in range(1, 14)) == FOCUSED_EVIDENCE
    assert tuple(f"E2E-REC-{index:02d}" for index in range(1, 4)) == RECOVERY_EVIDENCE
    assert tuple(f"E2E-SEC-{index:02d}" for index in range(1, 4)) == SECURITY_EVIDENCE


@pytest.mark.parametrize("variant", ["truncated", "incompatible"])
def test_invalid_archive_variants_fail_before_publication(tmp_path: Path, variant: str) -> None:
    path = tmp_path / f"{variant}.zip"
    write_archive_variant(path, variant)  # type: ignore[arg-type]
    if variant == "truncated":
        with pytest.raises(ArchiveValidationError):
            archive_identity(path)
    else:
        assert archive_identity(path).filestore_members
    assert not (tmp_path / "database").exists()
    assert not (tmp_path / "filestore").exists()


def test_partial_publication_preserves_primary_error_and_cleans_owned_state(
    tmp_path: Path,
) -> None:
    run_id = "a" * 32
    outcome = run_injected_failure(
        run_id,
        tmp_path / f"run-{run_id}",
        error_code="db_restore_failed",
        publish=("database", "filestore", "catalog"),
        fail_at="filestore-publication",
    )
    assert outcome.exit_code == 1
    assert outcome.primary_error == "injected failure at filestore-publication"
    assert outcome.publication == outcome.publication.__class__()
    assert outcome.cleanup_errors == ()


def test_interrupt_and_cleanup_failure_are_reported_separately(tmp_path: Path) -> None:
    run_id = "b" * 32
    outcome = run_injected_failure(
        run_id,
        tmp_path / f"run-{run_id}",
        error_code="db_restore_interrupted",
        fail_at="interrupt",
        cleanup=(lambda: (_ for _ in ()).throw(RuntimeError("cleanup failure")),),
    )
    assert outcome.exit_code == 130
    assert outcome.primary_error == "injected failure at interrupt"
    assert outcome.cleanup_errors == ("cleanup failure",)


def test_retention_keeps_only_run_owned_files(tmp_path: Path) -> None:
    run_id = "c" * 32
    root = tmp_path / f"artifacts-{run_id}"
    outcome = run_injected_failure(
        run_id,
        root,
        error_code="exec_failed",
        publish=("database",),
        retain=True,
    )
    assert outcome.retained_files == (root / "failure.json",)
    assert all(path.parent == root for path in outcome.retained_files)
    assert all(run_id in path.parent.name for path in outcome.retained_files)


def test_evidence_and_machine_output_are_secret_free_and_bounded(tmp_path: Path) -> None:
    canary = "focused-secret-canary"
    evidence = FailureEvidence("d" * 32, canary, tmp_path / "evidence")
    evidence.add_log("failure", "master_pwd=super-secret; diagnostic redacted")
    files = evidence.write()
    assert_secret_free(files, canary)
    assert_secret_free(json.loads((tmp_path / "evidence" / "manifest.json").read_text()), canary)
    assert all(path.stat().st_size <= 2 * 1024 * 1024 for path in files)
    assert secret_variants(canary)[0] not in "\n".join(
        path.read_text(encoding="utf-8") for path in files
    )


def test_clean_recovery_audit_has_no_owned_resources(tmp_path: Path) -> None:
    run_id = "e" * 32
    root = tmp_path / f"run-{run_id}"
    outcome = run_injected_failure(run_id, root, error_code="restore_failed", publish=("database",))
    assert outcome.publication.published is False
    assert audit_no_leaks(run_id).clean
    assert not root.exists() or not tuple(root.iterdir())


def test_ledger_is_empty_after_recovery(tmp_path: Path) -> None:
    run_id = "f" * 32
    ledger = ResourceLedger(run_id)
    path = tmp_path / f"{run_id}-owned"
    path.write_text("owned", encoding="utf-8")

    def cleanup() -> None:
        path.unlink()

    ledger.record("artifact", path.name, cleanup)
    with pytest.raises(RuntimeError, match="primary"):
        ledger.unwind(primary_failure=RuntimeError("primary"))
    assert ledger.records == ()
    assert not path.exists()
