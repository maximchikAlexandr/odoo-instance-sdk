"""Shared evidence and catalog helpers for focused real-Odoo leaves."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from odoo_instance_sdk.models import BackupState
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

from .cleanup import FailureEvidence
from .conftest import E2ERuntime
from .failures import assert_secret_free, write_failure_evidence

BACKUP_ID = "00000000-0000-0000-0000-000000000007"


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
