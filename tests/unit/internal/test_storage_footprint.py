from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from odoo_instance_sdk.internal import storage_footprint
from odoo_instance_sdk.internal.storage_footprint import (
    _directory_size,
    clear_storage_footprint_cache,
    collect_storage_footprint,
)


def _make_worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "worktree"
    (worktree / "subdir").mkdir(parents=True)
    (worktree / "a.txt").write_bytes(b"hello")
    (worktree / "subdir" / "b.txt").write_bytes(b"world!!")
    return worktree


def _kwargs(tmp_path: Path, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "environment_id": "env-1",
        "worktree_path": tmp_path / "worktree",
        "python_environment_path": tmp_path / "venv",
        "python_environment_owned": False,
        "db_mode": "shared",
        "generated_config_path": tmp_path / "cfg.conf",
        "dependency_lock_path": tmp_path / "lock.txt",
        "target_db_name": None,
        "db_host": None,
        "db_port": None,
        "db_user": None,
        "db_password": None,
        "data_dir": None,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_storage_footprint_cache()


def test_owned_venv_included_in_total(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    venv = tmp_path / "venv"
    (venv).mkdir(parents=True)
    (venv / "lib.txt").write_bytes(b"x" * 64)
    footprint = collect_storage_footprint(
        **_kwargs(
            tmp_path,
            worktree_path=worktree,
            python_environment_path=venv,
            python_environment_owned=True,
        )
    )
    assert footprint.python_environment.owned is True
    assert footprint.python_environment.bytes is not None
    assert footprint.python_environment.bytes >= 64
    assert (
        footprint.total_bytes
        >= (footprint.worktree_bytes or 0) + footprint.python_environment.bytes
    )


def test_reused_venv_excluded_from_total(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    footprint = collect_storage_footprint(
        **_kwargs(
            tmp_path, worktree_path=worktree, python_environment_path=tmp_path / "missing-venv"
        )
    )
    assert footprint.python_environment.owned is False
    assert footprint.python_environment.bytes is None
    assert footprint.complete is True
    base = (footprint.worktree_bytes or 0) + footprint.other_files_bytes
    assert footprint.total_bytes == base


def test_shared_db_excluded(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    footprint = collect_storage_footprint(
        **_kwargs(tmp_path, worktree_path=worktree, db_mode="shared")
    )
    assert footprint.database.owned is False
    assert footprint.database.postgres_bytes is None
    assert footprint.database.filestore_bytes is None
    assert footprint.database.total_bytes is None
    assert footprint.complete is True


def test_copy_db_psql_missing_marks_incomplete(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    with patch("odoo_instance_sdk.internal.postgres_size.shutil.which", return_value=None):
        footprint = collect_storage_footprint(
            **_kwargs(
                tmp_path,
                worktree_path=worktree,
                db_mode="copy",
                target_db_name="mydb",
                db_user="u",
                db_port=5432,
            )
        )
    assert footprint.database.owned is True
    assert footprint.database.postgres_bytes is None
    assert footprint.complete is False


def test_copy_db_success_included(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    with patch(
        "odoo_instance_sdk.internal.storage_footprint.database_size_bytes",
        return_value=4096,
    ):
        footprint = collect_storage_footprint(
            **_kwargs(
                tmp_path,
                worktree_path=worktree,
                db_mode="copy",
                target_db_name="mydb",
                db_user="u",
                db_port=5432,
            )
        )
    assert footprint.database.owned is True
    assert footprint.database.postgres_bytes == 4096
    assert footprint.database.total_bytes == 4096
    assert footprint.total_bytes >= (footprint.worktree_bytes or 0) + 4096


def test_du_used_when_available(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    du = shutil.which("du")
    if du is None:
        pytest.skip("du not installed; walk path covered by other tests")
    size = _directory_size(worktree)
    assert size is not None
    assert size >= 10


def test_walk_fallback_when_du_missing(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    (worktree / "d").mkdir(parents=True)
    (worktree / "a.txt").write_bytes(b"hello")
    (worktree / "d" / "b.txt").write_bytes(b"world!!")
    with patch("odoo_instance_sdk.internal.storage_footprint.shutil.which", return_value=None):
        size = _directory_size(worktree)
    assert size is not None
    assert size == 12


def test_walk_fallback_skips_symlink_targets(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    (worktree / "d").mkdir(parents=True)
    (worktree / "a.txt").write_bytes(b"hello")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 4096)
    os.symlink(outside / "big.bin", worktree / "link.bin")
    os.symlink(outside, worktree / "d" / "linkdir")
    with patch("odoo_instance_sdk.internal.storage_footprint.shutil.which", return_value=None):
        size = _directory_size(worktree)
    assert size is not None
    assert size == 5


def test_du_path_uses_du(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    du = shutil.which("du")
    if du is None:
        pytest.skip("du not installed")
    with patch(
        "odoo_instance_sdk.internal.storage_footprint.subprocess.run",
        wraps=__import__("subprocess").run,
    ) as spy:
        _directory_size(worktree)
    called_du = any(call.args[0][0].endswith("du") for call in spy.call_args_list)
    assert called_du


def test_cache_returns_same_result_then_expires(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    first = collect_storage_footprint(**_kwargs(tmp_path, worktree_path=worktree))
    second = collect_storage_footprint(**_kwargs(tmp_path, worktree_path=worktree))
    assert first is second
    # expire the cache and confirm recomputation
    clear_storage_footprint_cache()
    third = collect_storage_footprint(**_kwargs(tmp_path, worktree_path=worktree))
    assert third.total_bytes == first.total_bytes
    assert third is not first


def test_cache_ttl_window(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    base = time.monotonic()
    calls: list[float] = []

    def fake_monotonic() -> float:
        return base + len(calls) * 0.001

    with patch.object(storage_footprint.time, "monotonic", side_effect=fake_monotonic):
        first = collect_storage_footprint(**_kwargs(tmp_path, worktree_path=worktree))
        second = collect_storage_footprint(**_kwargs(tmp_path, worktree_path=worktree))
    assert first is second
