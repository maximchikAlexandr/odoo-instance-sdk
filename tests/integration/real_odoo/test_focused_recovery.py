"""Focused recovery and retention scenarios for the full E2E tier."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import BackupState
from odoo_instance_sdk.resources.database import DatabaseResource
from tests.integration.real_odoo.archive import ArchiveIdentity, SourceBackupPlan
from tests.integration.real_odoo.cleanup import (
    FailureEvidence,
    ResourceLedger,
    terminate_owned_process_group,
)
from tests.integration.real_odoo.compose import ComposeLifecycle
from tests.integration.real_odoo.conftest import E2ERuntime, _finalize
from tests.integration.real_odoo.failures import (
    assert_secret_free,
    run_recovery_action,
    write_failure_evidence,
)
from tests.integration.real_odoo.focused_support import (
    _approve_project_postgres,
    _catalog_for_project,
    _ensure_isolated_environment,
    _isolated_catalog,
    _project,
    _project_database_probe,
    project_filestore,
)
from tests.integration.real_odoo.focused_support import (
    catalog_state as _catalog_state,
)
from tests.integration.real_odoo.focused_support import (
    invoke_in_registered_worktree as _invoke_in_registered_worktree,
)
from tests.integration.real_odoo.focused_support import (
    observe_failure as _observe_failure,
)
from tests.integration.real_odoo.focused_support import (
    record as _record,
)
from tests.integration.real_odoo.focused_support import (
    registered_worktree as _registered_worktree,
)
from tests.integration.real_odoo.focused_support import (
    seed_backup as _seed_backup,
)

pytestmark = [
    pytest.mark.real_odoo,
    pytest.mark.e2e_full,
    pytest.mark.serial,
    pytest.mark.timeout(300),
]

_RECOVERY_BACKUP_IDS = {
    "recovery": "00000000-0000-0000-0000-000000000009",
    "recovery-timeout": "00000000-0000-0000-0000-00000000000a",
    "partial": "00000000-0000-0000-0000-00000000000b",
}


def _prepare_recovery_case(
    tmp_path: Path,
    target_runtime: E2ERuntime,
    source_backup: ArchiveIdentity,
    source_backup_plan: SourceBackupPlan,
    failure_evidence: FailureEvidence,
    name: str,
) -> tuple[str, Path, str, Path, dict[str, str]]:
    run_id = target_runtime.run_id
    archive_path = target_runtime.artifact_root / f"{name}-{run_id}.zip"
    shutil.copy2(source_backup.path, archive_path)
    backup_id = _RECOVERY_BACKUP_IDS[name]
    catalog_path = Path(target_runtime.environment["ODCLI_E2E_CATALOG"])
    _seed_backup(
        catalog_path,
        archive_path,
        backup_id=backup_id,
        database=source_backup_plan.database,
    )
    project = _project(target_runtime, target_runtime.root / f"{name}-{run_id}")
    catalog_path = _isolated_catalog(
        _catalog_for_project(target_runtime, project), tmp_path / f"{name}-catalog"
    )
    _ensure_isolated_environment(catalog_path, project)
    environment = {
        **os.environ,
        **target_runtime.environment,
        "HOME": str(catalog_path.parent.parent),
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_TEST_MASTER_PASSWORD": failure_evidence.secret_canary,
    }
    _approve_project_postgres(project, environment)
    return run_id, archive_path, backup_id, project, environment


def test_sigint_recovery_without_leaks(
    tmp_path: Path,
    target_runtime: E2ERuntime,
    source_backup: ArchiveIdentity,
    source_backup_plan: SourceBackupPlan,
    failure_evidence: FailureEvidence,
    resource_ledger: ResourceLedger,
    record_property: object,
) -> None:
    run_id, _archive_path, backup_id, project, environment = _prepare_recovery_case(
        tmp_path,
        target_runtime,
        source_backup,
        source_backup_plan,
        failure_evidence,
        "recovery",
    )
    target = f"odcli_interrupt_{run_id.replace('-', '')[:20]}"
    command = shutil.which("odcli")
    if command is None:
        pytest.fail("odcli executable is required for public recovery boundaries")
    catalog_path = Path(environment["ODCLI_E2E_CATALOG"])
    process = subprocess.Popen(
        [
            command,
            "--project",
            str(project),
            "db",
            "restore",
            backup_id,
            "--target",
            target,
            "--yes",
            "--format",
            "json",
        ],
        cwd=_registered_worktree(catalog_path, project),
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    resource_ledger.record(
        "process",
        f"{run_id}-odcli-restore-{process.pid}",
        lambda: terminate_owned_process_group(process.pid),
    )
    deadline = time.monotonic() + 5
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGINT)
    stdout, stderr = process.communicate(timeout=30)
    assert_secret_free({"stdout": stdout, "stderr": stderr}, failure_evidence.secret_canary)
    assert process.returncode == 130, stdout + stderr
    interrupted = json.loads(stdout)
    assert interrupted["error"]["code"] == "db_restore_interrupted"
    assert_secret_free(
        {"argv": command, "machine_output": stdout, "pytest_output": stderr},
        failure_evidence.secret_canary,
    )
    _record(record_property, "E2E-REC-01", interrupted)
    files = write_failure_evidence(
        failure_evidence,
        logs={"recovery-interrupt": stdout},
    )
    assert_secret_free(files, failure_evidence.secret_canary)


def test_timeout_recovery_without_leaks(
    tmp_path: Path,
    target_runtime: E2ERuntime,
    source_backup: ArchiveIdentity,
    source_backup_plan: SourceBackupPlan,
    failure_evidence: FailureEvidence,
    resource_ledger: ResourceLedger,
    record_property: object,
) -> None:
    run_id, _archive_path, _backup_id, project, environment = _prepare_recovery_case(
        tmp_path,
        target_runtime,
        source_backup,
        source_backup_plan,
        failure_evidence,
        "recovery-timeout",
    )
    command = shutil.which("odcli")
    if command is None:
        pytest.fail("odcli executable is required for public recovery boundaries")
    catalog_path = Path(environment["ODCLI_E2E_CATALOG"])
    timeout_process = subprocess.Popen(
        [
            command,
            "--project",
            str(project),
            "db",
            "init-monitoring",
            target_runtime.topology.target_sentinel_database,
            "--yes",
            "--timeout",
            "0.001",
            "--format",
            "json",
        ],
        cwd=_registered_worktree(catalog_path, project),
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    resource_ledger.record(
        "process",
        f"{run_id}-odcli-timeout-{timeout_process.pid}",
        lambda: terminate_owned_process_group(timeout_process.pid),
    )
    timeout_stdout, timeout_stderr = timeout_process.communicate(timeout=30)
    assert timeout_process.returncode != 0
    timeout_document = json.loads(timeout_stdout)
    assert timeout_document["ok"] is False
    assert timeout_document["error"]["code"] == "db_init-monitoring_failed"
    assert "timeout" in timeout_document["error"]["message"].lower()
    assert_secret_free(
        {"argv": command, "machine_output": timeout_stdout, "pytest_output": timeout_stderr},
        failure_evidence.secret_canary,
    )
    _record(
        record_property,
        "E2E-REC-02",
        timeout_document,
    )
    files = write_failure_evidence(
        failure_evidence,
        logs={"recovery-timeout": timeout_stdout},
    )
    assert_secret_free(files, failure_evidence.secret_canary)


def test_partial_publication_recovery_without_leaks(
    tmp_path: Path,
    target_runtime: E2ERuntime,
    source_backup: ArchiveIdentity,
    source_backup_plan: SourceBackupPlan,
    failure_evidence: FailureEvidence,
    resource_ledger: ResourceLedger,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, archive_path, backup_id, partial_project, environment = _prepare_recovery_case(
        tmp_path,
        target_runtime,
        source_backup,
        source_backup_plan,
        failure_evidence,
        "partial",
    )
    catalog_path = Path(environment["ODCLI_E2E_CATALOG"])
    database = f"odcli_partial_{run_id.replace('-', '')[:20]}"
    partial_args = [
        "--project",
        str(partial_project),
        "db",
        "restore",
        backup_id,
        "--target",
        database,
        "--yes",
        "--format",
        "json",
    ]
    partial_document: dict[str, Any] = {}
    scenario_ledger = ResourceLedger(run_id)
    resource_ledger.record(
        "recovery-scenario",
        f"{run_id}-recovery-scenario",
        scenario_ledger.unwind,
    )

    original_restore = DatabaseResource.restore

    def restore_then_fail(
        resource: DatabaseResource, *restore_args: Any, **restore_kwargs: Any
    ) -> Any:
        original_restore(resource, *restore_args, **restore_kwargs)
        raise RuntimeError("injected restore failure after database and filestore publication")

    monkeypatch.setattr(DatabaseResource, "restore", restore_then_fail)
    filestore = project_filestore(partial_project, database)

    def public_restore_failure() -> None:
        failed = _invoke_in_registered_worktree(
            CliRunner(), cli, partial_project, catalog_path, partial_args, environment
        )
        assert failed.exit_code != 0
        observed, _ = _observe_failure(
            failed,
            runtime=target_runtime,
            evidence=failure_evidence,
            name="recovery-partial-public-restore",
            argv=partial_args,
        )
        assert observed is not None
        assert filestore.is_dir()
        partial_document.update(observed)
        raise RuntimeError(observed["error"]["message"])

    def drop_database() -> None:
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        with pytest.MonkeyPatch.context() as paths:
            paths.setenv("HOME", target_runtime.environment["HOME"])
            cluster = PostgresCluster.from_project(partial_project)
            lifecycle = ComposeLifecycle(cluster.compose_file, cluster.compose_project_name)
        result = lifecycle.run(
            "exec", "-T", "postgres", "dropdb", "-U", "odoo", database, timeout=30.0
        )
        if result.returncode != 0:
            raise RuntimeError("database cleanup failed")

    scenario_ledger.record(
        "database",
        f"{run_id}-{database}",
        drop_database,
    )
    scenario_ledger.record("filestore", f"{run_id}-filestore", lambda: shutil.rmtree(filestore))

    def delete_catalog_record() -> None:
        deleted = _invoke_in_registered_worktree(
            CliRunner(),
            cli,
            partial_project,
            catalog_path,
            [
                "--project",
                str(partial_project),
                "backup",
                "delete",
                backup_id,
                "--yes",
                "--format",
                "json",
            ],
            environment,
        )
        if deleted.exit_code != 0:
            raise RuntimeError(deleted.output)

    scenario_ledger.record("catalog", f"{run_id}-catalog", delete_catalog_record)
    scenario_ledger.record(
        "cleanup",
        f"{run_id}-injected-cleanup",
        lambda: (_ for _ in ()).throw(RuntimeError("cleanup failure")),
    )
    observation = run_recovery_action(
        public_restore_failure,
        ledger=scenario_ledger,
    )
    assert str(observation.primary_error) == partial_document["error"]["message"]
    assert observation.cleanup_errors == ("cleanup failure",)
    assert not filestore.exists()
    assert _catalog_state(catalog_path, backup_id) is BackupState.DELETED
    assert not archive_path.exists()
    database_probe = _project_database_probe(
        partial_project, database, home=target_runtime.environment["HOME"]
    )
    assert database_probe.returncode == 0
    assert database_probe.stdout.strip() != database
    _record(
        record_property,
        "E2E-REC-03",
        {
            "primary_error": str(observation.primary_error),
            "cleanup_errors": observation.cleanup_errors,
        },
    )
    files = write_failure_evidence(
        failure_evidence,
        logs={
            "recovery-partial": json.dumps(partial_document, sort_keys=True),
        },
    )
    assert_secret_free(files, failure_evidence.secret_canary)


def test_failed_debug_retention_contains_only_sanitized_files(
    tmp_path: Path,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {
        **target_runtime.environment,
        "ODCLI_E2E_KEEP_FAILED": "1",
        "ODCLI_TEST_MASTER_PASSWORD": failure_evidence.secret_canary,
    }
    project = _project(
        target_runtime,
        target_runtime.root / f"retention-{target_runtime.run_id}",
        source=source_backup_plan,
    )
    catalog_path = _isolated_catalog(_catalog_for_project(target_runtime, project), tmp_path)
    _ensure_isolated_environment(catalog_path, project)
    environment["HOME"] = str(catalog_path.parent.parent)
    environment["ODCLI_E2E_CATALOG"] = str(catalog_path)
    result = _invoke_in_registered_worktree(
        CliRunner(),
        cli,
        project,
        catalog_path,
        ["--project", str(project), "db", "refresh", "--format", "json"],
        environment,
    )
    assert result.exit_code != 0
    document, files = _observe_failure(
        result, runtime=target_runtime, evidence=failure_evidence, name="retention"
    )
    assert document is not None
    assert files
    monkeypatch.setenv("ODCLI_E2E_KEEP_FAILED", "1")
    expected_failure = RuntimeError(document["error"]["message"])
    with pytest.raises(RuntimeError) as raised:
        _finalize(target_runtime, expected_failure)
    assert raised.value is expected_failure
    retained = tuple(failure_evidence.root.iterdir())
    assert retained
    assert_secret_free(retained, failure_evidence.secret_canary)
    assert all(path.parent == failure_evidence.root for path in retained)
    assert all(path.suffix in {".log", ".json"} for path in retained)
    assert all(path.stat().st_size <= 2 * 1024 * 1024 for path in files)
    _record(record_property, "E2E-SEC-03", {"artifact_limit_bytes": 2 * 1024 * 1024})
