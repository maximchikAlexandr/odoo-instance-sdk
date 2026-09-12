from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    GitAbsorbNotFoundError,
    GitCheckFailedError,
    GitScopeError,
    GitSyncError,
    GitWorkflowError,
    PlanValidationError,
)
from odoo_instance_sdk.internal.executables import OptionalExecutable
from odoo_instance_sdk.internal.proc.executor import ProcessResult
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.git import GitResource
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.module import ModuleResource


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _instance(root: Path) -> OdooInstance:
    config = InstanceConfig(
        base_url="http://127.0.0.1:8069",
        default_cwd=root,
        start_config=StartConfig(addons_path=["addons"]),
    )
    instance = cast("OdooInstance", SimpleNamespace(config=config))
    instance.modules = ModuleResource(instance)
    return instance


def _repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "tests")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "base")


def _ticket_project(root: Path) -> None:
    manifest = root / ".odcli"
    manifest.mkdir()
    (manifest / "project.toml").write_text(
        '[project]\ndefault_base_ref = "main"\nticket_link_enabled = true\nticket_base_url = "https://tracker.test"\n',
        encoding="utf-8",
    )


def test_commit_context_resolves_module_ticket_and_url(tmp_path: Path) -> None:
    _repo(tmp_path)
    module = tmp_path / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "models.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "addons/sale")

    resource = GitResource(_instance(tmp_path))
    context = resource.commit_context("add a model", ticket="PROJ-123", tag="IMP")

    assert context.module == "sale"
    assert context.ticket_link is None
    assert context.message == "[IMP] sale: PROJ-123 add a model"
    assert context.staged_paths == ("addons/sale/__manifest__.py", "addons/sale/models.py")


def test_commit_rejects_empty_description_and_empty_index(tmp_path: Path) -> None:
    _repo(tmp_path)
    resource = GitResource(_instance(tmp_path))

    with pytest.raises(PlanValidationError):
        resource.commit_context(" ")
    with pytest.raises(GitScopeError, match="staged"):
        resource.commit_context("nothing")


def test_commit_runs_only_the_captured_staged_scope(tmp_path: Path) -> None:
    _repo(tmp_path)
    module = tmp_path / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "models.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "addons/sale")

    resource = GitResource(_instance(tmp_path))
    result = resource.commit("add a model", ticket="PROJ-123", tag="IMP")

    assert result.returncode == 0, result.stderr
    assert _git(tmp_path, "log", "-1", "--format=%s").strip() == "[IMP] sale: PROJ-123 add a model"


