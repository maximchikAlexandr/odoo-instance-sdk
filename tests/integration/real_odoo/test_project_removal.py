"""Public project forget, re-init, and purge coverage for the full tier."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.exceptions import ProjectRemovalError
from odoo_instance_sdk.internal.postgres_compose import (
    container_id_matches_snapshot,
    inspect_container_identity,
    inspect_volume_identity,
)
from odoo_instance_sdk.models.project_removal import ProjectSelector
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from scripts.real_odoo_pins import E2E_PINS
from tests.integration.real_odoo.archive import ArchiveIdentity, SourceBackupPlan
from tests.integration.real_odoo.cleanup import FailureEvidence, audit_no_leaks
from tests.integration.real_odoo.conftest import E2ERuntime, odoo_runtime_python
from tests.integration.real_odoo.focused_support import (
    _catalog_for_project,
    _project,
    _project_database_probe,
    record,
    registered_worktree,
    seed_backup,
)
from tests.integration.real_odoo.test_critical_path import _remove_owned_fixture_tree

pytestmark = [
    pytest.mark.real_odoo,
    pytest.mark.e2e_full,
    pytest.mark.serial,
    pytest.mark.timeout(300),
]


def _document(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(result.stdout + result.stderr) from error
    assert isinstance(document, dict), document
    return document


@pytest.mark.parametrize("retain_source", [False, True], ids=["removed-source", "retained-source"])
def test_public_project_forget_reinit_and_purge(
    tmp_path: Path,
    target_runtime: E2ERuntime,
    source_backup: ArchiveIdentity,
    source_backup_plan: SourceBackupPlan,
    failure_evidence: FailureEvidence,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
    retain_source: bool,
) -> None:
    """Exercise one disposable project through the public CLI boundary."""
    project = _project(
        target_runtime,
        target_runtime.root
        / (f"project-removal-{target_runtime.run_id}-{'retained' if retain_source else 'removed'}"),
        source=source_backup_plan,
    )
    catalog_path = _catalog_for_project(target_runtime, project)
    catalog_environment = {
        **os.environ,
        **target_runtime.environment,
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_TEST_MASTER_PASSWORD": failure_evidence.secret_canary,
    }
    command = shutil.which("odcli")
    if command is None:
        pytest.fail("odcli executable is required for public project removal")

    source_variant = "retained" if retain_source else "removed"
    unrelated_project = (
        target_runtime.root / f"unrelated-project-{target_runtime.run_id}-{source_variant}"
    )
    unrelated_project.mkdir()
    (unrelated_project / "sentinel.txt").write_text("unrelated\n", encoding="utf-8")
    shared_resource = (
        target_runtime.root / f"shared-resource-{target_runtime.run_id}-{source_variant}.dat"
    )
    shared_resource.write_bytes(b"shared resource\n")

    def invoke(*args: str, cwd: Path = project) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [command, *args, "--format", "json"],
            cwd=cwd,
            env=catalog_environment,
            capture_output=True,
            text=True,
            check=False,
        )

    catalog = BackupCatalog(db_path=catalog_path)
    project_id = next(
        str(row["project_id"])
        for row in catalog.list_projects(include_forgotten=True)
        if Path(str(row["repository_root"])).resolve() == project.resolve()
    )
    registration = catalog.get_project_for_lifecycle(project_id)
    assert registration is not None
    claim = catalog._get_postgres_cluster_for_project(
        project_id,
        str(registration["repository_root"]),
        str(registration["git_common_dir"]),
    )
    assert claim is not None and claim.state == "active"
    catalog.close()
    project_cluster = PostgresCluster.from_project(project)
    project_postgres_port = project_cluster.endpoint_port
    backup_id = str(uuid.uuid4())
    backup_path = target_runtime.root / f"project-removal-{target_runtime.run_id}.zip"
    shutil.copy2(source_backup.path, backup_path)
    seed_backup(
        catalog_path,
        backup_path,
        backup_id=backup_id,
        database=source_backup_plan.database,
        source_base_url=source_backup_plan.endpoint,
    )
    project_database = f"odcli_e2e_owned_{target_runtime.run_id.replace('-', '')[:20]}"
    restored = invoke("db", "restore", backup_id, "--target", project_database, "--yes")
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert _document(restored)["result"]["restored_database"] == project_database
    configured = invoke(
        "init",
        "--project",
        str(project),
        "--no-input",
        "--yes",
        "--odoo-bin",
        str(project / "odoo-bin"),
        "--python",
        str(odoo_runtime_python(target_runtime, project, project / "odoo-bin")),
        "--config",
        str(target_runtime.config_file),
        "--test-url",
        source_backup_plan.endpoint,
        "--test-database",
        source_backup_plan.database,
        "--test-branch",
        E2E_PINS.odoo_source_commit,
        "--local-config",
        "--database",
        project_database,
        "--postgres",
        "compose",
        "--postgres-image",
        E2E_PINS.postgres_image,
        "--postgres-port",
        str(project_postgres_port),
        "--postgres-user",
        "odoo",
    )
    assert configured.returncode == 0, configured.stdout + configured.stderr
    assert ProjectConfig.load(project).default_source_database == project_database
    environment_id = (project / ".odcli" / "e2e-environment-id").read_text(encoding="ascii").strip()
    worktree = registered_worktree(catalog_path, project)
    fixture_addon = worktree / "addons" / "odcli_e2e_probe"
    _remove_owned_fixture_tree(worktree, fixture_addon)

    active_refusal = invoke("remove", "--project-id", project_id)
    assert active_refusal.returncode == 1, active_refusal.stdout + active_refusal.stderr
    active_document = _document(active_refusal)
    assert active_document["ok"] is False
    assert "active" in active_document["error"]["message"].lower()

    removed_environment = invoke(
        "--project", str(project), "env", "remove", environment_id, "--yes"
    )
    assert removed_environment.returncode == 0, (
        removed_environment.stdout + removed_environment.stderr
    )
    stopped_cluster = invoke("--project", str(project), "postgres", "stop")
    assert stopped_cluster.returncode == 0, stopped_cluster.stdout + stopped_cluster.stderr

    forgotten = invoke("remove", "--project-id", project_id)
    assert forgotten.returncode == 0, forgotten.stdout + forgotten.stderr
    forgotten_document = _document(forgotten)
    assert forgotten_document["result"]["registration_state"] == "forgotten"
    assert project.exists()

    catalog = BackupCatalog(db_path=catalog_path)
    forgotten_projects = [
        row
        for row in catalog.list_projects(include_forgotten=True)
        if str(row["project_id"]) == project_id
    ]
    registered_projects = [
        row for row in catalog.list_projects() if str(row["project_id"]) == project_id
    ]
    retained_environments = catalog._conn.execute(
        "SELECT state FROM environments WHERE id = ?",
        (environment_id,),
    ).fetchall()
    assert len(forgotten_projects) == 1
    assert str(forgotten_projects[0]["registration_state"]) == "forgotten"
    assert not catalog._project_purge_completed(project_id)
    assert registered_projects == []
    assert len(retained_environments) == 1
    assert str(retained_environments[0]["state"]) == "removed"
    retained_bytes = sum(path.stat().st_size for path in project.rglob("*") if path.is_file())
    assert retained_bytes > 0
    assert (project / ".odcli" / "project.toml").is_file()
    catalog.close()

    monitor = invoke("ps", "--all-projects")
    assert monitor.returncode == 0, monitor.stdout + monitor.stderr
    monitor_document = _document(monitor)
    assert project_id not in json.dumps(monitor_document, sort_keys=True)

    reinit_config = ProjectConfig.load(project)
    reinit_args = [
        "init",
        "--project",
        str(project),
        "--no-input",
        "--yes",
        "--odoo-bin",
        str(project / "odoo-bin"),
        "--python",
        str(odoo_runtime_python(target_runtime, project, project / "odoo-bin")),
        "--config",
        str(target_runtime.config_file),
        "--test-url",
        source_backup_plan.endpoint,
        "--test-database",
        source_backup_plan.database,
        "--test-branch",
        E2E_PINS.odoo_source_commit,
        "--local-config",
        "--database",
        project_database,
        "--postgres",
        "compose",
        "--postgres-image",
        E2E_PINS.postgres_image,
        "--postgres-port",
        str(project_postgres_port),
        "--postgres-user",
        "odoo",
    ]
    if reinit_config.preferred_http_port is not None:
        reinit_args.extend(("--http-port", str(reinit_config.preferred_http_port)))
    reinit = invoke(*reinit_args)
    assert reinit.returncode == 0, reinit.stdout + reinit.stderr
    catalog = BackupCatalog(db_path=catalog_path)
    reactivated = catalog.get_project_for_lifecycle(project_id)
    assert reactivated is not None
    assert reactivated["registration_state"] == "registered"
    assert not catalog._project_purge_completed(project_id)
    assert [
        row
        for row in catalog.list_projects(include_forgotten=True)
        if str(row["project_id"]) == project_id
    ]
    catalog.close()
    resumed_cluster_result = invoke("--project", str(project), "postgres", "up")
    resumed_cluster_output = resumed_cluster_result.stdout + resumed_cluster_result.stderr
    assert resumed_cluster_result.returncode == 0, resumed_cluster_output
    database_probe = _project_database_probe(
        project,
        project_database,
        home=target_runtime.environment["HOME"],
    )
    database_probe_output = database_probe.stdout + database_probe.stderr
    assert database_probe.returncode == 0, database_probe_output
    assert database_probe.stdout.strip() == project_database
    project_instance = OdooClient(config=OdooClientConfig(executable="odoo")).instance.from_project(
        ProjectConfig.load(project)
    )
    database_inventory = project_instance.databases.list_inventory(project, tracked=False)
    owned_database = next(item for item in database_inventory.databases if item.name == project_database)
    assert type(owned_database.database_oid) is int and owned_database.database_oid > 0
    resumed_claim = BackupCatalog(db_path=catalog_path)
    current_claim = resumed_claim._get_postgres_cluster_for_project(
        project_id,
        str(project),
        str(registration["git_common_dir"]),
    )
    resumed_postgres_cluster = PostgresCluster.from_project(project)
    bound_records = resumed_claim._project_bound_database_records(
        claim.project_id,
        claim.cluster_id,
        resumed_postgres_cluster.endpoint_host,
        resumed_postgres_cluster.endpoint_port,
    )
    resumed_claim.close()
    assert current_claim is not None and current_claim.cluster_id == claim.cluster_id
    assert (owned_database.name, owned_database.database_oid) in bound_records
    assert inspect_volume_identity(
        resumed_postgres_cluster.compose_runner,
        claim.volume_name,
        str(claim.cluster_id),
        project_id=claim.project_id,
    )
    container_id = inspect_container_identity(
        resumed_postgres_cluster.compose_runner,
        f"{claim.compose_project}-postgres-1",
        str(claim.cluster_id),
        project_id=claim.project_id,
        compose_project=claim.compose_project,
        volume_name=claim.volume_name,
    )
    with monkeypatch.context() as project_environment:
        for key in (
            "HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "ODCLI_E2E_CATALOG",
        ):
            project_environment.setenv(key, target_runtime.environment[key])
        assert resumed_postgres_cluster.compose_file.is_file()
        snapshot = resumed_postgres_cluster.resource_snapshot()
    assert container_id is not None
    assert snapshot is not None and snapshot.container is not None
    assert container_id_matches_snapshot(container_id, snapshot.container.id)

    token = f"{project.name}:{project_id}"
    drift_target = project / ".odcli" / "logs"
    drift_target.mkdir()
    (drift_target / "drift.log").write_text("drift\n", encoding="utf-8")
    for key in (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "ODCLI_E2E_CATALOG",
    ):
        monkeypatch.setenv(key, catalog_environment[key])
    sdk_client = OdooClient(config=OdooClientConfig(executable="odoo"))
    drift_command = sdk_client.projects.remove_command(
        ProjectSelector.from_project_id(project_id),
        purge=True,
        confirmation=token,
    )
    shutil.rmtree(drift_target)
    with pytest.raises(ProjectRemovalError) as drift_error:
        drift_command.run()
    # A changed project tree can invalidate the captured plan either at the
    # required-observation boundary or during the later fingerprint check.
    # Both are fail-closed outcomes; retain the structured step and durable
    # no-write assertions below so this does not accept an arbitrary refusal.
    assert drift_error.value.details["removal"]["failed_step"] in {
        "project-removal:observations",
        "project-removal:revalidate",
    }
    assert project.exists()
    catalog = BackupCatalog(db_path=catalog_path)
    assert not catalog._project_purge_completed(project_id)
    retained_claim = catalog._get_postgres_cluster(claim.project_id)
    assert retained_claim is not None and retained_claim.state == "active"
    catalog.close()

    wrong_confirmation = invoke(
        "remove",
        "--project-id",
        project_id,
        "--purge",
        "--confirm-project",
        "wrong-token",
    )
    assert wrong_confirmation.returncode == 1, wrong_confirmation.stdout + wrong_confirmation.stderr
    assert _document(wrong_confirmation)["error"]["code"] == "confirmation_required", (
        wrong_confirmation.stdout + wrong_confirmation.stderr
    )
    catalog = BackupCatalog(db_path=catalog_path)
    assert not catalog._project_purge_completed(project_id)
    catalog.close()
    source_status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert source_status.returncode == 0, source_status.stderr
    assert not source_status.stdout.strip(), (
        "project fixture should be source-clean before caller-owned retention: "
        + source_status.stdout
    )
    # This selector is created only by the real-Odoo harness so its cleanup
    # helpers can locate the environment. The SDK does not own this file.
    (project / ".odcli" / "e2e-environment-id").unlink(missing_ok=True)
    if retain_source:
        # Match a caller-owned checkout whose remaining source fails the
        # registered Git provenance check after purge removes its manifest.
        (project / ".odcli-e2e-retained-source.txt").write_text(
            "caller-owned source\n", encoding="utf-8"
        )
    purge = invoke(
        "remove",
        "--project-id",
        project_id,
        "--purge",
        "--confirm-project",
        token,
    )
    assert purge.returncode == 0, purge.stdout + purge.stderr
    purge_document = _document(purge)
    assert purge_document["result"]["registration_state"] == "forgotten"
    assert project.exists() is retain_source
    assert not (project / ".odcli" / "project.toml").exists()
    if retain_source:
        assert (project / ".odcli-e2e-retained-source.txt").read_text(encoding="utf-8") == (
            "caller-owned source\n"
        )
    assert (unrelated_project / "sentinel.txt").read_text(encoding="utf-8") == "unrelated\n"
    assert shared_resource.read_bytes() == b"shared resource\n"

    # Read the durable catalogue and Docker independently before issuing a
    # fresh full-id command. The purge must retire the exact legacy-aware
    # claim only after both owned Compose resources have been verified absent.
    catalog = BackupCatalog(db_path=catalog_path)
    persisted = catalog.get_project_for_lifecycle(project_id)
    assert persisted is not None
    assert persisted["registration_state"] == "forgotten"
    assert catalog._project_purge_completed(project_id)
    assert catalog._get_postgres_cluster(claim.project_id) is None
    assert catalog._get_postgres_cluster_by_id(claim.cluster_id) is None
    assert (
        catalog._conn.execute(
            "SELECT 1 FROM project_database_bindings WHERE project_id = ? LIMIT 1", (project_id,)
        ).fetchone()
        is None
    )
    catalog.close()
    for docker_args, label in (
        (("docker", "inspect", claim.compose_project + "-postgres-1"), "container"),
        (("docker", "volume", "inspect", claim.volume_name), "volume"),
    ):
        absent = subprocess.run(docker_args, capture_output=True, text=True, check=False)
        assert absent.returncode != 0
        assert "no such" in (absent.stdout + absent.stderr).lower(), (
            f"{label} absence was not proven: {absent.stdout}{absent.stderr}"
        )

    cleanup_audit = audit_no_leaks(
        target_runtime.run_id,
        ports=(project_postgres_port,),
    )
    assert cleanup_audit.clean, cleanup_audit.leaks

    observer = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    observer.execute("PRAGMA query_only=ON")
    changes_before_repeat = int(observer.execute("PRAGMA data_version").fetchone()[0])
    repeated = invoke(
        "remove",
        "--project-id",
        project_id,
        "--purge",
        "--confirm-project",
        token,
        cwd=tmp_path,
    )
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    repeated_result = _document(repeated)["result"]
    assert repeated_result["registration_state"] == "forgotten"
    assert repeated_result["completed"] == []
    assert repeated_result["remaining"] == []
    assert int(observer.execute("PRAGMA data_version").fetchone()[0]) == changes_before_repeat
    observer.close()
    catalog = BackupCatalog(db_path=catalog_path)
    persisted_after_repeat = catalog.get_project_for_lifecycle(project_id)
    assert persisted_after_repeat is not None
    assert persisted_after_repeat["registration_state"] == "forgotten"
    assert catalog._project_purge_completed(project_id)
    assert catalog._get_postgres_cluster(claim.project_id) is None
    catalog.close()
    record(
        record_property,
        "E2E-CP-16",
        {
            "project_id": project_id,
            "active_refusal": active_document["error"]["code"],
            "forget": forgotten_document["result"]["registration_state"],
            "reinit_state": reactivated["registration_state"],
            "retained_bytes_after_forget": retained_bytes,
            "retained_catalog_rows_after_forget": {
                "forgotten_projects": len(forgotten_projects),
                "registered_projects": len(registered_projects),
                "environments": len(retained_environments),
            },
            "monitor_hidden_after_forget": project_id
            not in json.dumps(monitor_document, sort_keys=True),
            "unavailable_observation_refusal": "project claim retained",
            "unrelated_project_preserved": True,
            "shared_resource_preserved": True,
            "purge": purge_document["result"]["registration_state"],
            "repeat": "idempotent",
            "cleanup_audit": cleanup_audit.leaks,
        },
    )
