from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.backup_retention import (
    read_retention_policy,
    write_retention_policy,
)
from odoo_instance_sdk.models import BackupRetentionPolicy


def test_retention_defaults_and_atomic_update_preserve_unrelated_toml(tmp_path: Path) -> None:
    path = tmp_path / "config" / "user.toml"
    assert read_retention_policy(path).retention_days == 14

    path.parent.mkdir()
    path.write_text('[other]\nkeep = "yes"\n\n[backup]\nmax_uncompressed_bytes = 42\n')
    assert write_retention_policy(
        BackupRetentionPolicy(retention_days=7, auto_prune=True, path=str(path))
    )

    assert read_retention_policy(path).auto_prune is True
    assert "max_uncompressed_bytes = 42" in path.read_text()
    assert '[other]\nkeep = "yes"' in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "content",
    [
        "[backup]\nretention_days = 0\n",
        "[backup]\nretention_days = true\n",
        '[backup]\nauto_prune = "yes"\n',
        "[backup\n",
    ],
)
def test_retention_rejects_malformed_settings(tmp_path: Path, content: str) -> None:
    path = tmp_path / "user.toml"
    path.write_text(content)
    with pytest.raises(ConfigError):
        read_retention_policy(path)
