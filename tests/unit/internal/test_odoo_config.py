from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.odoo_config import (
    get_admin_passwd,
    infer_base_url,
    parse_db_names,
    parse_odoo_config,
)
from odoo_instance_sdk.models import StartConfig
from tests.fixtures.odoo_config import write_fixtures


def _write_config(content: str, tmp_path: Path) -> Path:
    path = tmp_path / "odoo.conf"
    path.write_text(content)
    return path


class TestParseOdooConfig:
    def test_loopback_config(self, tmp_path: Path) -> None:
        path = _write_config("[options]\nhttp_port = 8070\nhttp_interface = 127.0.0.1\n", tmp_path)
        config = parse_odoo_config(path)
        url = infer_base_url(config)
        assert url == "http://127.0.0.1:8070"

    def test_ipv6_config(self, tmp_path: Path) -> None:
        path = _write_config("[options]\nhttp_interface = ::1\n", tmp_path)
        config = parse_odoo_config(path)
        url = infer_base_url(config)
        assert url == "http://[::1]:8069"

    def test_wildcard_interface_raises(self, tmp_path: Path) -> None:
        path = _write_config("[options]\nhttp_interface = 0.0.0.0\n", tmp_path)
        config = parse_odoo_config(path)
        with pytest.raises(InstanceConfigurationError):
            infer_base_url(config)

    def test_missing_section_requires_explicit_base_url(self, tmp_path: Path) -> None:
        path = _write_config("[other]\nkey = val\n", tmp_path)
        config = parse_odoo_config(path)
        assert config == {}
        with pytest.raises(InstanceConfigurationError):
            infer_base_url(config)
        url = infer_base_url(config, base_url="http://localhost:8069")
        assert url == "http://localhost:8069"

    def test_explicit_override_has_priority(self, tmp_path: Path) -> None:
        path = _write_config("[options]\nhttp_port = 9999\nhttp_interface = 127.0.0.1\n", tmp_path)
        config = parse_odoo_config(path)
        url = infer_base_url(config, base_url="http://custom:1234")
        assert url == "http://custom:1234"

    def test_explicit_override_uses_fixture(self, tmp_path: Path) -> None:
        fixtures = write_fixtures(tmp_path)
        result = infer_base_url(
            parse_odoo_config(fixtures["wildcard.ini"]),
            base_url="http://localhost:9999",
        )
        assert result == "http://localhost:9999"

    def test_loopback_fixture(self, tmp_path: Path) -> None:
        fixtures = write_fixtures(tmp_path)
        config = parse_odoo_config(fixtures["loopback.ini"])
        assert infer_base_url(config) == "http://127.0.0.1:8069"

    def test_ipv6_fixture(self, tmp_path: Path) -> None:
        fixtures = write_fixtures(tmp_path)
        config = parse_odoo_config(fixtures["ipv6.ini"])
        assert "::1" in infer_base_url(config)

    def test_missing_section_fixture(self, tmp_path: Path) -> None:
        fixtures = write_fixtures(tmp_path)
        config = parse_odoo_config(fixtures["missing_section.ini"])
        assert config == {}

    def test_explicit_override_fixture(self, tmp_path: Path) -> None:
        fixtures = write_fixtures(tmp_path)
        config = parse_odoo_config(fixtures["explicit_override.ini"])
        result = infer_base_url(config, base_url="http://localhost:9999")
        assert result == "http://localhost:9999"

    def test_comma_db_name_fixture(self, tmp_path: Path) -> None:
        fixtures = write_fixtures(tmp_path)
        config = parse_odoo_config(fixtures["comma_db_name.ini"])
        assert parse_db_names(config.get("db_name")) == ("db1", "db2", "db3")

    def test_invalid_port_falls_back(self, tmp_path: Path) -> None:
        fixtures = write_fixtures(tmp_path)
        config = parse_odoo_config(fixtures["invalid_port.ini"])
        result = infer_base_url(config)
        assert ":8069" in result


