"""Shared evidence and catalog helpers for focused real-Odoo leaves."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import chdir
from pathlib import Path
from typing import Any

from odoo_instance_sdk.models import BackupState
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

from .cleanup import FailureEvidence
from .conftest import E2ERuntime
from .failures import assert_secret_free, write_failure_evidence

BACKUP_ID = "00000000-0000-0000-0000-000000000007"


def copy_catalog_snapshot(source: Path, destination: Path) -> None:
    """Take a transactionally consistent snapshot, including SQLite WAL state."""
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    source_uri = f"file:{source}?mode=ro"
    with (
        sqlite3.connect(source_uri, uri=True) as source_connection,
        sqlite3.connect(temporary) as destination_connection,
    ):
        source_connection.backup(destination_connection)
    temporary.replace(destination)
    destination.chmod(0o600)


def registered_worktree(catalog_path: Path, project: Path) -> Path:
    """Return the active checkout recorded for a focused project."""
    selector = project / ".odcli" / "e2e-environment-id"
    if not selector.is_file():
        raise AssertionError(f"focused project has no environment selector: {selector}")
    environment_id = selector.read_text(encoding="ascii").strip()
    catalog = BackupCatalog(db_path=catalog_path)
    try:
        row = next(
            (
                item
                for item in catalog.list_environments(include_removed=True)
                if str(item["id"]) == environment_id
            ),
            None,
        )
    finally:
        catalog.close()
    if row is None or str(row["state"]) == "removed":
        raise AssertionError(
            f"isolated catalog does not contain an active environment: {environment_id}"
        )
    worktree = Path(str(row["worktree_path"])).resolve()
    if not worktree.is_dir():
        raise AssertionError(f"registered environment worktree is unavailable: {worktree}")
    return worktree


def assert_project_state_preflight(project: Path, catalog_path: Path) -> str:
    """Fail fast when a focused leaf lacks the state its public path requires."""
    config = ProjectConfig.load(project)
    if config.python is None:
        raise AssertionError("state preflight: registered project manifest lacks python")
    python = Path(config.python)
    if not python.is_absolute():
        python = project / python
    if not python.is_file():
        raise AssertionError(f"state preflight: registered python is unavailable: {python}")

    selector = project / ".odcli" / "e2e-environment-id"
    if not selector.is_file():
        raise AssertionError(f"state preflight: project has no environment selector: {selector}")
    environment_id = selector.read_text(encoding="ascii").strip()
    registered_worktree(catalog_path, project)

    from odoo_instance_sdk.resources.postgres import PostgresCluster

    cluster = PostgresCluster.from_project(project)
    catalog = BackupCatalog(db_path=catalog_path)
    try:
        claim = catalog._get_postgres_cluster(cluster._project_id)
    finally:
        catalog.close()
    if claim is None or claim.state != "active":
        raise AssertionError(
            "state preflight: isolated catalog lacks active postgres attachment "
            f"claim: {cluster._project_id}"
        )
    return environment_id


def invoke_in_registered_worktree(
    runner: Any,
    cli: Any,
    project: Path,
    catalog_path: Path,
    args: list[str],
    environment: dict[str, str],
    *,
    input: str | None = None,
) -> Any:
    """Invoke a public command from the exact worktree it resolves."""
    with chdir(registered_worktree(catalog_path, project)):
        return runner.invoke(cli, args, env=environment, input=input)


def record(record_property: object, evidence: str, value: object = "passed") -> None:
    getattr(record_property, "__call__")(
        evidence.lower().replace("-", "_"), json.dumps(value, default=str, sort_keys=True)
    )


def observe_failure(
    result: object,
    *,
    runtime: E2ERuntime,
    evidence: FailureEvidence,
    name: str,
    argv: object = (),
) -> tuple[dict[str, Any] | None, tuple[Path, ...]]:
    """Audit every real CLI failure across output, exception, and artifacts."""
    text = "\n".join(
        str(getattr(result, field, "")) for field in ("stdout", "stderr", "output", "exception")
    )
    assert_secret_free(
        {
            "argv": argv,
            "machine_output": text,
            "pytest_output": getattr(result, "output", ""),
            "fingerprint": getattr(result, "fingerprint", ""),
            "exception_graph": getattr(result, "exception", ""),
        },
        evidence.secret_canary,
    )
    files = write_failure_evidence(evidence, logs={name: text})
    assert_secret_free(files, evidence.secret_canary)
    stdout = str(getattr(result, "stdout", ""))
    if not stdout.strip():
        return None, files
    try:
        document = json.loads(stdout)
    except json.JSONDecodeError:
        return None, files
    assert isinstance(document, dict) and document.get("ok") is False
    return document, files


def seed_backup(
    db_path: Path,
    archive_path: Path,
    *,
    backup_id: str = BACKUP_ID,
    database: str = "demo",
    source_base_url: str = "http://127.0.0.1:8069",
) -> None:
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    catalog = BackupCatalog(db_path=db_path)
    catalog.start_download(backup_id, source_base_url, database, "zip", True, archive_path)
    catalog.success_download(backup_id, archive_path.name, archive_path.stat().st_size, digest)
    catalog.close()


def catalog_state(db_path: Path, backup_id: str = BACKUP_ID) -> BackupState:
    catalog = BackupCatalog(db_path=db_path)
    row = catalog.get_by_id(backup_id)
    catalog.close()
    assert row is not None
    return BackupState(str(row["state"]))
