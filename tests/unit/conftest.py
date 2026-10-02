"""Fixtures shared by unit-level self-update tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".odcli"
    root.mkdir()
    locks = root / "locks"
    locks.mkdir()
    monkeypatch.setattr("odoo_instance_sdk.internal.self_update.get_user_root", lambda **_: root)
    monkeypatch.setattr("odoo_instance_sdk.internal.self_update.get_locks_dir", lambda **_: locks)
    return root
