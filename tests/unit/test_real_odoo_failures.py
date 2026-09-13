from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.real_odoo.cleanup import FailureEvidence, ResourceLedger, audit_no_leaks
from tests.integration.real_odoo.failures import (
    FOCUSED_EVIDENCE,
    RECOVERY_EVIDENCE,
    SECURITY_EVIDENCE,
    InjectedFailure,
    PublicationProxy,
    assert_secret_free,
    run_recovery_action,
    secret_variants,
    write_archive_variant,
)


def test_focused_contract_has_all_reviewed_evidence_ids() -> None:
    assert tuple(f"E2E-FC-{index:02d}" for index in range(1, 14)) == FOCUSED_EVIDENCE
    assert tuple(f"E2E-REC-{index:02d}" for index in range(1, 4)) == RECOVERY_EVIDENCE
    assert tuple(f"E2E-SEC-{index:02d}" for index in range(1, 4)) == SECURITY_EVIDENCE


@pytest.mark.parametrize("variant", ["truncated", "incompatible"])
def test_archive_variants_are_written_without_publication(tmp_path: Path, variant: str) -> None:
    path = tmp_path / f"{variant}.zip"
    write_archive_variant(path, variant)  # type: ignore[arg-type]
    assert path.is_file()
    assert not (tmp_path / "database").exists()
    assert not (tmp_path / "filestore").exists()


def test_partial_publication_uses_shared_ledger_and_preserves_primary_error(
    tmp_path: Path,
) -> None:
    run_id = "a" * 32
    ledger = ResourceLedger(run_id)
    proxy = PublicationProxy(run_id, tmp_path / f"run-{run_id}", ledger)
    proxy.publish("database")
    proxy.publish("filestore")
    observation = run_recovery_action(
        lambda: (_ for _ in ()).throw(InjectedFailure("filestore-publication")),
        ledger=ledger,
    )
    assert observation.exit_code == 1
    assert str(observation.primary_error) == "injected failure at filestore-publication"
    assert observation.cleanup_errors == ()
    assert ledger.records == ()


def test_interrupt_and_cleanup_failure_are_reported_separately() -> None:
    run_id = "b" * 32
    ledger = ResourceLedger(run_id)

    def cleanup_failure() -> None:
        raise RuntimeError("cleanup failure")

    ledger.record("cleanup", f"{run_id}-cleanup", cleanup_failure)
    observation = run_recovery_action(
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()), ledger=ledger
    )
    assert observation.exit_code == 130
    assert isinstance(observation.primary_error, KeyboardInterrupt)
    assert observation.cleanup_errors == ("cleanup failure",)


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
    ledger = ResourceLedger(run_id)
    root = tmp_path / f"run-{run_id}"
    proxy = PublicationProxy(run_id, root, ledger)
    proxy.publish("database")
    run_recovery_action(lambda: (_ for _ in ()).throw(RuntimeError("primary")), ledger=ledger)
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