def test_commit_plan_captures_context_and_rejects_hook_failure(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    hook = tmp_path / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\necho rejected >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    head = _git(tmp_path, "rev-parse", "HEAD").strip()

    resource = GitResource(_instance(tmp_path))
    command = resource.commit_command(description="change", ticket="PROJ-123", tag="IMP")

    assert {step.step_id for step in command.plan.process_steps} >= {
        "git.commit.staged",
        "git.commit.branch",
        "git.commit.status",
        "git.commit.verify-staged",
        "git.commit",
    }
    with pytest.raises(GitWorkflowError, match="hook or commit failed"):
        command.run()
    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head


def test_commit_plan_previews_complete_configured_message(tmp_path: Path) -> None:
    _repo(tmp_path)
    _ticket_project(tmp_path)
    change = tmp_path / "change.txt"
    change.write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")

    command = GitResource(_instance(tmp_path)).commit_command(
        description="change", ticket="PROJ-123", tag="IMP"
    )
    commit_step = next(step for step in command.plan.process_steps if step.step_id == "git.commit")
    assert commit_step.argv[-2:] == (
        "-m",
        f"[IMP] {tmp_path.name}: PROJ-123 change\\x0a\\x0ahttps://tracker.test/PROJ-123",
    )


def test_commit_omitted_tag_freezes_add_inference_in_plan_and_execution(tmp_path: Path) -> None:
    _repo(tmp_path)
    module = tmp_path / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "models.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "addons/sale")

    resource = GitResource(_instance(tmp_path))
    command = resource.commit_command(description="add module", ticket="PROJ-123")
    commit_step = next(step for step in command.plan.process_steps if step.step_id == "git.commit")

    assert "[ADD] sale: PROJ-123 add module" in commit_step.argv[-1]
    command.run()
    assert _git(tmp_path, "log", "-1", "--format=%s").strip() == "[ADD] sale: PROJ-123 add module"


@pytest.mark.parametrize(
    ("paths", "expected"),
    [(("docs/guide.md",), "DOC"), ((".github/workflows/check.yml",), "CI")],
)
def test_commit_omitted_tag_uses_outside_scope_precedence(
    tmp_path: Path, paths: tuple[str, ...], expected: str
) -> None:
    _repo(tmp_path)
    for raw in paths:
        path = tmp_path / raw
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", *paths)
    _git(tmp_path, "commit", "-qm", "outside base")
    for raw in paths:
        (tmp_path / raw).write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", *paths)

    context = GitResource(_instance(tmp_path)).commit_context("outside change", ticket="PROJ-123")

    assert context.tag == expected


@pytest.mark.parametrize(
    ("case", "paths", "expected"),
    [
        ("delete", ("addons/sale/models/old.py",), "DEL"),
        ("port", ("addons/sale/migrations/1.0.py",), "PORT"),
        ("i18n", ("addons/sale/i18n/sale.pot",), "I18N"),
        ("ui", ("addons/sale/static/src/main.js",), "UI"),
        ("test", ("addons/sale/tests/test_sale.py",), "TEST"),
        ("mixed-module", ("addons/sale/models/a.py", "addons/sale/models/b.py"), "IMP"),
        ("doc-over-ci", ("docs/guide.md", ".github/workflows/check.yml"), "DOC"),
    ],
)
def test_commit_context_uses_deterministic_semantic_tag_matrix(
    tmp_path: Path, case: str, paths: tuple[str, ...], expected: str
) -> None:
    _repo(tmp_path)
    module = tmp_path / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    for raw in paths:
        path = tmp_path / raw
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("before\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "baseline")
    if case == "delete":
        _git(tmp_path, "rm", paths[0])
    else:
        for raw in paths:
            (tmp_path / raw).write_text("after\n", encoding="utf-8")
        _git(tmp_path, "add", *paths)

    context = GitResource(_instance(tmp_path)).commit_context("semantic change")

    assert context.tag == expected


def test_commit_context_preserves_rename_scope_and_ticket_precedence(tmp_path: Path) -> None:
    _repo(tmp_path)
    module = tmp_path / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "old.py").write_text("before\n", encoding="utf-8")
    _git(tmp_path, "add", "addons/sale")
    _git(tmp_path, "commit", "-qm", "baseline")
    _git(tmp_path, "mv", "addons/sale/old.py", "addons/sale/new.py")
    branch = "feature-PROJ-123"
    _git(tmp_path, "checkout", "-qb", branch)

    resource = GitResource(_instance(tmp_path))
    inferred = resource.commit_context("rename")
    explicit = resource.commit_context("rename", ticket="EXPLICIT-9")

    assert inferred.ticket == "PROJ-123"
    assert explicit.ticket == "EXPLICIT-9"
    assert inferred.staged_paths == ("addons/sale/old.py", "addons/sale/new.py")
    assert inferred.module == "sale"


def test_commit_rejects_mixed_module_and_repository_scope(tmp_path: Path) -> None:
    _repo(tmp_path)
    module = tmp_path / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "models.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("mixed\n", encoding="utf-8")
    _git(tmp_path, "add", "addons/sale", "README.md")

    with pytest.raises(GitScopeError, match="mix"):
        GitResource(_instance(tmp_path)).commit_context("mixed", ticket="PROJ-123")


def test_commit_rejects_replaced_and_restaged_index_content(tmp_path: Path) -> None:
    _repo(tmp_path)
    change = tmp_path / "change.txt"
    change.write_text("original\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    resource = GitResource(_instance(tmp_path))
    context = resource.commit_context("change", ticket="PROJ-123", tag="IMP")
    head = _git(tmp_path, "rev-parse", "HEAD").strip()
    change.write_text("replacement\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")

    with pytest.raises(GitWorkflowError, match="staged changes changed"):
        resource.commit_command(context).run()
    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head


def test_check_reports_malformed_history_and_dirty_state(tmp_path: Path) -> None:
    _repo(tmp_path)
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    _git(tmp_path, "commit", "-qm", "fixup! malformed")
    (tmp_path / "unstaged.txt").write_text("dirty\n", encoding="utf-8")

    result = GitResource(_instance(tmp_path)).check(base="main")

    assert result.valid is False
    assert {issue.code for issue in result.issues} >= {"message_invalid", "pending_fixup", "dirty"}


def test_check_rejects_unknown_prefix_and_accepts_multiple_outside_top_levels(
    tmp_path: Path,
) -> None:
    _repo(tmp_path)
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md", "docs/guide.md")
    _git(tmp_path, "commit", "-qm", "[BOGUS] repo: accepted today")

    result = GitResource(_instance(tmp_path)).check(base="main")

    assert result.valid is False
    assert any(issue.code == "message_invalid" for issue in result.issues)


def test_check_requires_explicit_or_configured_base(tmp_path: Path) -> None:
    _repo(tmp_path)

    with pytest.raises(GitCheckFailedError, match="no Git base configured"):
        GitResource(_instance(tmp_path)).check()


def test_check_validates_the_complete_one_or_two_paragraph_message(tmp_path: Path) -> None:
    _repo(tmp_path)
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    _git(
        tmp_path,
        "commit",
        "-qm",
        f"[IMP] {tmp_path.name}: PROJ-123 change\n\nhttps://tracker.test/PROJ-123",
    )

    result = GitResource(_instance(tmp_path)).check(base="main")

    assert result.valid is True


def test_check_accepts_module_history_with_matching_ticket_link(tmp_path: Path) -> None:
    _repo(tmp_path)
    _ticket_project(tmp_path)
    _git(tmp_path, "add", ".odcli/project.toml")
    _git(tmp_path, "commit", "-qm", "project config")
    _git(tmp_path, "checkout", "-qb", "feature")
    module = tmp_path / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "models.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "addons/sale")
    _git(
        tmp_path,
        "commit",
        "-qm",
        "[IMP] sale: PROJ-123 change\n\nhttps://tracker.test/PROJ-123",
    )
    result = GitResource(_instance(tmp_path)).check()

    assert result.valid is True


def test_check_rejects_option_like_base_before_git_log(tmp_path: Path) -> None:
    _repo(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD").strip()

    with pytest.raises(GitCheckFailedError, match="must not start"):
        GitResource(_instance(tmp_path)).check(base="--output=HEAD")

    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("[IMP] other: PROJ-123 change", "scope_ambiguous"),
        ("[IMP] sale: change", "message_invalid"),
        ("[IMP] sale: PROJ-123 change\n\nhttps://tracker.test/OTHER-999", "message_invalid"),
        ("[IMP] sale: PROJ-123 change\n\nhttps://tracker.test/not-a-ticket", "message_invalid"),
    ],
)
def test_check_rejects_module_or_ticket_history_mismatches(
    tmp_path: Path, subject: str, expected: str
) -> None:
    _repo(tmp_path)
    _ticket_project(tmp_path)
    _git(tmp_path, "checkout", "-qb", "feature")
    module = tmp_path / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "models.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "addons/sale")
    _git(tmp_path, "commit", "-qm", subject)

    result = GitResource(_instance(tmp_path)).check()

    assert result.valid is False
    assert any(issue.code == expected for issue in result.issues)


def test_absorb_nonzero_is_typed_failure_and_collects_both_unmapped_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo(tmp_path)
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    tool = tmp_path / "git-absorb"
    tool.write_text(
        "#!/bin/sh\necho 'unmapped stdout'\necho 'not mapped stderr' >&2\nexit 1\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git.resolve_optional_executable",
        lambda name: OptionalExecutable(name=name, path=str(tool)),
    )

    with pytest.raises(GitWorkflowError, match="git-absorb failed"):
        GitResource(_instance(tmp_path)).absorb(base="main")


def test_absorb_resolves_a_late_installed_executable_without_reinstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo(tmp_path)
    tool = tmp_path / "git-absorb"
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o755)
    capability = "odoo_instance_sdk.resources.git.resolve_optional_executable"
    monkeypatch.setattr(capability, lambda name: OptionalExecutable(name=name, path=None))
    with pytest.raises(GitAbsorbNotFoundError, match="git-absorb was not found"):
        GitResource(_instance(tmp_path)).absorb(base="main")
    monkeypatch.setattr(
        capability, lambda name: OptionalExecutable(name=name, path=str(tool.resolve()))
    )

    result = GitResource(_instance(tmp_path)).absorb(base="main", dry_run=True)

    assert result.executable == str(tool.resolve())


