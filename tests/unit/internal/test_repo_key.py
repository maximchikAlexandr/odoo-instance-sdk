from __future__ import annotations

from pathlib import Path

from odoo_instance_sdk.internal.repo_key import git_common_dir, parse_git_common_dir


def test_parse_git_common_dir_validates_directory_and_file_markers(tmp_path: Path) -> None:
    regular = tmp_path / "regular"
    (regular / ".git").mkdir(parents=True)
    assert parse_git_common_dir(regular) == regular / ".git"

    worktree = tmp_path / "worktree"
    target = tmp_path / "main" / ".git" / "worktrees" / "child"
    target.mkdir(parents=True)
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {target}\n")
    assert parse_git_common_dir(worktree) == tmp_path / "main" / ".git"


def test_git_common_dir_keeps_legacy_fallback_for_invalid_markers(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").write_text("malformed\n")

    assert parse_git_common_dir(repository) is None
    assert git_common_dir(repository) == (repository / ".git").resolve()
