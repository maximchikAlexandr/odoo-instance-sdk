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

from tests.integration.real_odoo.archive import SourceBackupPlan
from tests.integration.real_odoo.cleanup import FailureEvidence
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

    def invoke(*args: str, cwd: Path = project) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [command, *args, "--format", "json"],
            cwd=cwd,
            env=catalog_environment,
            capture_output=True,
            text=True,
            check=False,
        )

    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = BackupCatalog(db_path=catalog_path)
    project_id = next(
        str(row["project_id"])
        for row in catalog.list_projects(include_forgotten=True)
        if Path(str(row["repository_root"])).resolve() == project.resolve()
    )
    catalog.close()
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
            "purge": purge_document["result"]["registration_state"],
            "repeat": "idempotent",
        },
    )
