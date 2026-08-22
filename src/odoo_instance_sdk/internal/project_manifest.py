from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from odoo_instance_sdk.project import ProjectConfig

_MANIFEST_DIR = ".odcli"
_MANIFEST_FILE = "project.toml"

_SECRET_KEYS = frozenset(
    {
        "admin_passwd",
        "master_password",
        "master_pwd",
        "db_password",
        "password",
        "secret",
        "token",
        "api_key",
    }
)


def manifest_path(project_path: str | Path) -> Path:
    return Path(project_path) / _MANIFEST_DIR / _MANIFEST_FILE


def write_manifest(project_path: str | Path, config: ProjectConfig) -> Path:
    dest = manifest_path(project_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = config.to_manifest()
    assert_no_secrets(content)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp", prefix=_MANIFEST_FILE)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, dest)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return dest


def assert_no_secrets(content: str) -> None:
    lowered = content.lower()
    for key in _SECRET_KEYS:
        if key in lowered:
            raise ValueError(f"Refusing to write secret-like key {key!r} to project manifest")