def test_absorb_reports_unmapped_hunks_from_stdout_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo(tmp_path)
    tool = tmp_path / "git-absorb"
    args_file = tmp_path / "absorb-args"
    tool.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {args_file}\necho 'unmapped stdout'\necho 'not mapped stderr' >&2\nexit 0\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.git.resolve_optional_executable",
        lambda name: OptionalExecutable(name=name, path=str(tool)),
    )

    result = GitResource(_instance(tmp_path)).absorb(base="main", dry_run=True, and_rebase=True)

    assert result.returncode == 0
    assert result.unmapped_hunks == ("unmapped stdout", "not mapped stderr")
    assert "--and-rebase" in args_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("lease_returncode", [0, 1])
def test_sync_executes_exact_fetched_lease_without_retry(  # noqa: C901
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lease_returncode: int
) -> None:
    calls: list[tuple[str, ...]] = []

    class FakeExecutor:
        def execute(self, step: object, **_: object) -> ProcessResult:
            argv = tuple(getattr(step, "argv"))
            step_id = getattr(step, "step_id")
            calls.append(argv)
            if step_id == "git.sync.status":
                stdout, returncode = "", 0
            elif step_id == "git.sync.branch":
                stdout, returncode = "feature\n", 0
            elif step_id == "git.sync.upstream":
                stdout, returncode = "origin/feature\n", 0
            elif step_id == "git.sync.remote":
                stdout, returncode = "git@host:repo.git\n", 0
            elif step_id == "git.sync.authoritative-remote-sha":
                stdout, returncode = (
                    "0123456789abcdef0123456789abcdef01234567\trefs/heads/feature\n",
                    0,
                )
            elif step_id == "git.sync.fetched-sha":
                stdout, returncode = "0123456789abcdef0123456789abcdef01234567\n", 0
            elif step_id == "git.sync.remote-ancestry":
                stdout, returncode = "", 1
            elif step_id == "git.sync.push-lease":
                stdout, returncode = "", lease_returncode
            else:
                stdout, returncode = "", 0
            return ProcessResult(argv, returncode, stdout, "", 0.0, str(tmp_path), ())

        def spawn(self, *_: object, **__: object) -> object:
            raise AssertionError("sync does not spawn processes")

    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", FakeExecutor)
    resource = GitResource(_instance(tmp_path))

    if lease_returncode:
        with pytest.raises(GitSyncError, match="stale lease"):
            resource.sync(base="main", push=True)
    else:
        result = resource.sync(base="main", push=True)
        assert result.pushed is True
    lease_calls = [argv for argv in calls if "--force-with-lease" in " ".join(argv)]
    assert len(lease_calls) == 1
    assert (
        "--force-with-lease=refs/heads/feature:0123456789abcdef0123456789abcdef01234567"
        in lease_calls[0]
    )
