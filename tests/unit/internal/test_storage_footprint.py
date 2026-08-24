from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TypedDict, cast
from unittest.mock import patch

import pytest

from odoo_instance_sdk.internal.storage_footprint import (
    DatabaseStorageInput,
    _directory_size,
    collect_storage_footprint,
)


def _make_worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "worktree"
    (worktree / "subdir").mkdir(parents=True)
    (worktree / "a.txt").write_bytes(b"hello")
    (worktree / "subdir" / "b.txt").write_bytes(b"world!!")
    return worktree


class StorageKwargs(TypedDict):
    worktree_path: Path
    python_environment_path: Path
    python_environment_owned: bool
    generated_config_path: Path
    dependency_lock_path: Path
    database: DatabaseStorageInput


def _kwargs(tmp_path: Path, **overrides: object) -> StorageKwargs:
    base: StorageKwargs = {
        "worktree_path": tmp_path / "worktree",
        "python_environment_path": tmp_path / "venv",
        "python_environment_owned": False,
        "generated_config_path": tmp_path / "cfg.conf",
        "dependency_lock_path": tmp_path / "lock.txt",
        "database": DatabaseStorageInput(
            mode="shared",
            target_name=None,
            host=None,
            port=None,
            user=None,
            password=None,
            data_dir=None,
        ),
    }
    return cast("StorageKwargs", {**base, **overrides})


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
    python_bytes = footprint.python_environment.bytes
    assert footprint.python_environment.bytes >= 64
    assert footprint.total_bytes >= (footprint.worktree_bytes or 0) + python_bytes


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
    other_files_bytes = footprint.other_files_bytes
    assert other_files_bytes is not None
    base = (footprint.worktree_bytes or 0) + other_files_bytes
    assert footprint.total_bytes == base


def test_shared_db_excluded(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    footprint = collect_storage_footprint(**_kwargs(tmp_path, worktree_path=worktree))
    assert footprint.database.owned is False
    assert footprint.database.postgres_bytes is None
    assert footprint.database.filestore_bytes is None
    assert footprint.database.total_bytes is None
    assert footprint.complete is True


def test_copy_db_psql_missing_marks_incomplete(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    with patch("odoo_instance_sdk.internal.postgres_transport.shutil.which", return_value=None):
        footprint = collect_storage_footprint(
            **_kwargs(
                tmp_path,
                worktree_path=worktree,
                database=DatabaseStorageInput(
                    mode="copy",
                    target_name="mydb",
                    host=None,
                    port=5432,
                    user="u",
                    password=None,
                    data_dir=None,
                ),
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
                database=DatabaseStorageInput(
                    mode="copy",
                    target_name="mydb",
                    host=None,
                    port=5432,
                    user="u",
                    password=None,
                    data_dir=None,
                ),
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


def test_collection_is_stateless(tmp_path: Path) -> None:
    worktree = _make_worktree(tmp_path)
    first = collect_storage_footprint(**_kwargs(tmp_path, worktree_path=worktree))
    second = collect_storage_footprint(**_kwargs(tmp_path, worktree_path=worktree))
    assert first == second
    assert first is not second
