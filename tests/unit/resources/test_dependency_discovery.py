from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.proc import run as process_executor
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project_init import prepare_venv_command
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


@pytest.mark.parametrize("managed_worktree", [False, True])
def test_absolute_addon_requirements_reject_escape(tmp_path: Path, managed_worktree: bool) -> None:
    repo = tmp_path / "client"
    repo.mkdir()
    worktree = tmp_path / "checkout" if managed_worktree else repo
    worktree.mkdir(exist_ok=True)
    addons = tmp_path / "platform" / "addons"
    module = addons / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}")
    module_requirement = module / "requirements.txt"
    parent_requirement = addons / "requirements.txt"
    parent_requirement.write_text("parent-package==1\n")
    outside = tmp_path / "private.txt"
    outside.write_text("private-package==1\n")
    module_requirement.symlink_to(outside)
    config = ProjectConfig(repository_root=repo, addon_roots=(addons,))

    with pytest.raises(ConfigError, match="escapes its configured checkout boundary"):
        discover_dependency_inputs(config, repo, worktree)


@pytest.mark.parametrize("managed_worktree", [False, True])
def test_absolute_addon_requirements_discover_declared_boundary(
    tmp_path: Path, managed_worktree: bool
) -> None:
    repo = tmp_path / "client"
    repo.mkdir()
    worktree = tmp_path / "checkout" if managed_worktree else repo
    worktree.mkdir(exist_ok=True)
    addons = tmp_path / "platform" / "addons"
    module = addons / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}")
    module_requirement = module / "requirements.txt"
    parent_requirement = addons / "requirements.txt"
    parent_requirement.write_text("parent-package==1\n")
    module_requirement.write_text("module-package==1\n")
    config = ProjectConfig(repository_root=repo, addon_roots=(addons,))

    assert discover_dependency_inputs(config, repo, worktree) == (
        str(module_requirement),
        str(parent_requirement),
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


def test_discovery_accepts_external_odoo_bin_sibling_requirements(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    external_odoo = tmp_path / "external-odoo"
    project.mkdir()
    external_odoo.mkdir()
    odoo_bin = external_odoo / "odoo-bin"
    odoo_bin.write_text("", encoding="utf-8")
    requirements = external_odoo / "requirements.txt"
    requirements.write_text("odoo==1\n", encoding="utf-8")

    result = discover_dependency_inputs(
        ProjectConfig(repository_root=project, odoo_bin=odoo_bin), project, project
    )

    assert result == (str(requirements.resolve()),)


def test_discovery_accepts_relative_external_odoo_bin_sibling_requirements(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    external_odoo = tmp_path / "external"
    project.mkdir()
    external_odoo.mkdir()
    (external_odoo / "odoo-bin").write_text("", encoding="utf-8")
    requirements = external_odoo / "requirements.txt"
    requirements.write_text("odoo==1\n", encoding="utf-8")
    (project / "odoo-bin").symlink_to(external_odoo / "odoo-bin")

    result = discover_dependency_inputs(
        ProjectConfig(repository_root=project, odoo_bin=Path("odoo-bin")),
        project,
        project,
    )

    assert result == (str(requirements.resolve()),)


def test_discovery_rejects_external_odoo_requirements_symlink_escape(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    external_odoo = tmp_path / "external"
    outside = tmp_path / "outside-requirements.txt"
    project.mkdir()
    external_odoo.mkdir()
    (external_odoo / "odoo-bin").write_text("", encoding="utf-8")
    outside.write_text("private-package==1\n", encoding="utf-8")
    (external_odoo / "requirements.txt").symlink_to(outside)

    with pytest.raises(ConfigError, match="escapes its configured checkout boundary"):
        discover_dependency_inputs(
            ProjectConfig(repository_root=project, odoo_bin=external_odoo / "odoo-bin"),
            project,
            project,
        )


def test_discovery_rejects_external_addon_symlink_before_process_or_manifest_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    addons = tmp_path / "addons"
    module = addons / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}", encoding="utf-8")
    outside = tmp_path.parent / "outside-requirements.txt"
    outside.write_text("private-package==1\n", encoding="utf-8")
    (module / "requirements.txt").symlink_to(outside)
    manifest = tmp_path / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_bytes(b"[project]\n")
    before = manifest.read_bytes()

    config = ProjectConfig(repository_root=tmp_path, addon_roots=(Path("addons"),))

    process_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fail_if_process_started(*args: object, **kwargs: object) -> NoReturn:
        process_calls.append((args, kwargs))
        raise AssertionError("external discovered input started a process")

    for method in ("execute", "execute_with_deadline", "spawn"):
        monkeypatch.setattr(process_executor.SubprocessExecutor, method, fail_if_process_started)

    with pytest.raises(ConfigError, match="escapes its configured checkout boundary"):
        prepare_venv_command(config, python="python3", venv=tmp_path / ".venv")

    assert process_calls == []
    assert manifest.read_bytes() == before
    assert not (tmp_path / ".odcli" / "requirements.lock").exists()
    assert not (tmp_path / ".venv").exists()
