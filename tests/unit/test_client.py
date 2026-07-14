from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.resources.instance import OdooInstance


def _make_client() -> OdooClient:
    return OdooClient(config=OdooClientConfig(executable="python3"))


class TestClientAPI:
    def test_client_creates_instance(self) -> None:
        client = _make_client()
        inst_a = client.instance(base_url="http://localhost:8069")
        inst_b = client.instance(base_url="http://127.0.0.1:8070")

        assert isinstance(inst_a, OdooInstance)
        assert inst_a.config.base_url == "http://localhost:8069"
        assert inst_b.config.base_url == "http://127.0.0.1:8070"

    def test_client_optional_master_password(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        assert inst.config.master_password is None

    def test_client_master_password_passed(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069", master_password="mysecret")
        assert inst.config.master_password == "mysecret"

    def _write_conf(self, content: str, tmp_path: Path) -> Path:
        path = tmp_path / "odoo.conf"
        path.write_text(content)
        return path

    def test_client_from_config_success(self, tmp_path: Path) -> None:
        path = self._write_conf(
            "[options]\n"
            "http_port = 8070\n"
            "http_interface = 127.0.0.1\n"
            "admin_passwd = mypass\n"
            "db_name = db1,db2\n",
            tmp_path,
        )
        client = _make_client()
        inst = client.instance.from_config(path)

        assert isinstance(inst, OdooInstance)
        assert inst.config.base_url == "http://127.0.0.1:8070"
        assert inst.config.master_password == "mypass"
        assert inst.config.configured_database_names == ("db1", "db2")

    def test_client_from_config_with_override(self, tmp_path: Path) -> None:
        path = self._write_conf(
            "[options]\nhttp_port = 9999\nhttp_interface = 127.0.0.1\n",
            tmp_path,
        )
        client = _make_client()
        inst = client.instance.from_config(
            path,
            base_url="http://127.0.0.1:1234",
            master_password="override_pwd",
        )

        assert inst.config.base_url == "http://127.0.0.1:1234"
        assert inst.config.master_password == "override_pwd"

    def test_client_old_api_absent(self) -> None:
        client = _make_client()
        with pytest.raises(AttributeError):
            _ = client.server
        with pytest.raises(AttributeError):
            _ = client.database

    def test_shared_process_registry(self) -> None:
        client = _make_client()
        inst_a = client.instance(base_url="http://localhost:8069")
        inst_b = client.instance(base_url="http://127.0.0.1:8070")

        assert inst_a._client is inst_b._client

    def test_client_repr(self) -> None:
        client = _make_client()
        r = repr(client)
        assert "executable" in r
        assert "python3" in r


if __name__ == "__main__":
    pytest.main([__file__])
