"""Focused authentication and archive-boundary scenarios for the full E2E tier."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
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


@pytest.mark.parametrize("variant", ["truncated", "incompatible"])
def test_archive_and_restore_boundaries_publish_no_unowned_state(
    tmp_path: Path,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    variant: str,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
    focused_project: Path,
    focused_catalog: Path,
) -> None:
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
    if variant == "incompatible":
        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["ok"] is True
        assert document["result"]["db_name"] == "not-the-catalogue-database"
        assert document["result"]["db_name"] != source_backup_plan.database
        files = write_failure_evidence(
            failure_evidence, logs={"archive-incompatible": result.stdout}
        )
        assert_secret_free(files, failure_evidence.secret_canary)
        project = focused_project
        target = f"odcli_incompatible_{target_runtime.run_id.replace('-', '')[:20]}"
        restore = _invoke_in_registered_worktree(
            CliRunner(),
            cli,
            project,
            catalog_path,
            [
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
            ],
            environment,
        )
        assert restore.exit_code != 0
        restore_document, _ = _observe_failure(
            restore,
            runtime=target_runtime,
            evidence=failure_evidence,
            name="archive-incompatible-restore",
        )
        assert restore_document is not None
        assert restore_document["error"]["code"] == "db_restore_failed"
        assert "database" in restore_document["error"]["message"].lower()
        assert _catalog_state(catalog_path) is BackupState.AVAILABLE
        assert not project_filestore(project, target).exists()
        probe = _project_database_probe(project, target, home=target_runtime.environment["HOME"])
        assert probe.returncode == 0
        assert probe.stdout.strip() != target
    else:
        assert result.exit_code != 0
        document, _ = _observe_failure(
            result, runtime=target_runtime, evidence=failure_evidence, name="archive-truncated"
        )
        assert document is not None
        assert document["error"]["code"] == "backup_corrupt"
    assert _catalog_state(catalog_path) is BackupState.AVAILABLE
    assert not (tmp_path / "database").exists()
    assert not (tmp_path / "filestore").exists()
    _record(record_property, "E2E-FC-03" if variant == "truncated" else "E2E-FC-04", document)


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
    filestore_before: list[str],
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
    assert sorted(path.relative_to(filestore).as_posix() for path in filestore.rglob("*")) == (
        filestore_before
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
    from odoo_instance_sdk.models import PostRestoreCheckpoint
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.database import DatabaseResource
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

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
    project = focused_project
    previous_default = ProjectConfig.load(project).default_source_database
    target = f"odcli_postrestore_{target_runtime.run_id.replace('-', '')[:22]}"
    environment = {
        **_focused_cli_environment(target_runtime.environment, catalog_path),
        "ODCLI_E2E_KEEP_FAILED": "1",
        "ODCLI_ADMIN_PASSWORD": failure_evidence.secret_canary,
    }
    _ensure_isolated_environment(catalog_path, project)
    _approve_project_postgres(project, environment)

    inventory_diagnostics: list[InventoryDiagnostic] = []
    credential_bindings = []
    from odoo_instance_sdk.internal.pg import inventory as inventory_module

    original_credentials = inventory_module._credentials

    def capture_credential_binding(instance: object) -> tuple[str, int, str | None, str | None]:
        selected = original_credentials(cast("OdooInstance", instance))
        credential_bindings.append(capture_binding(instance, selected))
        return selected

    monkeypatch.setattr(inventory_module, "_credentials", capture_credential_binding)
    from odoo_instance_sdk.internal import project_init as project_init_module

    publication_binding_traces: list[dict[str, object]] = []
    original_generated_rewrite = project_init_module._rewrite_project_generated_database

    def capture_publication_binding(root: Path, config: ProjectConfig, destination: Path) -> None:
        before_publication = capture_project_binding(root)
        original_generated_rewrite(root, config, destination)
        after_publication = capture_project_binding(root)
        publication_binding_traces.append(compare_bindings(before_publication, after_publication))

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
            inventory_diagnostics.append(inventory_diagnostic(None, error=error))
            raise
        inventory_diagnostics.append(inventory_diagnostic(result))
        return result

    monkeypatch.setattr(SubprocessExecutor, "execute", capture_inventory_result)

    reset_failure_injected = False

    def fail_reset_once(self: DatabaseResource, *, admin_password: str, provenance: str) -> object:
        nonlocal reset_failure_injected
        del self, admin_password, provenance
        reset_failure_injected = True
        raise RuntimeError("injected administrator reset failure")

    with monkeypatch.context() as injected:
        injected.setattr(DatabaseResource, "reset_admin_password", fail_reset_once)
        failed = _invoke_in_registered_worktree(
            CliRunner(),
            cli,
            project,
            catalog_path,
            [
                "--project",
                str(project),
                "db",
                "restore",
                backup_id,
                "--target",
                target,
                "--reset-admin-password",
                "--yes",
                "--format",
                "json",
            ],
            environment,
        )
    failed_diagnostic = {
        "exit_code": failed.exit_code,
        "stderr": failed.stderr,
        "stdout": failed.stdout,
        "exception": failed.exception,
    }
    assert_secret_free(failed_diagnostic, failure_evidence.secret_canary)
    assert reset_failure_injected
    failure = _assert_post_restore_failure(failed, project, previous_default, failure_evidence)
    filestore = project_filestore(project, target)
    assert filestore.is_dir()
    cluster = PostgresCluster.from_project(project)
    live_oid_before = project_database_oid(project, target, home=environment["HOME"])
    catalog = BackupCatalog(db_path=catalog_path)
    try:
        binding = catalog._latest_restore_binding(
            cluster.endpoint_host, cluster.endpoint_port, target
        )
        assert binding is not None
        assert binding["state"] == "incomplete"
        assert binding["post_restore_checkpoint"] == PostRestoreCheckpoint.RESET_PENDING.value
        assert binding["database_oid"] is not None
        assert binding["database_oid"] == live_oid_before
        restored_events_before = catalog._conn.execute(
            "SELECT COUNT(*) FROM database_events WHERE database_name=? AND event_type='restored'",
            (target,),
        ).fetchone()[0]
        assert restored_events_before == 1
        oid_before = binding["database_oid"]
    finally:
        catalog.close()
    filestore_before = sorted(
        path.relative_to(filestore).as_posix() for path in filestore.rglob("*")
    )
    completed = _invoke_in_registered_worktree(
        CliRunner(),
        cli,
        project,
        catalog_path,
        [
            "--project",
            str(project),
            "db",
            "restore",
            "--complete",
            target,
            "--yes",
            "--format",
            "json",
        ],
        environment,
    )
    result = _assert_post_restore_completion(
        completed,
        project=project,
        target=target,
        live_oid_before=live_oid_before,
        home=environment["HOME"],
        filestore=filestore,
        filestore_before=filestore_before,
        catalog_path=catalog_path,
        cluster=cluster,
        oid_before=oid_before,
        restored_events_before=restored_events_before,
    )

    repeated = _invoke_in_registered_worktree(
        CliRunner(),
        cli,
        project,
        catalog_path,
        [
            "--project",
            str(project),
            "db",
            "restore",
            "--complete",
            target,
            "--yes",
            "--format",
            "json",
        ],
        environment,
    )
    inventory_evidence = {"inventory_processes": inventory_diagnostics}
    assert_secret_free(inventory_evidence, failure_evidence.secret_canary)
    _record(record_property, "POST_RESTORE_INVENTORY_DIAGNOSTICS", inventory_evidence)
    assert len(credential_bindings) >= 2
    credential_trace = compare_bindings(credential_bindings[0], credential_bindings[-1])
    assert_secret_free(credential_trace, failure_evidence.secret_canary)
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
    assert publication_binding_traces
    for publication_trace in publication_binding_traces:
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
    publication_evidence = {"default_publications": publication_binding_traces}
    assert_secret_free(publication_evidence, failure_evidence.secret_canary)
    _record(record_property, "POST_RESTORE_PUBLICATION_BINDING", publication_evidence)
    repeated_result = _assert_post_restore_no_op(
        repeated, project, target, home=environment["HOME"]
    )
    _record(
        record_property,
        "COMP-HAPPY",
        {"failure": failure, "completed": result, "repeat": repeated_result},
    )
