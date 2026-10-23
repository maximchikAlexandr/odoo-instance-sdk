"""Public project forget, re-init, and purge coverage for the full tier."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.exceptions import ProjectRemovalError
from odoo_instance_sdk.models.project_removal import ProjectSelector
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.integration.real_odoo.archive import SourceBackupPlan
from tests.integration.real_odoo.cleanup import FailureEvidence, audit_no_leaks
from tests.integration.real_odoo.conftest import E2ERuntime
from tests.integration.real_odoo.focused_support import (
    _catalog_for_project,
    _project,
    record,
    registered_worktree,
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


def test_public_project_forget_reinit_and_purge(
    tmp_path: Path,
    target_runtime: E2ERuntime,
    source_backup_plan: SourceBackupPlan,
    failure_evidence: FailureEvidence,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise one disposable project through the public CLI boundary."""
    project = _project(
        target_runtime,
        target_runtime.root / f"project-removal-{target_runtime.run_id}",
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

    unrelated_project = target_runtime.root / f"unrelated-project-{target_runtime.run_id}"
    unrelated_project.mkdir()
    (unrelated_project / "sentinel.txt").write_text("unrelated\n", encoding="utf-8")
    shared_resource = target_runtime.root / f"shared-resource-{target_runtime.run_id}.dat"
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
    catalog.close()
    project_cluster = PostgresCluster.from_project(project)
    project_postgres_port = project_cluster.endpoint_port
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
    forgotten_projects = catalog.list_projects(include_forgotten=True)
    registered_projects = catalog.list_projects()
    retained_environments = catalog.list_environments(project_id=project_id, include_removed=True)
    assert len(forgotten_projects) == 1
    assert str(forgotten_projects[0]["registration_state"]) == "forgotten"
    assert registered_projects == []
    assert len(retained_environments) == 1
    retained_bytes = sum(path.stat().st_size for path in project.rglob("*") if path.is_file())
    assert retained_bytes > 0
    assert (project / ".odcli" / "project.toml").is_file()
    catalog.close()

    monitor = invoke("ps", "--all-projects")
    assert monitor.returncode == 0, monitor.stdout + monitor.stderr
    monitor_document = _document(monitor)
    assert project_id not in json.dumps(monitor_document, sort_keys=True)

    reinit = invoke(
        "init",
        "--project",
        str(project),
        "--no-input",
        "--allow-partial",
        "--odoo-bin",
        str(project / "odoo-bin"),
        "--python",
        sys.executable,
    )
    assert reinit.returncode == 0, reinit.stdout + reinit.stderr
    catalog = BackupCatalog(db_path=catalog_path)
    reactivated = catalog.get_project_for_lifecycle(project_id)
    assert reactivated is not None
    assert reactivated["registration_state"] == "registered"
    assert len(catalog.list_projects(include_forgotten=True)) == 1
    catalog.close()

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
    with pytest.raises(ProjectRemovalError, match="stale"):
        drift_command.run()
    assert project.exists()

    wrong_confirmation = invoke(
        "remove",
        "--project-id",
        project_id,
        "--purge",
        "--confirm-project",
        "wrong-token",
    )
    assert wrong_confirmation.returncode == 1, wrong_confirmation.stdout + wrong_confirmation.stderr
    assert _document(wrong_confirmation)["error"]["code"] == "confirmation_required"
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
    assert not project.exists()
    assert (unrelated_project / "sentinel.txt").read_text(encoding="utf-8") == "unrelated\n"
    assert shared_resource.read_bytes() == b"shared resource\n"

    cleanup_audit = audit_no_leaks(
        target_runtime.run_id,
        ports=(project_postgres_port,),
    )
    assert cleanup_audit.clean, cleanup_audit.leaks

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
    assert _document(repeated)["result"]["registration_state"] == "forgotten"
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
            "drift_refusal": "stale",
            "unrelated_project_preserved": True,
            "shared_resource_preserved": True,
            "purge": purge_document["result"]["registration_state"],
            "repeat": "idempotent",
            "cleanup_audit": cleanup_audit.leaks,
        },
    )
