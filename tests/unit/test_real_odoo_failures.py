from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.real_odoo_secrets import secret_variants
from tests.integration.real_odoo.cleanup import FailureEvidence, ResourceLedger
from tests.integration.real_odoo.failures import (
    assert_secret_free,
    run_recovery_action,
    write_archive_variant,
)


@pytest.mark.parametrize("variant", ["truncated", "incompatible"])
def test_archive_variants_are_written_without_publication(tmp_path: Path, variant: str) -> None:
    path = tmp_path / f"{variant}.zip"
    write_archive_variant(path, variant)  # type: ignore[arg-type]
    assert path.is_file()
    assert not (tmp_path / "database").exists()
    assert not (tmp_path / "filestore").exists()


def test_recovery_removes_owned_file_and_preserves_primary_error(
    tmp_path: Path,
) -> None:
    run_id = "a" * 32
    ledger = ResourceLedger(run_id)
    path = tmp_path / f"{run_id}-owned"
    path.write_text("owned", encoding="utf-8")
    ledger.record("artifact", path.name, path.unlink)
    primary = RuntimeError("restore failed")
    observation = run_recovery_action(
        lambda: (_ for _ in ()).throw(primary),
        ledger=ledger,
    )
    assert observation.primary_error is primary
    assert not path.exists()
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


def test_secret_scan_accepts_a_genuine_binary_zip(tmp_path: Path) -> None:
    canary = "focused-secret-canary"
    path = tmp_path / "binary.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("payload.bin", b"\x00\x01\xff\xfe\x80\x00")
    assert_secret_free((path,), canary)


@pytest.mark.parametrize(
    "variant",
    secret_variants("focused-secret-canary"),
    ids=("raw", "sha256", "base64", "urlencoded"),
)
def test_secret_scan_rejects_each_variant_inside_a_binary_zip(tmp_path: Path, variant: str) -> None:
    path = tmp_path / "binary-leak.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("payload.bin", b"\x00\xff" + variant.encode() + b"\x00")
    with pytest.raises(AssertionError, match="secret material leaked"):
        assert_secret_free((path,), "focused-secret-canary")


@pytest.mark.parametrize("name", ["recovery", "recovery-timeout", "partial"])
def test_recovery_input_is_removed_while_diagnostics_are_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    from tests.integration.real_odoo import conftest as fixtures
    from tests.integration.real_odoo import test_focused_recovery as recovery
    from tests.integration.real_odoo.archive import SourceBackupPlan, archive_identity

    run_id = "a" * 32
    runtime = Mock(
        spec=fixtures.E2ERuntime,
        run_id=run_id,
        root=tmp_path / f"runtime-{run_id}",
        artifact_root=tmp_path / f"artifacts-{run_id}",
        environment={"ODCLI_E2E_CATALOG": str(tmp_path / "catalog.sqlite3")},
        secret_registry_file=tmp_path / "secrets.json",
        failed=True,
    )
    runtime.root.mkdir()
    evidence = FailureEvidence(run_id, "canary", runtime.artifact_root)
    evidence.add_log("failure", "expected failure")
    diagnostics = evidence.write()
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("dump.sql", "SELECT 1;")
        archive.writestr("filestore/blob", "fixture")
    seed = Mock()
    monkeypatch.setattr(recovery, "_seed_backup", seed)
    monkeypatch.setattr(
        recovery, "_project", Mock(side_effect=RuntimeError("stop before provisioning"))
    )
    with pytest.raises(RuntimeError, match="stop before provisioning"):
        recovery._prepare_recovery_case(
            tmp_path,
            runtime,
            archive_identity(source),
            SourceBackupPlan("http://127.0.0.1", "source", source),
            evidence,
            name,
        )
    copied_archive = seed.call_args.args[1]
    assert copied_archive.read_bytes() == source.read_bytes()
    monkeypatch.setenv("ODCLI_E2E_KEEP_FAILED", "1")
    fixtures._remove_runtime_files(runtime)
    assert not copied_archive.exists()
    assert set(runtime.artifact_root.iterdir()) == set(diagnostics)