class TestParseDbNames:
    def test_comma_separated(self) -> None:
        assert parse_db_names("db1,db2,db3") == ("db1", "db2", "db3")

    def test_default_to_empty(self) -> None:
        assert parse_db_names(None) == ()

    def test_empty_string(self) -> None:
        assert parse_db_names("") == ()

    def test_with_whitespace(self) -> None:
        assert parse_db_names(" db1 , db2 ") == ("db1", "db2")


class TestGetAdminPasswd:
    def test_missing_returns_admin_default(self) -> None:
        with pytest.warns(UserWarning, match="admin_passwd not set"):
            assert get_admin_passwd({}) == "admin"

    def test_custom_value(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert get_admin_passwd({"admin_passwd": "mypass"}) == "mypass"


class TestStartConfigFromOdooConfig:
    def test_reads_db_fields(self, tmp_path: Path) -> None:
        path = _write_config(
            "[options]\ndb_host = localhost\ndb_user = odoo\ndb_password = secret\n"
            "http_port = 8070\nhttp_interface = 127.0.0.1\nlog_level = debug\n",
            tmp_path,
        )
        sc = StartConfig.from_odoo_config(path)
        assert sc.db_host == "localhost"
        assert sc.db_user == "odoo"
        assert sc.db_password == "secret"
        assert sc.http_port == 8070
        assert sc.http_interface == "127.0.0.1"
        assert sc.log_level == "debug"

    def test_empty_config_uses_defaults(self, tmp_path: Path) -> None:
        path = _write_config("[options]\n", tmp_path)
        sc = StartConfig.from_odoo_config(path)
        assert sc.http_port == 8069
        assert sc.http_interface == "127.0.0.1"
        assert sc.db_host is None
        assert sc.db_password is None

    def test_invalid_port_skipped(self, tmp_path: Path) -> None:
        path = _write_config("[options]\nhttp_port = notanumber\n", tmp_path)
        sc = StartConfig.from_odoo_config(path)
        assert sc.http_port == 8069

    def test_addons_path_split(self, tmp_path: Path) -> None:
        path = _write_config("[options]\naddons_path = /a,/b,/c\n", tmp_path)
        sc = StartConfig.from_odoo_config(path)
        assert sc.addons_path == ["/a", "/b", "/c"]

    def test_empty_values_skipped(self, tmp_path: Path) -> None:
        path = _write_config("[options]\ndb_host = \n", tmp_path)
        sc = StartConfig.from_odoo_config(path)
        assert sc.db_host is None

    def test_log_level_debug(self, tmp_path: Path) -> None:
        path = _write_config("[options]\nlog_level = debug\n", tmp_path)
        sc = StartConfig.from_odoo_config(path)
        assert sc.log_level == "debug"

    def test_log_level_info(self, tmp_path: Path) -> None:
        path = _write_config("[options]\nlog_level = info\n", tmp_path)
        sc = StartConfig.from_odoo_config(path)
        assert sc.log_level == "info"

    def test_dev_mode_all_stays_string(self, tmp_path: Path) -> None:
        path = _write_config("[options]\ndev_mode = all\n", tmp_path)
        sc = StartConfig.from_odoo_config(path)
        assert sc.dev_mode == "all"
        assert isinstance(sc.dev_mode, str)

    def test_dev_mode_comma_list(self, tmp_path: Path) -> None:
        path = _write_config("[options]\ndev_mode = all,queue\n", tmp_path)
        sc = StartConfig.from_odoo_config(path)
        assert sc.dev_mode == ["all", "queue"]

    def test_invalid_int_warns(self, tmp_path: Path) -> None:
        path = _write_config("[options]\nworkers = notanumber\n", tmp_path)
        with pytest.warns(UserWarning, match="Invalid int for workers"):
            sc = StartConfig.from_odoo_config(path)
        assert sc.workers is None


if __name__ == "__main__":
    pytest.main([__file__])
