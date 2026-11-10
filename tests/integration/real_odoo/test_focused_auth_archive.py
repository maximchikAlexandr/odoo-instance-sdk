"""Focused authentication and archive-boundary scenarios for the full E2E tier."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest
from click.testing import CliRunner, Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessResult,
    StepObserver,
    SubprocessExecutor,
)
from odoo_instance_sdk.models import BackupState
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster
from tests.integration.real_odoo.archive import ArchiveIdentity, SourceBackupPlan
from tests.integration.real_odoo.cleanup import FailureEvidence
from tests.integration.real_odoo.conftest import E2ERuntime
from tests.integration.real_odoo.credential_binding_diagnostics import (
    CredentialBinding,
    capture_binding,
    capture_project_binding,
    compare_bindings,
)
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
    focused_cli_environment as _focused_cli_environment,
    focused_project as _focused_project_fixture,
    invoke_in_registered_worktree as _invoke_in_registered_worktree,
    observe_failure as _observe_failure,
    project_database_oid,
    project_filestore,
    record as _record,
    seed_backup as _seed_backup,
)
from tests.integration.real_odoo.inventory_diagnostics import (
    InventoryDiagnostic,
    inventory_diagnostic,
    inventory_step_id,
)

pytestmark = [
    pytest.mark.real_odoo,
    pytest.mark.e2e_full,
    pytest.mark.serial,
    pytest.mark.timeout(300),
]
focused_catalog = _focused_catalog_fixture
focused_project = _focused_project_fixture


@dataclass(frozen=True)
class _ArchiveBoundaryInputs:
    source_database: str
    target_runtime: E2ERuntime
    failure_evidence: FailureEvidence
    focused_project: Path
    catalog_path: Path
    backup_id: str
    environment: dict[str, str]


@dataclass(frozen=True)
class _ArchiveBoundaryCase:
    variant: str
    evidence_id: str
    exercise: Callable[[_ArchiveBoundaryInputs], dict[str, object]]


def _assert_incompatible_archive_rejected(inputs: _ArchiveBoundaryInputs) -> dict[str, object]:
    validation = _invoke_in_registered_worktree(
        CliRunner(),
        cli,
        inputs.focused_project,
        inputs.catalog_path,
        ["backup", "validate", inputs.backup_id, "--format", "json"],
        inputs.environment,
    )
    assert validation.exit_code == 0, validation.output
    document = json.loads(validation.stdout)
    assert document["ok"] is True
    assert document["result"]["db_name"] == "not-the-catalogue-database"
    assert document["result"]["db_name"] != inputs.source_database
    files = write_failure_evidence(
        inputs.failure_evidence, logs={"archive-incompatible": validation.stdout}
    )
    assert_secret_free(files, inputs.failure_evidence.secret_canary)

    target = f"odcli_incompatible_{inputs.target_runtime.run_id.replace('-', '')[:20]}"
    restore = _invoke_in_registered_worktree(
        CliRunner(),
        cli,
        inputs.focused_project,
        inputs.catalog_path,
        [
            "--project",
            str(inputs.focused_project),
            "db",
            "restore",
            inputs.backup_id,
            "--target",
            target,
            "--yes",
            "--format",
            "json",
        ],
        inputs.environment,
    )
    assert restore.exit_code != 0
    restore_document, _ = _observe_failure(
        restore,
        runtime=inputs.target_runtime,
        evidence=inputs.failure_evidence,
        name="archive-incompatible-restore",
    )
    assert restore_document is not None
    assert restore_document["error"]["code"] == "db_restore_failed"
    assert "database" in restore_document["error"]["message"].lower()
    assert _catalog_state(inputs.catalog_path) is BackupState.AVAILABLE
    assert not project_filestore(inputs.focused_project, target).exists()
    probe = _project_database_probe(
        inputs.focused_project,
        target,
        home=inputs.target_runtime.environment["HOME"],
    )
    assert probe.returncode == 0
    assert probe.stdout.strip() != target
    return cast("dict[str, object]", document)


def _assert_truncated_archive_rejected(inputs: _ArchiveBoundaryInputs) -> dict[str, object]:
    result = _invoke_in_registered_worktree(
        CliRunner(),
        cli,
        inputs.focused_project,
        inputs.catalog_path,
        ["backup", "validate", inputs.backup_id, "--format", "json"],
        inputs.environment,
    )
    assert result.exit_code != 0
    document, _ = _observe_failure(
        result,
        runtime=inputs.target_runtime,
        evidence=inputs.failure_evidence,
        name="archive-truncated",
    )
    assert document is not None
    assert document["error"]["code"] == "backup_corrupt"
    return cast("dict[str, object]", document)


_ARCHIVE_BOUNDARY_CASES = (
    pytest.param(
        _ArchiveBoundaryCase("truncated", "E2E-FC-03", _assert_truncated_archive_rejected),
        id="truncated",
    ),
    pytest.param(
        _ArchiveBoundaryCase("incompatible", "E2E-FC-04", _assert_incompatible_archive_rejected),
        id="incompatible",
    ),
)


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


@pytest.mark.parametrize("case", _ARCHIVE_BOUNDARY_CASES)
def test_archive_and_restore_boundaries_publish_no_unowned_state(
    tmp_path: Path,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    case: _ArchiveBoundaryCase,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
    focused_project: Path,
    focused_catalog: Path,
) -> None:
    path = tmp_path / f"{case.variant}.zip"
    write_archive_variant(path, case.variant)  # type: ignore[arg-type]
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
    inputs = _ArchiveBoundaryInputs(
        source_database=source_backup_plan.database,
        target_runtime=target_runtime,
        failure_evidence=failure_evidence,
        focused_project=focused_project,
        catalog_path=catalog_path,
        backup_id=_BACKUP_ID,
        environment=environment,
    )
    document = case.exercise(inputs)
    assert _catalog_state(catalog_path) is BackupState.AVAILABLE
    assert not (tmp_path / "database").exists()
    assert not (tmp_path / "filestore").exists()
    _record(record_property, case.evidence_id, document)


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


def _assert_post_restore_failure(
    failed: Result,
    project: Path,
    previous_default: str | None,
    failure_evidence: FailureEvidence,
) -> dict[str, object]:
    from odoo_instance_sdk.project import ProjectConfig

    diagnostic = {
        "exit_code": failed.exit_code,
        "stderr": failed.stderr,
        "output": failed.output,
        "exception": failed.exception,
    }
    assert_secret_free(diagnostic, failure_evidence.secret_canary)
    assert failed.exit_code != 0, diagnostic
    assert failed.stdout.strip(), (
        f"reset failure must emit its JSON checkpoint on stdout; result={diagnostic!r}"
    )
    failure = json.loads(failed.stdout)
    assert failure["ok"] is False
    context = failure.get("context")
    assert isinstance(context, dict)
    assert context.get("post_restore_checkpoint") == "reset_pending", {
        "error": failure.get("error"),
        "context": context,
    }
    assert context.get("database_confirmed") is True
    assert context.get("restore_state") == "incomplete"
    assert context.get("retained_database")
    assert failure["context"]["pending_stage"] == "admin_reset"
    assert ProjectConfig.load(project).default_source_database == previous_default
    return cast("dict[str, object]", failure)


def _assert_post_restore_completion(
    completed: Result,
    *,
    project: Path,
    target: str,
    live_oid_before: int,
    home: str,
    filestore: Path,
    filestore_before: tuple[str, ...],
    catalog_path: Path,
    cluster: PostgresCluster,
    oid_before: int,
    restored_events_before: int,
) -> dict[str, object]:
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    assert completed.exit_code == 0, completed.output
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["result"]["target_database"] == target
    assert result["result"]["completed_stages"] == ["admin_reset", "default_switch"]
    assert ProjectConfig.load(project).default_source_database == target
    assert project_database_oid(project, target, home=home) == live_oid_before
    assert (
        tuple(sorted(path.relative_to(filestore).as_posix() for path in filestore.rglob("*")))
        == filestore_before
    )
    catalog = BackupCatalog(db_path=catalog_path)
    try:
        binding = catalog._latest_restore_binding(
            cluster.endpoint_host, cluster.endpoint_port, target
        )
        assert binding is not None
        assert binding["state"] == "complete"
        assert binding["database_oid"] == oid_before
        assert binding["post_restore_checkpoint"] is None
        assert (
            catalog._conn.execute(
                "SELECT COUNT(*) FROM database_events WHERE database_name=? AND event_type='restored'",
                (target,),
            ).fetchone()[0]
            == restored_events_before
        )
    finally:
        catalog.close()
    return cast("dict[str, object]", result)


def _assert_post_restore_no_op(
    repeated: Result, project: Path, target: str, *, home: str
) -> dict[str, object]:
    assert repeated.exit_code == 0, repeated.output
    result = json.loads(repeated.stdout)
    assert result["result"]["no_op"] is True
    assert _project_database_probe(project, target, home=home).stdout.strip() == target
    return cast("dict[str, object]", result)


@dataclass
class _PostRestoreScenario:
    tmp_path: Path
    source_backup: ArchiveIdentity
    source_backup_plan: SourceBackupPlan
    target_runtime: E2ERuntime
    failure_evidence: FailureEvidence
    project: Path
    catalog_path: Path
    backup_id: str
    target: str
    previous_default: str | None
    environment: dict[str, str]
    inventory_diagnostics: list[InventoryDiagnostic]
    credential_bindings: list[CredentialBinding]
    publication_binding_traces: list[dict[str, object]]
    reset_failures: list[bool]


@dataclass(frozen=True)
class _PostRestoreFailureEvidence:
    failure: dict[str, object]
    cluster: PostgresCluster
    live_oid: int
    captured_oid: int
    restored_events: int
    filestore: Path
    filestore_contents: tuple[str, ...]


def _prepare_post_restore_scenario(
    tmp_path: Path,
    source_backup: ArchiveIdentity,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    focused_catalog: Path,
) -> _PostRestoreScenario:
    from odoo_instance_sdk.project import ProjectConfig

    archive = tmp_path / "post-restore.zip"
    shutil.copy2(source_backup.path, archive)
    catalog_path = _isolated_catalog(focused_catalog, tmp_path)
    backup_id = "00000000-0000-0000-0000-000000000009"
    _seed_backup(
        catalog_path,
        archive,
        backup_id=backup_id,
        database=source_backup_plan.database,
        source_base_url=source_backup_plan.endpoint,
    )
    _bind_catalog_path(monkeypatch, catalog_path)
    previous_default = ProjectConfig.load(project).default_source_database
    target = f"odcli_postrestore_{target_runtime.run_id.replace('-', '')[:22]}"
    environment = {
        **_focused_cli_environment(target_runtime.environment, catalog_path),
        "ODCLI_E2E_KEEP_FAILED": "1",
        "ODCLI_ADMIN_PASSWORD": failure_evidence.secret_canary,
    }
    _ensure_isolated_environment(catalog_path, project)
    _approve_project_postgres(project, environment)

    scenario = _PostRestoreScenario(
        tmp_path=tmp_path,
        source_backup=source_backup,
        source_backup_plan=source_backup_plan,
        target_runtime=target_runtime,
        failure_evidence=failure_evidence,
        project=project,
        catalog_path=catalog_path,
        backup_id=backup_id,
        target=target,
        previous_default=previous_default,
        environment=environment,
        inventory_diagnostics=[],
        credential_bindings=[],
        publication_binding_traces=[],
        reset_failures=[],
    )
    from odoo_instance_sdk.internal.pg import inventory as inventory_module

    original_credentials = inventory_module._credentials

    def capture_credential_binding(instance: object) -> tuple[str, int, str | None, str | None]:
        selected = original_credentials(cast("OdooInstance", instance))
        scenario.credential_bindings.append(capture_binding(instance, selected))
        return selected

    monkeypatch.setattr(inventory_module, "_credentials", capture_credential_binding)
    from odoo_instance_sdk.internal import project_init as project_init_module

    original_generated_rewrite = project_init_module._rewrite_project_generated_database

    def capture_publication_binding(root: Path, config: ProjectConfig, destination: Path) -> None:
        before_publication = capture_project_binding(root)
        original_generated_rewrite(root, config, destination)
        after_publication = capture_project_binding(root)
        scenario.publication_binding_traces.append(
            compare_bindings(before_publication, after_publication)
        )

    monkeypatch.setattr(
        project_init_module,
        "_rewrite_project_generated_database",
        capture_publication_binding,
    )
    original_execute = SubprocessExecutor.execute

    def capture_inventory_result(
        self: SubprocessExecutor,
        step: PreparedStep,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResult:
        if not inventory_step_id(step):
            return original_execute(self, step, observer=observer, observe_output=observe_output)
        try:
            result = original_execute(self, step, observer=observer, observe_output=observe_output)
        except BaseException as error:
            scenario.inventory_diagnostics.append(inventory_diagnostic(None, error=error))
            raise
        scenario.inventory_diagnostics.append(inventory_diagnostic(result))
        return result

    monkeypatch.setattr(SubprocessExecutor, "execute", capture_inventory_result)

    return scenario


def _run_post_restore_with_reset_failure(
    scenario: _PostRestoreScenario, monkeypatch: pytest.MonkeyPatch
) -> Result:
    from odoo_instance_sdk.resources.database import DatabaseResource

    def fail_reset_once(self: DatabaseResource, *, admin_password: str, provenance: str) -> object:
        del self, admin_password, provenance
        scenario.reset_failures.append(True)
        raise RuntimeError("injected administrator reset failure")

    with monkeypatch.context() as injected:
        injected.setattr(DatabaseResource, "reset_admin_password", fail_reset_once)
        return cast(
            "Result",
            _invoke_in_registered_worktree(
                CliRunner(),
                cli,
                scenario.project,
                scenario.catalog_path,
                [
                    "--project",
                    str(scenario.project),
                    "db",
                    "restore",
                    scenario.backup_id,
                    "--target",
                    scenario.target,
                    "--reset-admin-password",
                    "--yes",
                    "--format",
                    "json",
                ],
                scenario.environment,
            ),
        )


def _assert_reset_failure_checkpoint(
    scenario: _PostRestoreScenario, failed: Result
) -> _PostRestoreFailureEvidence:
    from odoo_instance_sdk.models import PostRestoreCheckpoint
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    failed_diagnostic = {
        "exit_code": failed.exit_code,
        "stderr": failed.stderr,
        "stdout": failed.stdout,
        "exception": failed.exception,
    }
    assert_secret_free(failed_diagnostic, scenario.failure_evidence.secret_canary)
    assert scenario.reset_failures == [True]
    failure = _assert_post_restore_failure(
        failed, scenario.project, scenario.previous_default, scenario.failure_evidence
    )
    filestore = project_filestore(scenario.project, scenario.target)
    assert filestore.is_dir()
    cluster = PostgresCluster.from_project(scenario.project)
    live_oid_before = project_database_oid(
        scenario.project, scenario.target, home=scenario.environment["HOME"]
    )
    catalog = BackupCatalog(db_path=scenario.catalog_path)
    try:
        binding = catalog._latest_restore_binding(
            cluster.endpoint_host, cluster.endpoint_port, scenario.target
        )
        assert binding is not None
        assert binding["state"] == "incomplete"
        assert binding["post_restore_checkpoint"] == PostRestoreCheckpoint.RESET_PENDING.value
        assert binding["database_oid"] is not None
        assert binding["database_oid"] == live_oid_before
        restored_events_before = catalog._conn.execute(
            "SELECT COUNT(*) FROM database_events WHERE database_name=? AND event_type='restored'",
            (scenario.target,),
        ).fetchone()[0]
        assert restored_events_before == 1
        oid_before = binding["database_oid"]
    finally:
        catalog.close()
    filestore_before = tuple(
        sorted(path.relative_to(filestore).as_posix() for path in filestore.rglob("*"))
    )
    return _PostRestoreFailureEvidence(
        failure=failure,
        cluster=cluster,
        live_oid=live_oid_before,
        captured_oid=oid_before,
        restored_events=restored_events_before,
        filestore=filestore,
        filestore_contents=filestore_before,
    )


def _invoke_post_restore_completion(scenario: _PostRestoreScenario) -> Result:
    return cast(
        "Result",
        _invoke_in_registered_worktree(
            CliRunner(),
            cli,
            scenario.project,
            scenario.catalog_path,
            [
                "--project",
                str(scenario.project),
                "db",
                "restore",
                "--complete",
                scenario.target,
                "--yes",
                "--format",
                "json",
            ],
            scenario.environment,
        ),
    )


def _assert_post_restore_completion_and_no_op(
    scenario: _PostRestoreScenario,
    failure: _PostRestoreFailureEvidence,
) -> tuple[dict[str, object], dict[str, object]]:
    completed = _invoke_post_restore_completion(scenario)
    result = _assert_post_restore_completion(
        completed,
        project=scenario.project,
        target=scenario.target,
        live_oid_before=failure.live_oid,
        home=scenario.environment["HOME"],
        filestore=failure.filestore,
        filestore_before=failure.filestore_contents,
        catalog_path=scenario.catalog_path,
        cluster=failure.cluster,
        oid_before=failure.captured_oid,
        restored_events_before=failure.restored_events,
    )
    repeated = _invoke_post_restore_completion(scenario)
    repeated_result = _assert_post_restore_no_op(
        repeated, scenario.project, scenario.target, home=scenario.environment["HOME"]
    )
    return result, repeated_result


def _assert_post_restore_credential_diagnostics(
    scenario: _PostRestoreScenario, record_property: object
) -> None:
    inventory_evidence = {"inventory_processes": scenario.inventory_diagnostics}
    assert_secret_free(inventory_evidence, scenario.failure_evidence.secret_canary)
    _record(record_property, "POST_RESTORE_INVENTORY_DIAGNOSTICS", inventory_evidence)
    assert len(scenario.credential_bindings) >= 2
    credential_trace = compare_bindings(
        scenario.credential_bindings[0], scenario.credential_bindings[-1]
    )
    assert_secret_free(credential_trace, scenario.failure_evidence.secret_canary)
    for predicate in (
        "cluster_identity_unchanged",
        "endpoint_identity_unchanged",
        "user_identity_unchanged",
        "password_present_before",
        "password_present_after",
        "selected_password_matches_baseline",
        "generated_password_matches_selected_before",
        "generated_password_matches_selected_after",
        "generated_password_matches_owned_file_before",
        "generated_password_matches_owned_file_after",
        "owned_password_file_unchanged",
        "password_file_binding_unchanged",
        "home_binding_unchanged",
        "user_root_binding_unchanged",
        "user_root_matches_home_before",
        "user_root_matches_home_after",
    ):
        assert credential_trace[predicate] is True
    assert (
        credential_trace["credential_source_before"] == credential_trace["credential_source_after"]
    )
    _record(record_property, "POST_RESTORE_CREDENTIAL_BINDING", credential_trace)

    assert scenario.publication_binding_traces
    for publication_trace in scenario.publication_binding_traces:
        for predicate in (
            "cluster_identity_unchanged",
            "endpoint_identity_unchanged",
            "user_identity_unchanged",
            "selected_password_matches_baseline",
            "generated_password_matches_selected_before",
            "generated_password_matches_selected_after",
            "generated_password_matches_owned_file_before",
            "generated_password_matches_owned_file_after",
            "owned_password_file_unchanged",
            "password_file_binding_unchanged",
            "home_binding_unchanged",
            "user_root_binding_unchanged",
            "user_root_matches_home_before",
            "user_root_matches_home_after",
        ):
            assert publication_trace[predicate] is True
        assert (
            publication_trace["credential_source_before"]
            == publication_trace["credential_source_after"]
        )
    publication_evidence = {"default_publications": scenario.publication_binding_traces}
    assert_secret_free(publication_evidence, scenario.failure_evidence.secret_canary)
    _record(record_property, "POST_RESTORE_PUBLICATION_BINDING", publication_evidence)


def test_complete_post_restore_without_reimport(
    tmp_path: Path,
    source_backup: ArchiveIdentity,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    monkeypatch: pytest.MonkeyPatch,
    focused_project: Path,
    focused_catalog: Path,
    record_property: object,
) -> None:
    # Arrange: one owned catalog/project and safe process/credential evidence.
    scenario = _prepare_post_restore_scenario(
        tmp_path,
        source_backup,
        source_backup_plan,
        target_runtime,
        failure_evidence,
        monkeypatch,
        focused_project,
        focused_catalog,
    )
    # Act: use ordinary public restore and inject only the administrator reset fault.
    failed = _run_post_restore_with_reset_failure(scenario, monkeypatch)
    # Assert phase A: retain exact imported target proof, checkpoint and prior default.
    failure = _assert_reset_failure_checkpoint(scenario, failed)
    # Act phase B, then assert checkpoint completion and repeated no-op invariants.
    result, repeated_result = _assert_post_restore_completion_and_no_op(scenario, failure)
    # Assert phase C: private trace remains redacted and bound across publication/inventory.
    _assert_post_restore_credential_diagnostics(scenario, record_property)
    _record(
        record_property,
        "COMP-HAPPY",
        {"failure": failure.failure, "completed": result, "repeat": repeated_result},
    )
