"""Minimal self-check for database.backup()."""

import tempfile
from urllib.parse import parse_qs

from odoo_instance_sdk import OdooClient, OdooClientConfig
from tests._helpers import SilentHandler, make_client, start_stub_server


class BackupHandler(SilentHandler):
    """Stub that serves a fake backup zip."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        params = parse_qs(body)

        if "master_pwd" not in params or "name" not in params or "backup_format" not in params:
            self.send_response(400)
            self.end_headers()
            return

        db_name = params["name"][0]
        backup_format = params["backup_format"][0]

        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            self.send_response(401)
            self.end_headers()
            return

        content = f"fake-{backup_format}-content-for-{db_name}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def test_backup_default_format() -> None:
    """Test backup with default format (zip)."""
    server, port = start_stub_server(BackupHandler)
    client = make_client(base_url=f"http://127.0.0.1:{port}")

    with tempfile.TemporaryDirectory() as tmpdir:
        artifact = client.database.backup("testdb", dest=tmpdir)

        assert artifact.source_db == "testdb"
        assert artifact.format == "zip"
        assert artifact.has_filestore is True
        assert artifact.source_base_url == client.config.base_url
        assert artifact.path.name == "testdb.zip"
        assert artifact.path.exists()
        assert artifact.path.read_bytes() == b"fake-zip-content-for-testdb"

    server.shutdown()
    print("test_backup_default_format PASSED")


def test_backup_dump_no_filestore() -> None:
    """Test backup with dump format and no filestore."""
    server, port = start_stub_server(BackupHandler)
    client = make_client(base_url=f"http://127.0.0.1:{port}")

    with tempfile.TemporaryDirectory() as tmpdir:
        artifact = client.database.backup(
            "testdb2",
            format="dump",
            include_filestore=False,
            dest=tmpdir,
        )

        assert artifact.format == "dump"
        assert artifact.has_filestore is False
        assert artifact.path.name == "testdb2.dump"
        assert artifact.path.read_bytes() == b"fake-dump-content-for-testdb2"

    server.shutdown()
    print("test_backup_dump_no_filestore PASSED")


def test_backup_rejects_path_traversal() -> None:
    """backup() must reject db names containing path separators."""
    config = OdooClientConfig(
        executable="/bin/true", base_url="http://127.0.0.1:0", master_pwd="test"
    )
    client = OdooClient(config)
    try:
        client.database.backup("../../etc/passwd", dest="/tmp")
        assert False, "Should have raised"
    except ValueError:
        pass
    print("test_backup_rejects_path_traversal PASSED")


def test_backup_rejects_bad_format() -> None:
    """backup() must reject unknown format values."""
    config = OdooClientConfig(
        executable="/bin/true", base_url="http://127.0.0.1:0", master_pwd="test"
    )
    client = OdooClient(config)
    try:
        client.database.backup("testdb", format="tar")  # type: ignore[arg-type]
        assert False, "Should have raised"
    except ValueError:
        pass
    print("test_backup_rejects_bad_format PASSED")


if __name__ == "__main__":
    test_backup_default_format()
    test_backup_dump_no_filestore()
    test_backup_rejects_path_traversal()
    test_backup_rejects_bad_format()
