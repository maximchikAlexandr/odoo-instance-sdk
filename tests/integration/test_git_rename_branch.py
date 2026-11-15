from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import GitWorkflowError, StalePlanError
from odoo_instance_sdk.execution import ProcessStep
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    ProcessResult,
    StepObserver,
    SubprocessExecutor,
)
from odoo_instance_sdk.resources.git import GitResource
from odoo_instance_sdk.resources.instance import OdooInstance
from tests.unit.test_git_resource import _git, _repo


def _rename_fixture(root: Path) -> tuple[GitResource, list[dict[str, str]], Path]:
    bare = root.parent / f"{root.name}-origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    _repo(root)
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-qu", "origin", "main")
    _git(root, "checkout", "-qb", "feature")
    rows: list[dict[str, str]] = []
    catalog = SimpleNamespace(list_environments=lambda **_: rows)
    client = SimpleNamespace(get_catalog=lambda: catalog)
    instance = cast(
        "OdooInstance",
        SimpleNamespace(
            config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=root),
            _client=client,
        ),
    )
    return GitResource(instance), rows, bare


@pytest.mark.parametrize("source", ["local", "catalogue", "origin"])
def test_rename_allocates_above_maximum_from_each_source(tmp_path: Path, source: str) -> None:
    root = tmp_path / "work"
    root.mkdir()
    resource, rows, _ = _rename_fixture(root)
    if source == "local":
        _git(root, "branch", "ICODE-91")
    elif source == "catalogue":
        rows.append(
            {
                "repository_root": str(root),
                "git_common_dir": str(root / ".git"),
                "branch": "ICODE-91",
            }
        )
    else:
        _git(root, "branch", "ICODE-91")
        _git(root, "push", "origin", "ICODE-91")
        _git(root, "branch", "-D", "ICODE-91")

    result = resource.rename_branch("ICODE-91")

    assert (result.previous_branch, result.selected_branch, result.renamed) == (
        "feature",
        "ICODE-91_1",
        True,
    )
    assert _git(root, "branch", "--show-current").strip() == "ICODE-91_1"


