from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import LockConflictError
from odoo_instance_sdk.internal.locks import exclusive_lock, shared_lock


def test_exclusive_lock_conflict(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"
    held = threading.Event()

    def hold() -> None:
        with exclusive_lock(lock_path):
            held.set()
            time.sleep(0.5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(timeout=2)
    time.sleep(0.05)
    with pytest.raises(LockConflictError), exclusive_lock(lock_path):
        pass
    t.join(timeout=5)


def test_shared_lock_allows_multiple(tmp_path: Path) -> None:
    lock_path = tmp_path / "shared.lock"
    with shared_lock(lock_path), shared_lock(lock_path):
        pass


def test_exclusive_blocks_shared(tmp_path: Path) -> None:
    lock_path = tmp_path / "mixed.lock"
    held = threading.Event()

    def hold() -> None:
        with exclusive_lock(lock_path):
            held.set()
            time.sleep(0.5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(timeout=2)
    time.sleep(0.05)
    with pytest.raises(LockConflictError), shared_lock(lock_path):
        pass
    t.join(timeout=5)


def test_lock_released_on_exit(tmp_path: Path) -> None:
    lock_path = tmp_path / "release.lock"
    with exclusive_lock(lock_path):
        assert lock_path.exists()
    with exclusive_lock(lock_path):
        pass


def test_lock_file_persists_as_harmless_inode(tmp_path: Path) -> None:
    lock_path = tmp_path / "persist.lock"
    with exclusive_lock(lock_path):
        pass
    assert lock_path.exists()
