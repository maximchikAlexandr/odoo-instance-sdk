from __future__ import annotations

import configparser
import os
from pathlib import Path

from odoo_instance_sdk.internal.generated_config import generate_config


def _write_source_config(path: Path, **options: str) -> None:
    cp = configparser.RawConfigParser(interpolation=None)
    cp.add_section("options")
    for k, v in options.items():
        cp.set("options", k, v)
    with open(path, "w") as f:
        cp.write(f)
    os.chmod(path, 0o644)


def _read_config(path: Path) -> dict[str, str]:
    cp = configparser.RawConfigParser(interpolation=None)
    cp.read(str(path))
    if not cp.has_section("options"):
        return {}
    return dict(cp.items("options"))


class TestGenerateConfig:
    def test_atomic_write_0600(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        src = tmp_path / "odoo.conf"
        _write_source_config(src, db_host="localhost", db_port="5432")
        dest = tmp_path / "generated.conf"
        generate_config(
            src,
            dest,
            repo_root=repo,
            worktree=worktree,
            http_interface="127.0.0.1",
            http_port=8070,
            db_name="mydb",
        )
        mode = oct(os.stat(dest).st_mode & 0o777)
        assert mode == "0o600"

    def test_source_unchanged(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        src = tmp_path / "odoo.conf"
        _write_source_config(src, db_name="original", http_port="8069")
        dest = tmp_path / "generated.conf"
        generate_config(
            src,
            dest,
            repo_root=repo,
            worktree=worktree,
            http_interface="127.0.0.1",
            http_port=8070,
            db_name="mydb",
        )
        source_cfg = _read_config(src)
        assert source_cfg["db_name"] == "original"
        assert source_cfg["http_port"] == "8069"

    def test_repo_local_addons_rebased(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "addons").mkdir(parents=True)
        worktree = tmp_path / "worktree"
        (worktree / "addons").mkdir(parents=True)
        src = tmp_path / "odoo.conf"
        _write_source_config(
            src,
            addons_path="./addons,/opt/odoo/addons",
        )
        dest = tmp_path / "generated.conf"
        generate_config(
            src,
            dest,
            repo_root=repo,
            worktree=worktree,
            http_interface="127.0.0.1",
            http_port=8070,
            db_name="mydb",
        )
        gen = _read_config(dest)
        paths = [p.strip() for p in gen["addons_path"].split(",")]
        assert str((worktree / "addons").resolve()) in paths
        assert "/opt/odoo/addons" in paths

    def test_external_preserved(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        src = tmp_path / "odoo.conf"
        _write_source_config(src, addons_path="/opt/odoo/addons,/opt/other")
        dest = tmp_path / "generated.conf"
        generate_config(
            src,
            dest,
            repo_root=repo,
            worktree=worktree,
            http_interface="127.0.0.1",
            http_port=8070,
            db_name="mydb",
        )
        gen = _read_config(dest)
        paths = [p.strip() for p in gen["addons_path"].split(",")]
        assert "/opt/odoo/addons" in paths
        assert "/opt/other" in paths

    def test_unknown_keys_preserved(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        src = tmp_path / "odoo.conf"
        _write_source_config(
            src,
            db_host="localhost",
            db_port="5432",
            admin_passwd="secret",
            data_dir="/var/lib/odoo",
            custom_key="custom_value",
        )
        dest = tmp_path / "generated.conf"
        generate_config(
            src,
            dest,
            repo_root=repo,
            worktree=worktree,
            http_interface="127.0.0.1",
            http_port=8070,
            db_name="mydb",
        )
        gen = _read_config(dest)
        assert gen["custom_key"] == "custom_value"
        assert gen["db_host"] == "localhost"
        assert gen["admin_passwd"] == "secret"
        assert gen["data_dir"] == "/var/lib/odoo"

    def test_dbfilter_set(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        src = tmp_path / "odoo.conf"
        _write_source_config(src)
        dest = tmp_path / "generated.conf"
        generate_config(
            src,
            dest,
            repo_root=repo,
            worktree=worktree,
            http_interface="127.0.0.1",
            http_port=8070,
            db_name="targetdb",
        )
        gen = _read_config(dest)
        assert gen["dbfilter"] == "targetdb"
        assert gen["db_name"] == "targetdb"

    def test_http_interface_default(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        src = tmp_path / "odoo.conf"
        _write_source_config(src)
        dest = tmp_path / "generated.conf"
        generate_config(
            src,
            dest,
            repo_root=repo,
            worktree=worktree,
            http_interface="127.0.0.1",
            http_port=8070,
            db_name="mydb",
        )
        gen = _read_config(dest)
        assert gen["http_interface"] == "127.0.0.1"