def test_rename_uses_shared_maximum_and_preserves_dirty_index_and_upstream(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    resource, rows, _ = _rename_fixture(root)
    _git(root, "branch", "ICODE-91_2")
    rows.append(
        {
            "repository_root": str(root),
            "git_common_dir": str(root / ".git"),
            "branch": "ICODE-91_4",
        }
    )
    _git(root, "config", "branch.feature.remote", "origin")
    _git(root, "config", "branch.feature.merge", "refs/heads/main")
    (root / "staged.txt").write_text("staged", encoding="utf-8")
    _git(root, "add", "staged.txt")
    (root / "README.md").write_text("unstaged", encoding="utf-8")
    (root / "untracked.txt").write_text("untracked", encoding="utf-8")
    head = _git(root, "rev-parse", "HEAD").strip()
    index = _git(root, "write-tree").strip()
    status = _git(root, "status", "--porcelain=v1", "-z")

    result = resource.rename_branch("ICODE-91")

    assert result.selected_branch == "ICODE-91_5"
    assert _git(root, "rev-parse", "HEAD").strip() == head
    assert _git(root, "write-tree").strip() == index
    assert _git(root, "status", "--porcelain=v1", "-z") == status
    assert (root / "untracked.txt").read_text(encoding="utf-8") == "untracked"
    assert _git(root, "config", "--get", "branch.ICODE-91_5.remote").strip() == "origin"
    assert _git(root, "config", "--get", "branch.ICODE-91_5.merge").strip() == "refs/heads/main"
    assert _git(root, "ls-remote", "--heads", "origin", "ICODE-91_5") == ""


def test_rename_command_is_inspectable_idempotent_and_single_mutation(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    resource, _, _ = _rename_fixture(root)

    command = resource.rename_branch_command("ICODE-91")
    steps = command.plan.steps
    mutations = [step for step in steps if isinstance(step, ProcessStep) and step.mutating]
    assert len(mutations) == 1
    assert mutations[0].argv[-3:] == ("branch", "-m", "ICODE-91")
    process_steps = [step for step in steps if isinstance(step, ProcessStep)]
    assert all("fetch" not in step.argv and "push" not in step.argv for step in process_steps)
    assert command.run().renamed is True
    repeated = resource.rename_branch("ICODE-91")
    assert (repeated.previous_branch, repeated.selected_branch, repeated.renamed) == (
        "ICODE-91",
        "ICODE-91",
        False,
    )


@pytest.mark.parametrize("change", ["branch", "head", "local", "catalogue", "origin"])
def test_rename_revalidation_rejects_stale_plan(tmp_path: Path, change: str) -> None:
    root = tmp_path / "work"
    root.mkdir()
    resource, rows, _ = _rename_fixture(root)
    command = resource.rename_branch_command("ICODE-91")
    if change == "branch":
        _git(root, "checkout", "-qb", "other")
    elif change == "head":
        (root / "later.txt").write_text("later", encoding="utf-8")
        _git(root, "add", "later.txt")
        _git(root, "commit", "-qm", "later")
    elif change == "local":
        _git(root, "branch", "ICODE-91")
    elif change == "catalogue":
        rows.append(
            {
                "repository_root": str(root),
                "git_common_dir": str(root / ".git"),
                "branch": "ICODE-91",
            }
        )
    else:
        _git(root, "branch", "ICODE-91")
        _git(root, "push", "origin", "ICODE-91")
        _git(root, "branch", "-D", "ICODE-91")

    with pytest.raises(StalePlanError):
        command.run()


def test_rename_rejects_detached_head(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    resource, _, _ = _rename_fixture(root)
    _git(root, "checkout", "--detach", "HEAD")

    with pytest.raises(GitWorkflowError, match="attached current branch"):
        resource.rename_branch_command("ICODE-91")


@pytest.mark.parametrize("source", ["local", "catalogue", "origin"])
def test_rename_fails_closed_when_collision_evidence_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    resource, _rows, _ = _rename_fixture(root)
    current_branch = _git(root, "branch", "--show-current").strip()
    head = _git(root, "rev-parse", "HEAD").strip()

    if source == "local":
        execute = SubprocessExecutor.execute

        def fail_local_probe(
            executor: SubprocessExecutor,
            step: PreparedProcess,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> ProcessResult:
            result = execute(executor, step, observer=observer, observe_output=observe_output)
            if step.step_id == "git.rename.plan.local-heads":
                return replace(result, returncode=1, stdout="", stderr="unavailable")
            return result

        monkeypatch.setattr(SubprocessExecutor, "execute", fail_local_probe)
        message = "Git planning probe failed: git.rename.plan.local-heads"
    elif source == "catalogue":
        from odoo_instance_sdk.resources import git_rename

        monkeypatch.setattr(
            git_rename,
            "catalogue_branch_names",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        message = "Git branch catalogue evidence is unavailable"
    else:
        _git(root, "remote", "remove", "origin")
        message = "Git planning probe failed: git.rename.plan.origin-heads"

    with pytest.raises(GitWorkflowError, match=message):
        resource.rename_branch("ICODE-91")

    assert _git(root, "branch", "--show-current").strip() == current_branch
    assert _git(root, "rev-parse", "HEAD").strip() == head
    assert not (root / ".git" / "refs" / "heads" / "ICODE-91").exists()


@pytest.mark.parametrize(
    ("step_id", "message"),
    [
        ("git.rename.revalidate.local-heads", "Git branch collision evidence is unavailable"),
        ("git.rename.revalidate.origin-heads", "Git branch collision evidence is unavailable"),
    ],
)
def test_rename_fails_closed_when_execution_collision_evidence_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    step_id: str,
    message: str,
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    resource, _rows, _ = _rename_fixture(root)
    current_branch = _git(root, "branch", "--show-current").strip()
    head = _git(root, "rev-parse", "HEAD").strip()
    execute = SubprocessExecutor.execute

    def fail_evidence(
        executor: SubprocessExecutor,
        step: PreparedProcess,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResult:
        result = execute(
            executor,
            step,
            observer=observer,
            observe_output=observe_output,
        )
        if step.step_id == step_id:
            return replace(result, returncode=1, stdout="", stderr="unavailable")
        return result

    monkeypatch.setattr(SubprocessExecutor, "execute", fail_evidence)
    with pytest.raises(GitWorkflowError, match=message):
        resource.rename_branch("ICODE-91")

    assert _git(root, "branch", "--show-current").strip() == current_branch
    assert _git(root, "rev-parse", "HEAD").strip() == head


def test_rename_fails_closed_when_execution_catalogue_evidence_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    resource, _rows, _ = _rename_fixture(root)
    current_branch = _git(root, "branch", "--show-current").strip()
    head = _git(root, "rev-parse", "HEAD").strip()
    command = resource.rename_branch_command("ICODE-91")

    from odoo_instance_sdk.internal import ticket_branch_allocation

    def unavailable_catalogue(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        raise RuntimeError("catalogue unavailable during execution")

    monkeypatch.setattr(ticket_branch_allocation, "catalogue_branch_names", unavailable_catalogue)
    with pytest.raises(GitWorkflowError, match="Git branch catalogue evidence is unavailable"):
        command.run()

    assert _git(root, "branch", "--show-current").strip() == current_branch
    assert _git(root, "rev-parse", "HEAD").strip() == head
    assert not (root / ".git" / "refs" / "heads" / "ICODE-91").exists()
