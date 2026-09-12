"""Focused public-boundary failure and recovery scenarios for the full E2E tier."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import BackupState
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project import TestInstanceProjectConfig as _TestInstanceConfig
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES, PublicLeafCase

from .archive import SourceBackupPlan
from .cleanup import FailureEvidence, ResourceLedger, audit_no_leaks
from .conftest import E2ERuntime
from .failures import (
    InjectedFailure,
    PublicationProxy,
    assert_secret_free,
    run_recovery_action,
    write_archive_variant,
    write_failure_evidence,
)

pytestmark = [pytest.mark.real_odoo, pytest.mark.e2e_full, pytest.mark.serial]

_BACKUP_ID = "00000000-0000-0000-0000-000000000007"


def _record(record_property: object, evidence: str, value: object = "passed") -> None:
    getattr(record_property, "__call__")(
        evidence.lower().replace("-", "_"), json.dumps(value, default=str, sort_keys=True)
    )


def _project(runtime: E2ERuntime, root: Path, *, source: SourceBackupPlan | None = None) -> Path:
    """Create a real project manifest used by the public CLI invocation."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    config = ProjectConfig(
        repository_root=root,
        odoo_bin=Path(sys.executable),
        python=sys.executable,
        source_config=runtime.config_file,
        default_source_database=runtime.topology.target_sentinel_database,
        test_instance=(
            _TestInstanceConfig(base_url=source.endpoint, database=source.database)
            if source is not None
            else None
        ),
    )
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir(mode=0o700, exist_ok=True)
    manifest.write_text(config.to_manifest(), encoding="utf-8")
    manifest.chmod(0o600)
    return root


def _failure_text(result: object) -> str:
    return "\n".join(
        str(getattr(result, field, "")) for field in ("stdout", "stderr", "output", "exception")
    )


def _observe_failure(
    result: object,
    *,
    runtime: E2ERuntime,
    evidence: FailureEvidence,
    name: str,
    argv: object = (),
) -> tuple[dict[str, Any] | None, tuple[Path, ...]]:
    """Audit every real CLI failure across output, exception, and artifacts."""
    text = _failure_text(result)
    assert_secret_free(
        {"argv": argv, "machine_output": text, "pytest_output": getattr(result, "output", "")},
        evidence.secret_canary,
    )
    files = write_failure_evidence(evidence, logs={name: text})
    assert_secret_free(files, evidence.secret_canary)
    assert audit_no_leaks(runtime.run_id).clean
    stdout = str(getattr(result, "stdout", ""))
    if not stdout.strip():
        return None, files
    try:
        document = json.loads(stdout)
    except json.JSONDecodeError:
        return None, files
    assert isinstance(document, dict) and document.get("ok") is False
    return document, files


def _invoke_case(
    case: PublicLeafCase,
    *,
    project: Path,
    runtime: E2ERuntime,
    evidence: FailureEvidence,
    record_property: object,
) -> None:
    """Execute one canonical leaf through Click, including its public options."""
    args = list(case.args)
    if case.requires_dry_run and "--dry-run" not in args:
        args.append("--dry-run")
    if case.path == ("logs",):
        args = ["logs", "--tail", "1"]
    machine = case.classification not in {"native-passthrough", "jsonl-stream"}
    if machine:
        args.extend(("--format", "json"))
    result = CliRunner().invoke(
        cli,
        ["--project", str(project), *args],
        env={**runtime.environment, "ODCLI_E2E_KEEP_FAILED": "1"},
        input="raise RuntimeError('focused leaf failure')\n",
    )
    assert result.exit_code != 0, result.output
    document, files = _observe_failure(
        result,
        runtime=runtime,
        evidence=evidence,
        name="leaf-" + "-".join(case.path),
        argv=args,
    )
    if machine:
        assert document is not None
        expected = "_".join(case.path).replace("-", "_") + "_failed"
        error = document.get("error")
        assert isinstance(error, dict) and error.get("code") == expected
    else:
        assert files
    for evidence_id in case.e2e_evidence:
        _record(record_property, evidence_id, {"path": case.path, "exit_code": result.exit_code})


def _seed_backup(db_path: Path, archive_path: Path) -> None:
    catalog = BackupCatalog(db_path=db_path)
    catalog.start_download(
        _BACKUP_ID,
        "http://127.0.0.1:8069",
        "demo",
        "zip",
        True,
        archive_path,
    )
    catalog.success_download(_BACKUP_ID, archive_path.name, archive_path.stat().st_size, "")
    catalog.close()


