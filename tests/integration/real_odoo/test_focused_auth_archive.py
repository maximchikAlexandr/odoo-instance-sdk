"""Focused authentication and archive-boundary scenarios for the full E2E tier."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

if TYPE_CHECKING:
    from click.testing import Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import BackupState
from tests.integration.real_odoo.archive import ArchiveIdentity, SourceBackupPlan
from tests.integration.real_odoo.cleanup import FailureEvidence
from tests.integration.real_odoo.conftest import E2ERuntime
from tests.integration.real_odoo.failures import (
    assert_secret_free,
    write_archive_variant,
    write_failure_evidence,
)
from tests.integration.real_odoo.focused_support import (
    BACKUP_ID as _BACKUP_ID,
    _approve_project_postgres,
    _bind_catalog_path,
    _catalog_for_project,
    _ensure_isolated_environment,
    _isolated_catalog,
    _project,
    _project_database_probe,
    catalog_state as _catalog_state,
    focused_catalog as _focused_catalog_fixture,
    focused_project as _focused_project_fixture,
    invoke_in_registered_worktree as _invoke_in_registered_worktree,
    observe_failure as _observe_failure,
    project_filestore,
    record as _record,
    seed_backup as _seed_backup,
)

pytestmark = [
    pytest.mark.real_odoo,
    pytest.mark.e2e_full,
    pytest.mark.serial,
    pytest.mark.timeout(300),
]
focused_catalog = _focused_catalog_fixture
focused_project = _focused_project_fixture


def test_remote_auth_and_unreachable_source_fail_closed(
    tmp_path: Path,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
    focused_project: Path,
    focused_catalog: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public refresh command must fail before creating a local backup."""
    runtime = target_runtime
    project = focused_project
    catalog_path = _isolated_catalog(focused_catalog, tmp_path)
    _ensure_isolated_environment(catalog_path, project)
    _bind_catalog_path(monkeypatch, catalog_path)
    runner = CliRunner()
    wrong_password = failure_evidence.secret_canary
    environment = {
        **runtime.environment,
        "HOME": str(catalog_path.parent.parent),
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_TEST_MASTER_PASSWORD": wrong_password,
        "ODCLI_E2E_KEEP_FAILED": "1",
    }
    auth = _invoke_in_registered_worktree(
        runner,
        cli,
        project,
        catalog_path,
        ["--project", str(project), "db", "refresh", "--format", "json"],
        environment,
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

    # The second project must be registered in the runtime catalog.  The
    # first invocation uses a patched isolated catalog, so restore the normal
    # path before creating the project and snapshot its registration separately
    # for the second public invocation.
    monkeypatch.undo()
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
    unreachable_source_catalog = _catalog_for_project(runtime, unreachable_project)
    unreachable_catalog = _isolated_catalog(
        unreachable_source_catalog, tmp_path / "unreachable-catalog"
    )
    _ensure_isolated_environment(unreachable_catalog, unreachable_project)
    _bind_catalog_path(monkeypatch, unreachable_catalog)
    unreachable_environment = {
        **runtime.environment,
        "HOME": str(unreachable_catalog.parent.parent),
        "ODCLI_E2E_CATALOG": str(unreachable_catalog),
        "ODCLI_TEST_MASTER_PASSWORD": wrong_password,
        "ODCLI_E2E_KEEP_FAILED": "1",
    }
    unreachable = _invoke_in_registered_worktree(
        runner,
        cli,
        unreachable_project,
        unreachable_catalog,
        ["--project", str(unreachable_project), "db", "refresh", "--format", "json"],
        unreachable_environment,
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


@dataclass(frozen=True, slots=True)
class _ArchiveBoundary:
    tmp_path: Path
    runtime: E2ERuntime
    source_plan: SourceBackupPlan
    evidence: FailureEvidence
    project: Path
    catalog: Path
    environment: dict[str, str]
    result: Result
    variant: str


def _archive_validation_boundary(
    tmp_path: Path,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    variant: str,
    monkeypatch: pytest.MonkeyPatch,
    focused_project: Path,
    focused_catalog: Path,
) -> _ArchiveBoundary:
    path = tmp_path / f"{variant}.zip"
    write_archive_variant(path, variant)  # type: ignore[arg-type]
    catalog_path = _isolated_catalog(focused_catalog, tmp_path)
    _seed_backup(
        catalog_path,
        path,
        database=source_backup_plan.database,
        source_base_url=source_backup_plan.endpoint,
    )
    environment = {
        **target_runtime.environment,
        "HOME": str(catalog_path.parent.parent),
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_TEST_MASTER_PASSWORD": failure_evidence.secret_canary,
    }
    _ensure_isolated_environment(catalog_path, focused_project)
    _approve_project_postgres(focused_project, environment)
    _bind_catalog_path(monkeypatch, catalog_path)
    result = _invoke_in_registered_worktree(
        CliRunner(),
        cli,
        focused_project,
        catalog_path,
        ["backup", "validate", _BACKUP_ID, "--format", "json"],
        environment,
    )
    return _ArchiveBoundary(
        tmp_path,
        target_runtime,
        source_backup_plan,
        failure_evidence,
        focused_project,
        catalog_path,
        environment,
        result,
        variant,
    )


def test_truncated_archive_is_rejected_without_unowned_publication(
    tmp_path: Path,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
    focused_project: Path,
    focused_catalog: Path,
) -> None:
    case = _archive_validation_boundary(
        tmp_path,
        source_backup_plan,
        target_runtime,
        failure_evidence,
        "truncated",
        monkeypatch,
        focused_project,
        focused_catalog,
    )
    assert case.result.exit_code != 0
    document, _ = _observe_failure(
        case.result, runtime=case.runtime, evidence=case.evidence, name="archive-truncated"
    )
    assert document is not None
    assert document["error"]["code"] == "backup_corrupt"
    assert _catalog_state(case.catalog) is BackupState.AVAILABLE
    assert not (case.tmp_path / "database").exists()
    assert not (case.tmp_path / "filestore").exists()
    _record(record_property, "E2E-FC-03", document)


def test_incompatible_archive_restore_is_rejected_without_unowned_publication(
    tmp_path: Path,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
    focused_project: Path,
    focused_catalog: Path,
) -> None:
    case = _archive_validation_boundary(
        tmp_path,
        source_backup_plan,
        target_runtime,
        failure_evidence,
        "incompatible",
        monkeypatch,
        focused_project,
        focused_catalog,
    )
    assert case.result.exit_code == 0, case.result.output
    document = json.loads(case.result.stdout)
    assert document["ok"] is True
    assert document["result"]["db_name"] == "not-the-catalogue-database"
    assert document["result"]["db_name"] != case.source_plan.database
    files = write_failure_evidence(case.evidence, logs={"archive-incompatible": case.result.stdout})
    assert_secret_free(files, case.evidence.secret_canary)
    target = f"odcli_incompatible_{case.runtime.run_id.replace('-', '')[:20]}"
    restore = _invoke_in_registered_worktree(
        CliRunner(),
        case.project,
        case.catalog,
        [
            "--project",
            str(case.project),
            "db",
            "restore",
            _BACKUP_ID,
            "--target",
            target,
            "--yes",
            "--format",
            "json",
        ],
        case.environment,
    )
    assert restore.exit_code != 0
    restore_document, _ = _observe_failure(
        restore,
        runtime=case.runtime,
        evidence=case.evidence,
        name="archive-incompatible-restore",
    )
    assert restore_document is not None
    assert restore_document["error"]["code"] == "db_restore_failed"
    assert "database" in restore_document["error"]["message"].lower()
    assert _catalog_state(case.catalog) is BackupState.AVAILABLE
    assert not project_filestore(case.project, target).exists()
    probe = _project_database_probe(case.project, target, home=case.runtime.environment["HOME"])
    assert probe.returncode == 0
    assert probe.stdout.strip() != target
    assert not (case.tmp_path / "database").exists()
    assert not (case.tmp_path / "filestore").exists()
    _record(record_property, "E2E-FC-04", document)


def test_catalog_restore_is_exact_and_occupied_or_repeated_targets_fail(
    tmp_path: Path,
    source_backup: ArchiveIdentity,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
    focused_project: Path,
    focused_catalog: Path,
) -> None:
    archive_path = tmp_path / "restore.zip"
    shutil.copy2(source_backup.path, archive_path)
    catalog_path = _isolated_catalog(focused_catalog, tmp_path)
    success_id = "00000000-0000-0000-0000-000000000008"
    _seed_backup(
        catalog_path,
        archive_path,
        backup_id=success_id,
        database=source_backup_plan.database,
        source_base_url=source_backup_plan.endpoint,
    )
    _bind_catalog_path(monkeypatch, catalog_path)
    project = focused_project
    target = f"odcli_restore_{target_runtime.run_id.replace('-', '')[:24]}"
    args = [
        "--project",
        str(project),
        "db",
        "restore",
        success_id,
        "--target",
        target,
        "--yes",
        "--format",
        "json",
    ]
    environment = {
        **target_runtime.environment,
        "HOME": str(catalog_path.parent.parent),
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_E2E_KEEP_FAILED": "1",
    }
    _ensure_isolated_environment(catalog_path, project)
    _approve_project_postgres(project, environment)
    successful = _invoke_in_registered_worktree(
        CliRunner(), cli, project, catalog_path, args, environment
    )
    assert successful.exit_code == 0, successful.output
    success_document = json.loads(successful.stdout)
    assert success_document["ok"] is True
    assert success_document["result"]["restored_database"] == target
    database_probe = _project_database_probe(
        project, target, home=target_runtime.environment["HOME"]
    )
    assert database_probe.returncode == 0
    assert database_probe.stdout.strip() == target
    filestore = project_filestore(project, target)
    assert filestore.is_dir()

    first = _invoke_in_registered_worktree(
        CliRunner(), cli, project, catalog_path, args, environment
    )
    second = _invoke_in_registered_worktree(
        CliRunner(), cli, project, catalog_path, args, environment
    )
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
    assert _catalog_state(catalog_path, success_id) is BackupState.AVAILABLE
    assert database_probe.stdout.strip() == target
    assert filestore.is_dir()
    _record(
        record_property,
        "E2E-FC-05",
        {"success": success_document, "occupied": first_doc, "repeated": second_doc},
    )
