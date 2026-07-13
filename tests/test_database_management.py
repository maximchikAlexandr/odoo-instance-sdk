"""Self-check tests for database.list/exists/drop."""

import json
from urllib.parse import parse_qs

from _helpers import SilentHandler, make_client, start_stub_server

from odoo_instance_sdk.exceptions import RemoteInstanceError


class DBHandler(SilentHandler):
    """Stub for Odoo database management endpoints.

    The `databases` class attribute is bound per-test by start_stub_server, so
    each test gets an isolated list. The list mutates across requests within a
    test (drop removes entries), and a fresh test gets a fresh list.
    """

    def do_POST(self) -> None:
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            self.send_response(401)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if "/web/database/list" in self.path:
            response_data = {"result": list(self.databases)}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response_data).encode())

        elif "/web/database/drop" in self.path:
            post_data = parse_qs(body.decode("utf-8"))
            db_name = post_data.get("name", [""])[0]
            if db_name in self.databases:
                self.databases[:] = [d for d in self.databases if d != db_name]
                self.send_response(302)
                self.send_header("Location", "/web/database/manager")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>error: Database does not exist</body></html>")


def test_list_databases() -> None:
    server, port = start_stub_server(DBHandler, databases=["db1", "db2", "db3"])
    client = make_client(base_url=f"http://127.0.0.1:{port}")

    dbs = client.database.list()
    assert dbs == ["db1", "db2", "db3"], f"Got {dbs}"

    server.shutdown()
    print("test_list_databases PASSED")


def test_exists() -> None:
    server, port = start_stub_server(DBHandler, databases=["db1", "db2", "db3"])
    client = make_client(base_url=f"http://127.0.0.1:{port}")

    assert client.database.exists("db1") is True
    assert client.database.exists("nonexistent") is False

    server.shutdown()
    print("test_exists PASSED")


def test_drop_local() -> None:
    server, port = start_stub_server(DBHandler, databases=["db1", "db2", "db3"])
    client = make_client(base_url=f"http://127.0.0.1:{port}")

    result = client.database.drop("db1")
    assert result.db == "db1"

    dbs = client.database.list()
    assert "db1" not in dbs
    assert dbs == ["db2", "db3"]

    server.shutdown()
    print("test_drop_local PASSED")


def test_drop_remote_refused() -> None:
    client = make_client(base_url="http://example.com:8069")

    try:
        client.database.drop("test")
        assert False, "Should have raised RemoteInstanceError"
    except RemoteInstanceError:
        pass
    print("test_drop_remote_refused PASSED")


if __name__ == "__main__":
    test_list_databases()
    test_exists()
    test_drop_local()
    test_drop_remote_refused()