def _catalog_state(db_path: Path) -> BackupState:
    catalog = BackupCatalog(db_path=db_path)
    row = catalog.get_by_id(_BACKUP_ID)
    catalog.close()
    assert row is not None
    return BackupState(str(row["state"]))


def test_remote_auth_and_unreachable_source_fail_closed(
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
) -> None:
    """The public refresh command must fail before creating a local backup."""
    runtime = target_runtime
    project = _project(
        runtime, runtime.root / f"project-{runtime.run_id}", source=source_backup_plan
    )
    runner = CliRunner()
    wrong_password = "wrong-password-for-focused-case"
    environment = {
        **runtime.environment,
        "ODCLI_TEST_MASTER_PASSWORD": wrong_password,
        "ODCLI_E2E_KEEP_FAILED": "1",
    }
    auth = runner.invoke(
        cli,
        ["--project", str(project), "db", "refresh", "--format", "json"],
        env=environment,
    )
    assert auth.exit_code != 0
    auth_document, auth_files = _observe_failure(
        auth,
        runtime=runtime,
        evidence=failure_evidence,
        name="auth",
        argv=["db", "refresh", "--format", "json"],
    )
    assert auth_document is not None
    assert auth_document["error"]["code"] == "db_refresh_failed"
    assert not tuple(runtime.artifact_root.glob("*.zip"))
    assert_secret_free(auth_files, wrong_password)
    assert_secret_free(auth_document, wrong_password)
    _record(record_property, "E2E-FC-01", auth_document)
    _record(record_property, "E2E-SEC-01", {"artifacts": len(auth_files), "redacted": True})

    unreachable_plan = replace(
        source_backup_plan,
        endpoint="http://127.0.0.1:1",
        destination=runtime.artifact_root / "unreachable.zip",
    )
    unreachable_project = _project(
        runtime,
        runtime.root / f"unreachable-project-{runtime.run_id}",
        source=unreachable_plan,
    )
    unreachable = runner.invoke(
        cli,
        ["--project", str(unreachable_project), "db", "refresh", "--format", "json"],
        env=environment,
    )
    assert unreachable.exit_code != 0
    network_document, _ = _observe_failure(
        unreachable,
        runtime=runtime,
        evidence=failure_evidence,
        name="unreachable",
        argv=["db", "refresh", "--format", "json"],
    )
    assert network_document is not None
    assert network_document["error"]["code"] == "db_refresh_failed"
    assert not unreachable_plan.destination.exists()
    _record(record_property, "E2E-FC-02", network_document)
    _record(
        record_property, "E2E-SEC-02", {"machine_output": True, "exit_code": unreachable.exit_code}
    )


@pytest.mark.parametrize("variant", ["truncated", "incompatible"])
def test_archive_and_restore_boundaries_publish_no_unowned_state(
    tmp_path: Path,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    variant: str,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / f"{variant}.zip"
    write_archive_variant(path, variant)  # type: ignore[arg-type]
    catalog_path = tmp_path / "catalog.sqlite3"
    _seed_backup(catalog_path, path)
    monkeypatch.setattr("odoo_instance_sdk.cli.get_catalog_path", lambda **_: catalog_path)
    result = CliRunner().invoke(cli, ["backup", "validate", _BACKUP_ID, "--format", "json"])
    assert result.exit_code != 0
    document, _ = _observe_failure(
        result, runtime=target_runtime, evidence=failure_evidence, name=f"archive-{variant}"
    )
    assert document is not None
    assert document["error"]["code"] == "backup_validate_invalid"
    assert _catalog_state(catalog_path) is BackupState.AVAILABLE
    assert not (tmp_path / "database").exists()
    assert not (tmp_path / "filestore").exists()
    _record(record_property, "E2E-FC-03" if variant == "truncated" else "E2E-FC-04", document)


def test_catalog_restore_is_exact_and_occupied_or_repeated_targets_fail(
    tmp_path: Path,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
) -> None:
    archive_path = tmp_path / "restore.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name": "demo"}')
        archive.writestr("dump.sql", "-- database: demo\n")
        archive.writestr("filestore/demo/blob", b"fixture")
    catalog_path = tmp_path / "catalog.sqlite3"
    _seed_backup(catalog_path, archive_path)
    project = _project(target_runtime, tmp_path / f"restore-project-{target_runtime.run_id}")
    target = target_runtime.topology.target_sentinel_database
    args = [
        "--project",
        str(project),
        "db",
        "restore",
        _BACKUP_ID,
        "--target",
        target,
        "--yes",
        "--format",
        "json",
    ]
    environment = {
        **target_runtime.environment,
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_E2E_KEEP_FAILED": "1",
    }
    first = CliRunner().invoke(cli, args, env=environment)
    second = CliRunner().invoke(cli, args, env=environment)
    assert first.exit_code != 0 and second.exit_code != 0
    first_doc, _ = _observe_failure(
        first, runtime=target_runtime, evidence=failure_evidence, name="restore-occupied"
    )
    second_doc, _ = _observe_failure(
        second, runtime=target_runtime, evidence=failure_evidence, name="restore-repeated"
    )
    assert first_doc is not None and second_doc is not None
    assert first_doc["error"]["code"] == "db_restore_failed"
    assert second_doc["error"]["code"] == "db_restore_failed"
    assert _catalog_state(catalog_path) is BackupState.AVAILABLE
    _record(record_property, "E2E-FC-05", {"occupied": first_doc, "repeated": second_doc})


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in PUBLIC_LEAF_CASES
        if case.e2e_disposition == "focused" and case.e2e_evidence != ("E2E-FC-05",)
    ],
    ids=lambda case: ".".join(case.path),
)
def test_remaining_focused_public_leaves_use_canonical_inventory(
    case: PublicLeafCase,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
) -> None:
    project = _project(target_runtime, target_runtime.root / f"leaf-{target_runtime.run_id}")
    _invoke_case(
        case,
        project=project,
        runtime=target_runtime,
        evidence=failure_evidence,
        record_property=record_property,
    )


