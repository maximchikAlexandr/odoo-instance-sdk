from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from tests.integration.real_odoo.archive import ArchiveValidationError, archive_identity
from tests.integration.real_odoo.cleanup import FailureEvidence, ResourceLedger, audit_no_leaks
from tests.integration.real_odoo.compose import ComposeTopology, reserve_ports


def test_compose_topology_is_namespaced_and_loopback_only() -> None:
    reservations = reserve_ports()
    try:
        topology = ComposeTopology.create(
            "a" * 32, (reservations[0].port, reservations[1].port, reservations[2].port)
        )
        rendered = topology.render(
            secret_file=Path("/run/secret"),
            addon_root=Path("/addons"),
            source_root=Path("/source"),
        )
        assert topology.project_name in topology.names
        assert '"127.0.0.1:' in rendered
        assert topology.postgres_image in rendered
        assert "a" * 32 in rendered
    finally:
        for reservation in reservations:
            reservation.release()


def test_archive_identity_requires_dump_and_filestore(tmp_path: Path) -> None:
    path = tmp_path / "backup.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("dump.sql", "-- fixture")
        archive.writestr("filestore/db/blob", b"fixture")
    identity = archive_identity(path)
    assert identity.size_bytes == path.stat().st_size
    assert identity.sha256
    assert identity.filestore_members == ("filestore/db/blob",)

    invalid = tmp_path / "invalid.zip"
    with zipfile.ZipFile(invalid, "w") as archive:
        archive.writestr("dump.sql", "-- fixture")
    with pytest.raises(ArchiveValidationError, match="filestore"):
        archive_identity(invalid)


def test_ledger_unwinds_in_reverse_and_evidence_is_bounded(tmp_path: Path) -> None:
    events: list[str] = []
    ledger = ResourceLedger("run123")
    ledger.record("one", "run123-one", lambda: events.append("one"))
    ledger.record("two", "run123-two", lambda: events.append("two"))
    ledger.unwind()
    assert events == ["two", "one"]

    evidence = FailureEvidence("run123", "canary", tmp_path)
    evidence.add_log("odoo", "credentials omitted\n")
    files = evidence.write()
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)
    assert audit_no_leaks("run123").clean
