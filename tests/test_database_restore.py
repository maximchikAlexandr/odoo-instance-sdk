"""Self-check tests for database.restore()."""

import json
import os
import tempfile
from pathlib import Path

from _helpers import SilentHandler, make_client, start_stub_server

from odoo_instance_sdk.exceptions import RemoteInstanceError
from odoo_instance_sdk.models import BackupArtifact


class RestoreHandler(SilentHandler):
    """Stub for Odoo restore endpoint."""

    def do_POST(self) -> None:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            self.send_response(401)
            self.end_headers()
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_response(400)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            self.rfile.read(content_length)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"result": True}).encode())


def test_restore_local_success() -> None:
    """Test successful restore on a local instance."""
    server, port = start_stub_server(RestoreHandler)
    client = make_client(base_url=f"http://127.0.0.1:{port}")

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(b"fake-backup-content")
        backup_path = f.name

    artifact = BackupArtifact(
        path=Path(backup_path).resolve(),
        source_db="source_db",
        format="zip",
        has_filestore=True,
        source_base_url=client.config.base_url,
    )

    result = client.database.restore(artifact, "new_db", timeout=5.0)
    assert result.new_db == "new_db"
    assert result.source is artifact

    os.unlink(backup_path)
    server.shutdown()
    print("test_restore_local_success PASSED")


def test_restore_remote_refused() -> None:
    """Test that restore on remote URL raises RemoteInstanceError."""
    client = make_client(base_url="http://example.com:8069")

    artifact = BackupArtifact(
        path=Path("/tmp/fake.zip"),
        source_db="source",
        format="zip",
        has_filestore=True,
        source_base_url=client.config.base_url,
    )

    try:
        client.database.restore(artifact, "new_db")
        assert False, "Should have raised RemoteInstanceError"
    except RemoteInstanceError:
        pass
    print("test_restore_remote_refused PASSED")


def test_restore_missing_file() -> None:
    """Test that restore with nonexistent backup file raises FileNotFoundError."""
    client = make_client()

    artifact = BackupArtifact(
        path=Path("/tmp/nonexistent_backup.zip"),
        source_db="source",
        format="zip",
        has_filestore=True,
        source_base_url=client.config.base_url,
    )

    try:
        client.database.restore(artifact, "new_db")
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass
    print("test_restore_missing_file PASSED")


if __name__ == "__main__":
    test_restore_local_success()
    test_restore_remote_refused()
    test_restore_missing_file()