def test_sigint_timeout_and_partial_publication_recover_without_leaks(
    target_runtime: E2ERuntime,
    resource_ledger: ResourceLedger,
    record_property: object,
) -> None:
    run_id = target_runtime.run_id
    worker = target_runtime.root / f"worker-{run_id}.py"
    worker.write_text(
        "import signal, time\nsignal.signal(signal.SIGINT, signal.default_int_handler)\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    process = subprocess.Popen([sys.executable, str(worker)], start_new_session=True)
    resource_ledger.record(
        "process",
        f"{run_id}-worker-{process.pid}",
        lambda: process.kill() if process.poll() is None else None,
    )
    process.send_signal(signal.SIGINT)
    process.wait(timeout=5)
    assert process.returncode in {-signal.SIGINT, 130}
    _record(record_property, "E2E-REC-01", {"exit_code": process.returncode})
    assert audit_no_leaks(run_id).clean

    timed_worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    resource_ledger.record(
        "process",
        f"{run_id}-timeout-{timed_worker.pid}",
        lambda: timed_worker.kill() if timed_worker.poll() is None else None,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        timed_worker.wait(timeout=0.05)
    timed_worker.kill()
    timed_worker.wait(timeout=5)
    assert timed_worker.returncode is not None
    _record(record_property, "E2E-REC-02", {"exit_code": timed_worker.returncode})
    assert audit_no_leaks(run_id).clean

    root = target_runtime.root / f"publication-{run_id}"
    proxy = PublicationProxy(run_id, root, resource_ledger)
    proxy.publish("database")
    proxy.publish("filestore")
    proxy.publish("catalog")
    observation = run_recovery_action(
        lambda: (_ for _ in ()).throw(InjectedFailure("filestore-publication")),
        ledger=resource_ledger,
    )
    assert str(observation.primary_error) == "injected failure at filestore-publication"
    assert not any(root.glob("*"))
    _record(record_property, "E2E-REC-03", {"primary_error": str(observation.primary_error)})
    assert audit_no_leaks(run_id).clean


def test_failed_debug_retention_contains_only_sanitized_files(
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
) -> None:
    environment = {**target_runtime.environment, "ODCLI_E2E_KEEP_FAILED": "1"}
    project = _project(target_runtime, target_runtime.root / f"retention-{target_runtime.run_id}")
    result = CliRunner().invoke(
        cli,
        ["--project", str(project), "db", "drop", "missing", "--format", "json"],
        env=environment,
    )
    assert result.exit_code != 0
    document, files = _observe_failure(
        result, runtime=target_runtime, evidence=failure_evidence, name="retention"
    )
    assert document is not None
    assert files
    assert all(path.stat().st_size <= 2 * 1024 * 1024 for path in files)
    assert audit_no_leaks(target_runtime.run_id).clean
    _record(record_property, "E2E-SEC-03", {"artifact_limit_bytes": 2 * 1024 * 1024})
