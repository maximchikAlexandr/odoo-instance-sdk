from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.dependency_sync import (
    build_trusted_sync_argv,
    resolve_hash_lock,
    revalidate_hash_lock,
)


def test_hash_lock_validation_requires_a_complete_immutable_pair(tmp_path: Path) -> None:
    """E2E-SEC-07: the approved public sync pair is immutable and complete."""
    "E2E-SEC-07"
    lock = tmp_path / "requirements.lock"
    lock.write_text("requests==2.32.5\n", encoding="utf-8")
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()

    assert resolve_hash_lock(lock, digest) == lock.resolve()
    with pytest.raises(ConfigError, match="supplied together"):
        resolve_hash_lock(lock, None)
    with pytest.raises(ConfigError, match="digest mismatch"):
        resolve_hash_lock(lock, "0" * 64)


def test_hash_lock_revalidation_rejects_changed_bytes(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("requests==2.32.5\n", encoding="utf-8")
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    captured = lock.resolve()
    lock.write_text("requests==2.32.6\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="digest mismatch"):
        revalidate_hash_lock(captured, digest)


def test_trusted_sync_builder_is_hash_required_and_non_discovering() -> None:
    assert build_trusted_sync_argv("/venv/bin/python", "/locks/odoo.lock") == (
        "uv",
        "pip",
        "sync",
        "--python",
        "/venv/bin/python",
        "--require-hashes",
        "/locks/odoo.lock",
    )
