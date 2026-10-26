from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from odoo_instance_sdk.client import OdooClient
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
from odoo_instance_sdk.internal.proc import ProcessResult
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.git import GitResource
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.instance.runtime import _RuntimeBinding
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


def _bound_instance(
    worktree: Path, project_root: Path, *, owner_kind: str = "environment"
) -> OdooInstance:
    config = InstanceConfig(
        base_url="http://127.0.0.1:8069",
        default_cwd=worktree,
        start_config=StartConfig(addons_path=["addons"]),
    )
    instance = cast("OdooInstance", SimpleNamespace(config=config))
    instance.modules = ModuleResource(instance)
    instance._runtime_binding = _RuntimeBinding(
        owner_kind=owner_kind,  # type: ignore[arg-type]
        owner_id=f"{owner_kind}_test",
        project_id="project_test",
        repository_root=project_root,
        git_common_dir=project_root / ".git",
    )
    return instance


def _repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "tests")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "base")


def _ticket_project(root: Path, base_url: str = "https://tracker.test") -> None:
    manifest = root / ".odcli"
    manifest.mkdir()
    (manifest / "project.toml").write_text(
        f'[project]\ndefault_base_ref = "main"\nticket_link_enabled = true\nticket_base_url = "{base_url}"\n',
        encoding="utf-8",
    )


def test_passthrough_preserves_native_args_and_checkout_cwd(tmp_path: Path) -> None:
    _repo(tmp_path)

    command = GitResource(_instance(tmp_path)).passthrough_command(("status", "--short"))

    step = command.plan.process_steps[0]
    assert step.argv == ("git", "status", "--short")
    assert step.cwd == str(tmp_path)
    assert step.interactive is True
    assert step.environment_overrides == ()
    assert command.run().returncode == 0


def test_credentialed_passthrough_captures_output_instead_of_inheriting_stdio(
    tmp_path: Path,
) -> None:
    _repo(tmp_path)
    _git(
        tmp_path,
        "config",
        "alias.canary",
        "!printf security-canary; printf security-canary >&2",
    )

    command = GitResource(_instance(tmp_path)).passthrough_command(
        ("canary",),
        environment={"GITLAB_TOKEN": "security-canary"},
        secret_values=("security-canary",),
    )

    step = command.plan.process_steps[0]
    assert step.interactive is False
    assert "security-canary" not in repr(command.plan)
    result = command.run()
    assert result.returncode == 0
    assert "security-canary" not in result.stdout
    assert "security-canary" not in result.stderr
    assert "security-canary" not in repr(result)


def test_credentialed_passthrough_forbids_ssh_and_ext_protocols(tmp_path: Path) -> None:
    _repo(tmp_path)

    command = GitResource(_instance(tmp_path)).passthrough_command(
        ("fetch", "origin"),
        environment={"ODCLI_GITLAB_TOKEN_FILE": str(tmp_path / "secret")},
        secret_values=("secret",),
    )

    argv = command.plan.process_steps[0].argv
    assert argv[1:9] == (
        "-c",
        "credential.helper=",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "protocol.ssh.allow=never",
        "-c",
        "protocol.ext.allow=never",
    )


