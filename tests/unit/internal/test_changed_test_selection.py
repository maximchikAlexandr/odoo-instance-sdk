from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal import test_selection
from odoo_instance_sdk.internal.test_selection import resolve_changed_selection


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch", "main")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "test")
    addons = repo / "addons"
    sale = addons / "sale"
    stock = addons / "stock"
    (sale / "tests").mkdir(parents=True)
    (stock / "tests").mkdir(parents=True)
    (sale / "__manifest__.py").write_text("{}\n")
    (stock / "__manifest__.py").write_text("{}\n")
    (sale / "tests" / "test_initial.py").write_text("# sale\n")
    (stock / "tests" / "test_initial.py").write_text("# stock\n")
    (repo / "README.md").write_text("docs\n")
    _commit(repo, "base")
    return repo, sale, stock


def test_changed_selection_unions_all_four_states_and_maps_direct_modules(tmp_path: Path) -> None:
    repo, sale, stock = _repo(tmp_path)
    (sale / "tests" / "test_initial.py").write_text("# committed\n")
    _commit(repo, "committed sale change")

    (stock / "tests" / "test_initial.py").write_text("# staged\n")
    _git(repo, "add", str(stock.relative_to(repo) / "tests" / "test_initial.py"))
    (sale / "tests" / "test_unstaged.py").write_text("# unstaged\n")
    untracked = stock / "tests" / "test_untracked.py"
    untracked.write_text("# untracked\n")
    (repo / "README.md").write_text("docs changed\n")

    selection = resolve_changed_selection(repo, ["addons"], base="HEAD~1")

    assert selection.changed_files == tuple(sorted(selection.changed_files))
    assert {
        "addons/sale/tests/test_initial.py",
        "addons/stock/tests/test_initial.py",
        "addons/sale/tests/test_unstaged.py",
        "addons/stock/tests/test_untracked.py",
        "README.md",
    } == set(selection.changed_files)
    assert selection.modules == ("sale", "stock")
    assert selection.test_tags == "/sale,/stock"
    assert selection.ignored_paths == ("README.md",)
    assert selection.unmapped_paths == ()
    assert selection.executable


def test_changed_selection_disables_rename_detection_and_preserves_both_addons(
    tmp_path: Path,
) -> None:
    repo, sale, stock = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    old = sale / "tests" / "test_initial.py"
    new = stock / "tests" / "test_renamed.py"
    _git(repo, "mv", str(old.relative_to(repo)), str(new.relative_to(repo)))

    selection = resolve_changed_selection(repo, ["addons"], base=base)

    assert selection.changed_files == (
        "addons/sale/tests/test_initial.py",
        "addons/stock/tests/test_renamed.py",
    )
    assert selection.modules == ("sale", "stock")


def test_changed_selection_preserves_adversarial_nul_decoded_filename_and_ignores_gitignore(
    tmp_path: Path,
) -> None:
    repo, sale, _stock = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "config", "core.quotePath", "false")
    (repo / ".gitignore").write_text("ignored.py\n")
    weird = sale / "tests" / "test space\n$(not-a-command).py"
    weird.write_text("# adversarial\n")
    (sale / "tests" / "ignored.py").write_text("# ignored\n")

    selection = resolve_changed_selection(
        repo, ["addons"], base=base, tags=" standard,/stock,-slow "
    )

    assert weird.relative_to(repo).as_posix() in selection.changed_files
    assert "addons/sale/tests/ignored.py" not in selection.changed_files
    assert selection.modules == ("sale",)
    assert selection.test_tags == " standard,/stock,-slow "


def test_paths_under_addons_without_safe_manifest_are_fatal_unmapped_paths(tmp_path: Path) -> None:
    repo, _sale, _stock = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    unsafe = repo / "addons" / "not_an_addon" / "file.py"
    unsafe.parent.mkdir()
    unsafe.write_text("# no manifest\n")

    selection = resolve_changed_selection(repo, ["addons"], base=base)

    assert selection.unmapped_paths == ("addons/not_an_addon/file.py",)
    assert selection.modules == ()
    assert not selection.executable


def test_symlinked_changed_path_is_unmapped_and_docs_only_is_ignored(tmp_path: Path) -> None:
    repo, sale, _stock = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    external = tmp_path / "external.py"
    external.write_text("# outside\n")
    linked = sale / "tests" / "test_escape.py"
    linked.symlink_to(external)
    (repo / "notes.md").write_text("docs\n")

    selection = resolve_changed_selection(repo, ["addons"], base=base)

    assert selection.unmapped_paths == ("addons/sale/tests/test_escape.py",)
    assert "notes.md" in selection.ignored_paths
    assert not selection.executable


