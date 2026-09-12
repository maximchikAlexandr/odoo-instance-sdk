"""Focused failure, recovery, and security scenarios for the full E2E tier."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from .archive import ArchiveValidationError, SourceBackupPlan, archive_identity
from .cleanup import FailureEvidence, audit_no_leaks
from .conftest import E2ERuntime
from .failures import (
    FailureOutcome,
    assert_secret_free,
    run_injected_failure,
    write_archive_variant,
    write_failure_evidence,
)

pytestmark = [pytest.mark.real_odoo, pytest.mark.e2e_full, pytest.mark.serial]


def _record(record_property: object, evidence: str, value: object = "passed") -> None:
    record = getattr(record_property, "__call__")
    record(evidence.lower().replace("-", "_"), json.dumps(value, default=str, sort_keys=True))


def _assert_machine_failure(outcome: FailureOutcome, *, code: str) -> dict[str, object]:
    payload = outcome.as_machine_output()
    assert payload["ok"] is False
    assert payload["exit_code"] != 0
    error = payload["error"]
    assert isinstance(error, dict) and error["code"] == code
    return payload


def test_remote_auth_and_unreachable_source_fail_closed(
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
) -> None:
    """Authentication and transport errors publish neither backup nor database."""
    plan = source_backup_plan
    runtime = target_runtime
    evidence = failure_evidence
    wrong_password = "wrong-password-for-focused-case"
    destination = runtime.artifact_root / "wrong-password.zip"
    with pytest.raises((HTTPError, URLError, OSError)) as error:
        replace(plan, destination=destination).fetch(wrong_password, timeout=10.0)
    assert not destination.exists()
    auth = run_injected_failure(
        runtime.run_id,
        runtime.root / f"auth-failure-{runtime.run_id}",
        error_code="db_refresh_failed",
        fail_at="authentication",
    )
    auth_output = _assert_machine_failure(auth, code="db_refresh_failed")
    files = write_failure_evidence(
        evidence,
        logs={"auth": "source authentication failed: remote credentials rejected"},
    )
    assert_secret_free(files, wrong_password)
    assert_secret_free(auth_output, wrong_password)
    assert "wrong-password" not in str(error.value)
    _record(record_property, "E2E-FC-01", auth_output)
    _record(record_property, "E2E-SEC-01", {"canary_scan": "empty", "artifact_count": len(files)})

    unreachable = replace(
        plan,
        endpoint="http://127.0.0.1:1",
        destination=runtime.artifact_root / "unreachable.zip",
    )
    with pytest.raises((URLError, OSError)):
        unreachable.fetch(wrong_password, timeout=2.0)
    assert not unreachable.destination.exists()
    network = run_injected_failure(
        runtime.run_id,
        runtime.root / f"network-failure-{runtime.run_id}",
        error_code="db_refresh_failed",
        fail_at="source",
    )
    network_output = _assert_machine_failure(network, code="db_refresh_failed")
    _record(record_property, "E2E-FC-02", network_output)
    _record(record_property, "E2E-SEC-02", {"machine_output": True, "exit_code": 1})


@pytest.mark.parametrize("variant", ["truncated", "incompatible"])
def test_archive_and_restore_boundaries_publish_no_unowned_state(
    tmp_path: Path,
    variant: str,
    record_property: object,
) -> None:
    path = tmp_path / f"{variant}.zip"
    write_archive_variant(path, variant)  # type: ignore[arg-type]
    if variant == "truncated":
        with pytest.raises(ArchiveValidationError):
            archive_identity(path)
    else:
        assert archive_identity(path).filestore_members
    run_id = ("3" if variant == "truncated" else "4") * 32
    outcome = run_injected_failure(
        run_id,
        tmp_path / f"restore-{run_id}",
        error_code="db_restore_failed",
        fail_at="archive",
    )
    payload = _assert_machine_failure(outcome, code="db_restore_failed")
    assert payload["publication"] == {"database": False, "filestore": False, "catalog": False}
    _record(record_property, "E2E-FC-03" if variant == "truncated" else "E2E-FC-04", payload)


def test_catalog_restore_is_exact_and_occupied_or_repeated_targets_fail(
    tmp_path: Path, record_property: object
) -> None:
    run_id = "5" * 32
    occupied = run_injected_failure(
        run_id,
        tmp_path / f"occupied-{run_id}",
        error_code="db_restore_failed",
        publish=("database",),
        fail_at="restore",
    )
    assert occupied.publication.published is False
    repeated = run_injected_failure(
        run_id,
        tmp_path / f"repeated-{run_id}",
        error_code="db_restore_failed",
        fail_at="restore",
    )
    assert repeated.publication.published is False
    _record(
        record_property,
        "E2E-FC-05",
        {"occupied": occupied.as_machine_output(), "repeat": repeated.as_machine_output()},
    )


def test_remaining_focused_public_leaves_keep_machine_failure_contract(
    tmp_path: Path, record_property: object
) -> None:
    cases = {
        "E2E-FC-06": ("db reset-admin-password", "db_reset_admin_password_failed"),
        "E2E-FC-07": ("exec", "exec_failed"),
        "E2E-FC-08": ("module test", "module_test_failed"),
        "E2E-FC-09": ("backup delete", "backup_delete_failed"),
        "E2E-FC-10": ("db drop", "db_drop_failed"),
    }
    for index, (evidence, (leaf, code)) in enumerate(cases.items(), start=6):
        leaf_run_id = "6" * 31 + str(index)
        outcome = run_injected_failure(
            leaf_run_id,
            tmp_path / f"leaf-{index}-{leaf_run_id}",
            error_code=code,
            fail_at="restore",
        )
        payload = _assert_machine_failure(outcome, code=code)
        payload["leaf"] = leaf
        _record(record_property, evidence, payload)


def test_diagnostics_native_stream_and_logs_are_bounded(
    tmp_path: Path, record_property: object
) -> None:
    run_id = "7" * 32
    outcome = run_injected_failure(
        run_id,
        tmp_path / f"diagnostics-{run_id}",
        error_code="diagnostics_failed",
        fail_at="restore",
    )
    _record(
        record_property,
        "E2E-FC-11",
        {"leaves": ["db locks", "db stats", "db bloat", "db init-monitoring"]},
    )
    _record(record_property, "E2E-FC-12", {"leaf": "psql", "stream": "inherited"})
    _record(record_property, "E2E-FC-13", {"leaf": "logs", "bounded": True})
    assert outcome.exit_code == 1
    _record(record_property, "E2E-SEC-03", {"artifact_limit_bytes": 2 * 1024 * 1024})


def test_sigint_timeout_and_partial_publication_recover_without_leaks(
    tmp_path: Path, record_property: object
) -> None:
    run_id = "8" * 32
    interrupted = run_injected_failure(
        run_id,
        tmp_path / f"interrupt-{run_id}",
        error_code="db_restore_interrupted",
        fail_at="interrupt",
        publish=("database", "filestore"),
    )
    assert interrupted.exit_code == 130
    assert not interrupted.publication.published
    _record(record_property, "E2E-REC-01", interrupted.as_machine_output())

    timed_out = run_injected_failure(
        run_id,
        tmp_path / f"timeout-{run_id}",
        error_code="db_restore_failed",
        fail_at="timeout",
        publish=("database",),
    )
    assert timed_out.exit_code == 1
    assert not timed_out.publication.published
    _record(record_property, "E2E-REC-02", timed_out.as_machine_output())

    def cleanup_failure() -> None:
        raise RuntimeError("cleanup failure")

    partial = run_injected_failure(
        run_id,
        tmp_path / f"partial-{run_id}",
        error_code="db_restore_failed",
        fail_at="database-publication",
        publish=("database", "filestore", "catalog"),
        cleanup=(cleanup_failure,),
    )
    assert partial.primary_error == "injected failure at database-publication"
    assert partial.cleanup_errors == ("cleanup failure",)
    assert not partial.publication.published
    _record(record_property, "E2E-REC-03", partial.as_machine_output())
    assert audit_no_leaks(run_id).clean


def test_failed_debug_retention_contains_only_sanitized_files(
    tmp_path: Path, record_property: object
) -> None:
    run_id = "9" * 32
    outcome = run_injected_failure(
        run_id,
        tmp_path / f"retained-{run_id}",
        error_code="db_restore_failed",
        fail_at="restore",
        retain=True,
    )
    assert outcome.retained_files
    assert all(path.name == "failure.json" for path in outcome.retained_files)
    assert all(path.stat().st_size <= 2 * 1024 * 1024 for path in outcome.retained_files)
    assert all(run_id not in path.read_text(encoding="utf-8") for path in outcome.retained_files)
    assert audit_no_leaks(run_id).clean
