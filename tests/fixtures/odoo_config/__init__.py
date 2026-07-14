"""Odoo config file fixtures."""

from pathlib import Path

FIXTURES: dict[str, str] = {
    "loopback.ini": "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n",
    "wildcard.ini": "[options]\nhttp_interface = 0.0.0.0\nhttp_port = 8069\n",
    "comma_db_name.ini": "[options]\nhttp_interface = 127.0.0.1\ndb_name = db1,db2,db3\n",
    "invalid_port.ini": "[options]\nhttp_interface = 127.0.0.1\nhttp_port = not_a_number\n",
    "explicit_override.ini": (
        "[options]\n"
        "http_interface = 0.0.0.0\n"
        "http_port = 9999\n"
        "admin_passwd = secret_pwd\n"
        "db_name = mydb\n"
    ),
    "ipv6.ini": "[options]\nhttp_interface = ::1\nhttp_port = 8069\n",
    "missing_section.ini": "[other]\nkey = val\n",
}


def write_fixtures(dst_dir: Path) -> dict[str, Path]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for name, content in FIXTURES.items():
        path = dst_dir / name
        path.write_text(content)
        result[name] = path
    return result