def test_no_addon_changes_is_safe_success_with_complete_base_provenance(tmp_path: Path) -> None:
    repo, _sale, _stock = _repo(tmp_path)
    (repo / "README.md").write_text("docs only\n")
    base = _git(repo, "rev-parse", "HEAD")

    selection = resolve_changed_selection(repo, ["addons"], environment_base=base)

    assert selection.base_source == "environment"
    assert selection.requested_base == base
    assert selection.resolved_base == base
    assert selection.merge_base == base
    assert selection.head == base
    assert selection.modules == ()
    assert selection.test_tags is None
    assert selection.ignored_paths == ("README.md",)
    assert selection.unmapped_paths == ()


@pytest.mark.parametrize("base", [None, "", "HEAD"])
def test_changed_selection_requires_non_head_base(tmp_path: Path, base: str | None) -> None:
    repo, _sale, _stock = _repo(tmp_path)

    with pytest.raises(ConfigError, match="requires --base"):
        resolve_changed_selection(repo, ["addons"], base=base, environment_base="HEAD")


def test_changed_selection_rejects_missing_base_ref(tmp_path: Path) -> None:
    repo, _sale, _stock = _repo(tmp_path)

    with pytest.raises(ConfigError, match="failed"):
        resolve_changed_selection(repo, ["addons"], base="does-not-exist")


def test_git_calls_are_bounded_argv_only_and_head_stability_is_checked(tmp_path: Path) -> None:
    repo, sale, _stock = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (sale / "tests" / "test_change.py").write_text("# change\n")
    with patch(
        "odoo_instance_sdk.internal.test_selection.subprocess.Popen",
        wraps=subprocess.Popen,
    ) as popen:
        selection = resolve_changed_selection(repo, ["addons"], base=base)

    assert selection.modules == ("sale",)
    calls = [(list(call.args[0]), dict(call.kwargs)) for call in popen.call_args_list]
    assert calls
    assert all(kwargs["shell"] is False for _command, kwargs in calls)
    assert all(kwargs["stdout"] is subprocess.PIPE for _command, kwargs in calls)
    assert all(kwargs["stderr"] is subprocess.PIPE for _command, kwargs in calls)
    assert all(command[0:2] == ["git", "-C"] for command, _kwargs in calls)
    assert all(
        command[3] not in {"fetch", "pull", "push", "reset", "checkout"}
        for command, _kwargs in calls
    )
    assert any("--no-renames" in command for command, _kwargs in calls)
    assert any(
        command[3:] == ["ls-files", "--others", "--exclude-standard", "-z"]
        for command, _kwargs in calls
    )


def test_head_change_during_collection_fails_closed(tmp_path: Path) -> None:
    repo, sale, _stock = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (sale / "tests" / "test_change.py").write_text("# change\n")
    original_revision = test_selection._git_revision
    head_calls = 0

    def changing_revision(
        ref: str, worktree: Path, *, timeout: float, max_output_bytes: int
    ) -> str:
        nonlocal head_calls
        if ref == "HEAD":
            head_calls += 1
            if head_calls == 2:
                return "f" * 40
        return original_revision(ref, worktree, timeout=timeout, max_output_bytes=max_output_bytes)

    with (
        patch.object(test_selection, "_git_revision", changing_revision),
        pytest.raises(ConfigError, match="HEAD changed"),
    ):
        resolve_changed_selection(repo, ["addons"], base=base)


@pytest.mark.parametrize("redirect", ["", " >&2"])
def test_git_output_limit_terminates_real_child_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redirect: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "terminated"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "trap 'printf terminated > \"$GIT_TERM_MARKER\"; exit 0' TERM INT\n"
        "while :; do printf '%s' "
        f"'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'{redirect}; done\n"
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_TERM_MARKER", str(marker))

    with pytest.raises(ConfigError, match="output exceeded"):
        test_selection._run_git_bytes(("status",), tmp_path, max_output_bytes=128)

    assert marker.read_text() == "terminated"


def test_git_timeout_terminates_kills_and_reaps_real_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "term-received"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        'printf started > "$GIT_TERM_MARKER"\n'
        "trap 'printf term > \"$GIT_TERM_MARKER\"' TERM\n"
        "while :; do :; done\n"
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_TERM_MARKER", str(marker))
    children: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def record_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = cast("subprocess.Popen[bytes]", real_popen(*args, **kwargs))
        children.append(process)
        return process

    with (
        patch.object(subprocess, "Popen", side_effect=record_popen),
        pytest.raises(ConfigError, match="git timed out"),
    ):
        test_selection._run_git_bytes(("status",), tmp_path, timeout=1.0)

    assert len(children) == 1
    child = children[0]
    assert marker.read_text() == "term"
    assert child.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(child.pid, 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(child.pid, os.WNOHANG)