def test_credentialed_sync_disables_repository_fsmonitor_before_status(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.git"
    local = tmp_path / "repo"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")
    local.mkdir()
    _repo(local)
    _git(local, "remote", "add", "origin", str(remote))
    _git(local, "push", "-qu", "origin", "main")
    _git(local, "checkout", "-qb", "feature")

    marker = tmp_path / "fsmonitor-canary"
    fsmonitor = tmp_path / "malicious-fsmonitor"
    fsmonitor.write_text(
        f'#!/bin/sh\ncat "$ODCLI_GITLAB_TOKEN_FILE" > "{marker}"\n',
        encoding="utf-8",
    )
    fsmonitor.chmod(0o700)
    _git(local, "config", "core.fsmonitor", str(fsmonitor))
    channel = tmp_path / "token-channel"
    channel.write_text("secret-token\n", encoding="utf-8")

    command = GitResource(_instance(local)).sync_command(
        base="main",
        environment={"ODCLI_GITLAB_TOKEN_FILE": str(channel)},
        secret_values=("secret-token",),
    )
    status = next(step for step in command.plan.process_steps if step.step_id == "git.sync.status")
    assert "core.fsmonitor=false" in status.argv

    command.run()
    assert not marker.exists()


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


@pytest.mark.parametrize("tag", ["VIEW", "DEMO", "DATA", "ACC", "UI"])
def test_commit_policy_prefixes_create_and_validate_history(tmp_path: Path, tag: str) -> None:
    _repo(tmp_path)
    _git(tmp_path, "checkout", "-qb", "PROJ-123")
    module = tmp_path / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "models.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "addons/sale")
    resource = GitResource(_instance(tmp_path))

    result = resource.commit("adapt module", ticket="PROJ-123", tag=tag)

    assert result.returncode == 0, result.stderr
    assert _git(tmp_path, "log", "-1", "--format=%s").strip() == (
        f"[{tag}] sale: PROJ-123 adapt module"
    )
    assert resource.check(base="main").valid


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


def test_public_git_plan_and_output_redact_opaque_git_config_value(
    tmp_path: Path,
) -> None:
    _repo(tmp_path)
    canary = "opaque-config-value-canary"
    command = GitResource(_instance(tmp_path)).passthrough_command(
        ("canary",),
        environment={
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "alias.canary",
            "GIT_CONFIG_VALUE_0": f"!printf {canary}; printf {canary} >&2",
        },
        secret_values=("unrelated-credential",),
    )

    assert canary not in repr(command.plan)
    result = command.run()

    assert result.returncode == 0
    assert canary not in result.stdout
    assert canary not in result.stderr
    assert canary not in repr(result)


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
        f"[IMP] {tmp_path.name}: PROJ-123 change\\x0a\\x0ahttps://tracker.test/PROJ-123\\x0a\\x0a",
    )


def test_commit_preserves_ticket_link_with_trailer_hook(tmp_path: Path) -> None:
    _repo(tmp_path)
    _ticket_project(tmp_path)
    _git(tmp_path, "add", ".odcli/project.toml")
    _git(tmp_path, "commit", "-qm", "project config")
    _git(tmp_path, "checkout", "-qb", "feature")
    hook = tmp_path / ".git" / "hooks" / "prepare-commit-msg"
    hook.write_text(
        "#!/bin/sh\n"
        "git interpret-trailers --in-place --trailer "
        '"Co-authored-by: Test Agent <agent@example.test>" "$1"\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")

    result = GitResource(_instance(tmp_path)).commit("change", ticket="PROJ-123", tag="IMP")

    assert result.returncode == 0
    message = _git(tmp_path, "log", "-1", "--format=%B")
    assert "\n\nhttps://tracker.test/PROJ-123\n\nCo-authored-by:" in message
    assert "https: //" not in message
    assert "Co-authored-by: Test Agent <agent@example.test>" in message
    assert GitResource(_instance(tmp_path)).check(base="main").valid is True


def test_check_keeps_legacy_angle_ticket_links_valid(tmp_path: Path) -> None:
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
        "[IMP] sale: PROJ-123 change\n\n<https://tracker.test/PROJ-123>",
    )

    assert GitResource(_instance(tmp_path)).check().valid is True


