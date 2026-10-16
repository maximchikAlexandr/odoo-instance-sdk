from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_planning import discover_dependency_inputs


def test_discovery_walks_nested_modules_and_deduplicates_in_order(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    first = addons / "a" / "nested" / "module_a"
    second = addons / "b" / "module_b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    for module in (first, second):
        (module / "__manifest__.py").write_text("{}", encoding="utf-8")
    (first / "requirements.txt").write_text("a", encoding="utf-8")
    (first.parent / "requirements.txt").write_text("shared", encoding="utf-8")
    (second.parent / "requirements.txt").symlink_to(first.parent / "requirements.txt")
    (tmp_path / "odoo-bin").write_text("", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("odoo", encoding="utf-8")
    explicit = tmp_path / "explicit.txt"
    explicit.write_text("explicit", encoding="utf-8")

    result = discover_dependency_inputs(
        ProjectConfig(
            repository_root=tmp_path,
            addon_roots=(Path("addons"),),
            odoo_bin=Path("odoo-bin"),
            requirements=("explicit.txt",),
        ),
        tmp_path,
        tmp_path,
    )

    assert result == tuple(
        str(path.resolve())
        for path in (
            first / "requirements.txt",
            first.parent / "requirements.txt",
            tmp_path / "requirements.txt",
            explicit,
        )
    )


def test_explicit_missing_requirement_fails_before_planning(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="dependency input is unavailable"):
        discover_dependency_inputs(
            ProjectConfig(repository_root=tmp_path, requirements=("missing.txt",)),
            tmp_path,
            tmp_path,
        )


def test_discovery_rebases_identical_inputs_to_managed_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    module = repo / "addons" / "sale" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}", encoding="utf-8")
    (module / "requirements.txt").write_text("sale==1\n", encoding="utf-8")
    (module.parent / "requirements.txt").write_text("shared==1\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("odoo==1\n", encoding="utf-8")
    explicit = repo / "explicit.txt"
    explicit.write_text("explicit==1\n", encoding="utf-8")

    import shutil

    shutil.copytree(repo, worktree)
    config = ProjectConfig(
        repository_root=repo,
        addon_roots=(Path("addons"),),
        requirements=("explicit.txt",),
    )

    repo_inputs = discover_dependency_inputs(config, repo, repo)
    worktree_inputs = discover_dependency_inputs(config, repo, worktree)

    assert tuple(Path(path).relative_to(repo) for path in repo_inputs) == tuple(
        Path(path).relative_to(worktree) for path in worktree_inputs
    )
    assert all(str(worktree) in path for path in worktree_inputs)
