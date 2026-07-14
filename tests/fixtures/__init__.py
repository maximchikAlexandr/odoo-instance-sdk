from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from odoo_instance_sdk.models import Backup, BackupFormat


def make_backup(**overrides: Any) -> Backup:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source_base_url": "http://localhost:8069",
        "database_name": "testdb",
        "format": BackupFormat.ZIP,
        "filestore_requested": True,
        "path": "/tmp/fake.zip",
        "filename": "fake.zip",
        "size_bytes": 100,
        "sha256": hashlib.sha256(b"").hexdigest(),
        "downloaded_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return Backup(**defaults)