def test_commit_in_worktree_without_manifest_uses_project_ticket_settings(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _repo(project_root)
    _ticket_project(project_root)

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _git(worktree, "init", "-q", "-b", "feature")
    _git(worktree, "config", "user.email", "tests@example.test")
    _git(worktree, "config", "user.name", "tests")
    (worktree / "change.txt").write_text("change\n", encoding="utf-8")
    _git(worktree, "add", "change.txt")

    command = GitResource(_bound_instance(worktree, project_root)).commit_command(
        description="change", ticket="PROJ-123", tag="IMP"
    )
    commit_step = next(step for step in command.plan.process_steps if step.step_id == "git.commit")
    assert commit_step.argv[-2:] == (
        "-m",
        "[IMP] worktree: PROJ-123 change\\x0a\\x0ahttps://tracker.test/PROJ-123\\x0a\\x0a",
    )


def _registered_native_checkout(tmp_path: Path) -> tuple[OdooInstance, Path, Path]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _repo(project_root)
    _ticket_project(project_root)
    _git(project_root, "add", ".odcli")
    _git(project_root, "commit", "-qm", "project config")
    _git(project_root, "remote", "add", "origin", "https://gitlab.example/team/repo.git")
    worktree = tmp_path / "native"
    parent = _git(project_root, "rev-parse", "HEAD^").strip()
    _git(project_root, "worktree", "add", "-qb", "task/PROJ-123", str(worktree), parent)
    (worktree / "addons" / "sale").mkdir(parents=True)
    (worktree / "addons" / "sale" / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    common = Path(_git(worktree, "rev-parse", "--git-common-dir").strip())
    common = (worktree / common).resolve() if not common.is_absolute() else common.resolve()
    instance = _bound_instance(worktree, project_root)
    instance._environment_id = "environment_test"

    class _Catalog:
        def get_project(self, project_id: str) -> dict[str, str]:
            assert project_id == "project_test"
            return {
                "registration_state": "registered",
                "repository_root": str(project_root.resolve()),
                "git_common_dir": str((project_root / ".git").resolve()),
            }

        def get_environment(self, environment_id: str) -> dict[str, str]:
            assert environment_id == "environment_test"
            return {
                "project_id": "project_test",
                "state": "active",
                "branch": "task/PROJ-123",
                "repository_root": str(project_root.resolve()),
                "git_common_dir": str((project_root / ".git").resolve()),
                "worktree_path": str(worktree.resolve()),
                "checkout_git_common_dir": str(common),
            }

        def get_catalog(self) -> _Catalog:
            return self

    instance._client = cast("OdooClient", _Catalog())
    return instance, project_root, worktree


def test_registered_native_checkout_uses_project_policy_and_verified_identity(
    tmp_path: Path,
) -> None:
    instance, _project, worktree = _registered_native_checkout(tmp_path)
    module_file = worktree / "addons" / "sale" / "models.py"
    module_file.write_text("value = 1\n", encoding="utf-8")
    _git(worktree, "add", "addons/sale")
    identity = {
        "GIT_AUTHOR_NAME": "Selected Member",
        "GIT_AUTHOR_EMAIL": "selected@example.test",
        "GIT_COMMITTER_NAME": "Selected Member",
        "GIT_COMMITTER_EMAIL": "selected@example.test",
    }

    with patch(
        "odoo_instance_sdk.internal.git_explicit.explicit_commit_environment",
        return_value=identity,
    ):
        command = GitResource(instance).commit_command(
            description="change model",
            ticket="PROJ-123",
            tag="VIEW",
            member_id="11111111-1111-4111-8111-111111111111",
        )
        commit_step = next(
            step for step in command.plan.process_steps if step.step_id == "git.commit"
        )
        assert {
            key: value for key, value in commit_step.environment_overrides if key in identity
        } == identity
        assert commit_step.argv[-1] == (
            "[VIEW] sale: PROJ-123 change model\\x0a\\x0ahttps://tracker.test/PROJ-123\\x0a\\x0a"
        )
        command.run()

    assert not (worktree / ".odcli" / "project.toml").exists()
    assert _git(worktree, "log", "-1", "--format=%an%x00%ae%x00%cn%x00%ce%x00%s").strip() == (
        "Selected Member\0selected@example.test\0Selected Member\0selected@example.test\0"
        "[VIEW] sale: PROJ-123 change model"
    )


def test_registered_native_checkout_origin_drift_fails_before_commit(tmp_path: Path) -> None:
    instance, project_root, worktree = _registered_native_checkout(tmp_path)
    change = worktree / "addons" / "sale" / "models.py"
    change.write_text("value = 2\n", encoding="utf-8")
    _git(worktree, "add", "addons/sale")
    head = _git(worktree, "rev-parse", "HEAD").strip()

    with patch(
        "odoo_instance_sdk.internal.git_explicit.explicit_commit_environment",
        return_value={"GIT_AUTHOR_NAME": "Selected", "GIT_AUTHOR_EMAIL": "s@example.test"},
    ):
        command = GitResource(instance).commit_command(
            description="change model",
            member_id="11111111-1111-4111-8111-111111111111",
        )
    _git(project_root, "remote", "set-url", "origin", "https://gitlab.example/other/repo.git")

    with pytest.raises(GitWorkflowError, match="identity changed"):
        command.run()
    assert _git(worktree, "rev-parse", "HEAD").strip() == head


def test_registered_native_checkout_rejects_foreign_catalog_worktree(tmp_path: Path) -> None:
    instance, _project_root, worktree = _registered_native_checkout(tmp_path)
    change = worktree / "addons" / "sale" / "models.py"
    change.write_text("value = 2\n", encoding="utf-8")
    _git(worktree, "add", "addons/sale")
    head = _git(worktree, "rev-parse", "HEAD").strip()
    catalog = instance._client
    original_get_environment = getattr(catalog, "get_environment")

    def foreign_environment(environment_id: str) -> dict[str, str]:
        row = cast("dict[str, str]", original_get_environment(environment_id))
        row["worktree_path"] = str(tmp_path / "foreign")
        return row

    setattr(catalog, "get_environment", foreign_environment)

    with (
        patch(
            "odoo_instance_sdk.internal.git_explicit.explicit_commit_environment",
            return_value={"GIT_AUTHOR_NAME": "Selected", "GIT_AUTHOR_EMAIL": "s@example.test"},
        ),
        pytest.raises(GitWorkflowError, match="stale or foreign"),
    ):
        GitResource(instance).commit_command(
            description="change model",
            member_id="11111111-1111-4111-8111-111111111111",
        )

    assert _git(worktree, "rev-parse", "HEAD").strip() == head


def test_commit_in_worktree_without_manifest_and_no_project_manifest_disables_link(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _git(worktree, "init", "-q", "-b", "feature")
    _git(worktree, "config", "user.email", "tests@example.test")
    _git(worktree, "config", "user.name", "tests")
    (worktree / "change.txt").write_text("change\n", encoding="utf-8")
    _git(worktree, "add", "change.txt")

    context = GitResource(_bound_instance(worktree, project_root)).commit_context(
        "change", ticket="PROJ-123", tag="IMP"
    )
    assert context.ticket_link is None
    assert context.message == "[IMP] worktree: PROJ-123 change"


def _project_bound_worktree(project_root: Path, _tmp_path: Path) -> tuple[Path, str]:
    return project_root, "project"


def _environment_bound_worktree(_project_root: Path, tmp_path: Path) -> tuple[Path, str]:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _git(worktree, "init", "-q", "-b", "feature")
    _git(worktree, "config", "user.email", "tests@example.test")
    _git(worktree, "config", "user.name", "tests")
    return worktree, "environment"


@pytest.mark.parametrize(
    ("worktree_factory", "base_url"),
    [
        pytest.param(_project_bound_worktree, "https://tracker.test", id="project-root"),
        pytest.param(
            _project_bound_worktree, "https://tracker.test/issues/", id="project-root-path"
        ),
        pytest.param(
            _environment_bound_worktree, "https://tracker.test", id="environment-worktree"
        ),
        pytest.param(
            _environment_bound_worktree, "https://tracker.test/issues/", id="environment-path"
        ),
    ],
)
def test_commit_ticket_settings_come_from_binding_repository_root(
    tmp_path: Path, worktree_factory: Callable[[Path, Path], tuple[Path, str]], base_url: str
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _repo(project_root)
    _ticket_project(project_root, base_url)

    worktree, owner_kind = worktree_factory(project_root, tmp_path)
    (worktree / "change.txt").write_text("change\n", encoding="utf-8")
    _git(worktree, "add", "change.txt")

    context = GitResource(
        _bound_instance(worktree, project_root, owner_kind=owner_kind)
    ).commit_context("change", ticket="PROJ-123", tag="IMP")
    expected_url = f"{base_url.rstrip('/')}/PROJ-123"
    assert context.ticket_link == expected_url
    assert context.message == (f"[IMP] {worktree.name}: PROJ-123 change\n\n{expected_url}\n\n")


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
