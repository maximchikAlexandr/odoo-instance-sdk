from __future__ import annotations

import threading
import uuid
from pathlib import Path

from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _create_backup_file(tmp_path: Path, name: str) -> Path:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    return f


def test_two_writers_sequential(tmp_path):
    db_path = tmp_path / "shared.db"
    p1 = _create_backup_file(tmp_path, "b1.zip")
    p2 = _create_backup_file(tmp_path, "b2.zip")
    catalog1 = BackupCatalog(db_path=db_path)
    catalog2 = BackupCatalog(db_path=db_path)
    bid1 = str(uuid.uuid4())
    bid2 = str(uuid.uuid4())
    catalog1.start_download(bid1, "http://localhost:8069", "db1", "zip", True, p1)
    catalog1.success_download(bid1, "b1.zip", 100, "")
    catalog2.start_download(bid2, "http://localhost:8069", "db2", "zip", True, p2)
    catalog2.success_download(bid2, "b2.zip", 200, "")
    assert len(catalog1.list_backups()) == 2
    assert len(catalog2.list_backups()) == 2
    catalog1.close()
    catalog2.close()


def test_concurrent_writes(tmp_path):
    db_path = tmp_path / "concurrent.db"
    # Warm up — open and close to set up the DB file before threading.
    warm = BackupCatalog(db_path=db_path)
    warm.close()

    errors: list[Exception] = []

    def writer(prefix: str) -> None:
        try:
            catalog = BackupCatalog(db_path=db_path)
            for i in range(5):
                p = _create_backup_file(tmp_path, f"{prefix}-{i}.zip")
                bid = str(uuid.uuid4())
                catalog.start_download(bid, "http://localhost:8069", "db", "zip", True, p)
                catalog.success_download(bid, f"{prefix}-{i}.zip", i * 100, "")
            catalog.close()
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=writer, args=("A",))
    t2 = threading.Thread(target=writer, args=("B",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert not errors, f"Concurrent writes failed: {errors}"
    catalog = BackupCatalog(db_path=db_path)
    listed = catalog.list_backups()
    assert len(listed) == 10
    catalog.close()
