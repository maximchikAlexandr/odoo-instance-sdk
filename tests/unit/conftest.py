"""Fixtures shared by unit-level self-update tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.internal import pgadmin_master_password


@pytest.fixture(autouse=True)
def _stub_pgadmin_master_unlock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pgadmin_master_password,
        "unlock_master_password",
        lambda *_args, **_kwargs: None,
    )


@pytest.fixture
def user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".odcli"
    root.mkdir()
    locks = root / "locks"
    locks.mkdir()
    monkeypatch.setattr("odoo_instance_sdk.internal.self_update.get_user_root", lambda **_: root)
    monkeypatch.setattr("odoo_instance_sdk.internal.self_update.get_locks_dir", lambda **_: locks)
    return root
