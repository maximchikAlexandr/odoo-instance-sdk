from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    GitLabCredentialError,
    GitRepositoryError,
    GitSyncError,
    GitWorkflowError,
    PlanValidationError,
)
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal import git_frontier, git_repository, proc
from odoo_instance_sdk.internal.git_sync import AuthoritativeProbeCapture
from odoo_instance_sdk.internal.gitlab_credentials import ensure_credential_directory
from odoo_instance_sdk.models import (
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    GitLabIdentity,
    StartConfig,
)
from odoo_instance_sdk.resources.git import GitResource
from odoo_instance_sdk.resources.git_generic import _planned_remote_sha, _remote_head
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.module import ModuleResource

MEMBER = "11111111-1111-4111-8111-111111111111"
HOST = "gitlab.example"
OTHER_HOST = "gitlab.other.example"
THIRD_HOST = "gitlab.third.example"


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


def _repo(root: Path, *, origin: str = "https://gitlab.example/team/repo") -> None:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "feature")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "tests")
    _git(root, "remote", "add", "origin", origin)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "base")


def _instance(root: Path) -> OdooInstance:
    config = InstanceConfig(
        base_url="http://127.0.0.1:8069",
        default_cwd=root,
        start_config=StartConfig(addons_path=["addons"]),
    )
    instance = cast("OdooInstance", SimpleNamespace(config=config))
    instance.modules = ModuleResource(instance)
    return instance


def _credential(
    host: str = HOST, *, login: str = "alice", token: str = "token"
) -> tuple[GitLabCredentialContext, GitLabIdentity]:
    credential = GitLabCredentialContext(
        identity=GitLabCredentialIdentity(
            user_id=MEMBER,
            host=host,
            login=login,
            token_key="ODCLI_GITLAB_TOKEN_TEST",
        ),
        _token=token,
    )
    identity = GitLabIdentity(
        user_id=7, username=login, name="Alice Example", email="alice@example.test", host=host
    )
    return credential, identity


def _mapping(root: Path, hosts: tuple[str, ...]) -> None:
    directory = ensure_credential_directory(root)
    entries = []
    values = []
    for index, host in enumerate(hosts):
        key = f"ODCLI_GITLAB_TOKEN_{index}"
        entries.append(
            "[[mappings]]\n"
            f'user_id = "{MEMBER}"\n'
            f'host = "{host}"\n'
            f'login = "user-{index}"\n'
            f'token_key = "{key}"\n'
        )
        values.append(f"{key}=token-{index}")
    (directory / "gitlab-credentials.toml").write_text("\n".join(entries), encoding="utf-8")
    (directory / "gitlab-credentials.toml").chmod(0o600)
    (directory / ".env").write_text("\n".join(values) + "\n", encoding="utf-8")
    (directory / ".env").chmod(0o600)


def _identity_transport(host: str, _token: str) -> dict[str, JsonValue]:
    usernames = {HOST: "user-0", OTHER_HOST: "user-1", THIRD_HOST: "user-2"}
    return {
        "id": 7,
        "username": usernames[host],
        "name": "Selected Member",
        "commit_email": "selected@example.test",
    }


def test_real_runtime_probe_parses_before_redaction_and_cleans_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "abcdef0123456789abcdef0123456789abcdef01"
    credential, _identity = _credential(token=token)
    options = git_frontier.environment((credential,), None)
    channels: list[Path] = []
    original_writer = cast(
        "Callable[[Sequence[GitLabCredentialContext]], str]",
        getattr(git_frontier, "write_askpass_channel"),
    )

    def recording_writer(values: Sequence[GitLabCredentialContext]) -> str:
        channel = Path(original_writer(values))
        channels.append(channel)
        return str(channel)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_frontier.write_askpass_channel", recording_writer
    )
    authoritative_capture = AuthoritativeProbeCapture()
    payload = f"{token}\trefs/heads/feature\n"
    step = proc.PreparedStep(
        step_id="git.sync.authoritative-remote-sha",
        argv=(sys.executable, "-c", f"import sys;sys.stdout.write({payload!r})"),
        environment=tuple(sorted(options["environment"].items())),
        environment_policy="explicit",
        runtime_environment_factory=options["runtime_environment"],
        secret_values=tuple(options["secrets"]),
        private_stdout_consumer=authoritative_capture.capture,
    )
    result = proc.SubprocessExecutor().execute(step)

    assert result.returncode == 0
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    assert "<redacted>" in stdout
    assert token not in stdout
    assert _planned_remote_sha(result, authoritative_capture) == token
    assert _remote_head(payload) == token
    assert _remote_head("not-a-ref\n") is None
    assert channels and all(not channel.exists() for channel in channels)


def test_runtime_non_probe_does_not_retain_unredacted_stdout() -> None:
    token = "runtime-non-probe-token"
    credential, _identity = _credential(token=token)
    options = git_frontier.environment((credential,), None)
    events: list[proc.StepEvent] = []
    payload = f"{token}\n"
    step = proc.PreparedStep(
        step_id="git.fetch",
        argv=(
            sys.executable,
            "-c",
            f"import sys;sys.stdout.write({payload!r});sys.stderr.write({payload!r});sys.exit(1)",
        ),
        environment=tuple(sorted(options["environment"].items())),
        environment_policy="explicit",
        runtime_environment_factory=options["runtime_environment"],
        secret_values=tuple(options["secrets"]),
    )
    result = proc.SubprocessExecutor().execute(step, observer=events.append, observe_output=True)
    serialized = asdict(result)

    assert result.returncode == 1
    assert token not in repr(result)
    assert token not in repr(serialized)
    assert token not in repr([asdict(event) for event in events])


def _add_local_submodule(
    parent: Path, source: Path, path: str, url: str, *, logical_name: str | None = None
) -> None:
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(source),
        path,
    )
    _git(parent, "config", "-f", ".gitmodules", f"submodule.{path}.url", url)
    if logical_name is not None:
        _git(
            parent,
            "config",
            "-f",
            ".gitmodules",
            "--rename-section",
            f"submodule.{path}",
            f"submodule.{logical_name}",
        )
    _git(parent, "add", ".gitmodules", path)
    _git(parent, "commit", "-qm", f"add {path}")


def _recursive_sources(
    tmp_path: Path, *, hostile: bool = False
) -> tuple[Path, Path, Path, Path, Path]:
    leaf = tmp_path / "leaf-source"
    _repo(leaf, origin="https://gitlab.example/team/leaf")

    deepest = tmp_path / "deepest-source"
    _repo(deepest, origin="https://gitlab.third.example/team/deepest")
    ultimate = tmp_path / "ultimate-source"
    _repo(ultimate, origin="https://gitlab.third.example/team/ultimate")
    _add_local_submodule(
        deepest,
        ultimate,
        "ultimate",
        "https://gitlab.third.example/team/ultimate",
    )
    _add_local_submodule(
        leaf,
        deepest,
        "vendor/lib",
        "https://gitlab.third.example/team/deepest",
        logical_name="nested-deep",
    )
    if hostile:
        _git(
            leaf,
            "config",
            "-f",
            ".gitmodules",
            "url.https://evil.example/.insteadOf",
            "https://gitlab.third.example/",
        )
        _git(leaf, "add", ".gitmodules")
        _git(leaf, "commit", "-qm", "hostile deepest policy")

    child = tmp_path / "child-source"
    _repo(child, origin="https://gitlab.other.example/team/child")
    _add_local_submodule(
        child,
        leaf,
        "leaf",
        "https://gitlab.example/team/leaf",
    )
    root = tmp_path / "root-source"
    _repo(root, origin="https://gitlab.example/team/root")
    _add_local_submodule(
        root,
        child,
        "child",
        "https://gitlab.other.example/team/child",
    )
    return root, child, leaf, deepest, ultimate


def _two_level_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a small real-Git cross-host root -> child -> leaf topology."""
    leaf = tmp_path / "two-level-leaf-source"
    _repo(leaf, origin="https://gitlab.example/team/leaf")
    child = tmp_path / "two-level-child-source"
    _repo(child, origin="https://gitlab.other.example/team/child")
    _add_local_submodule(
        child,
        leaf,
        "leaf",
        "https://gitlab.example/team/leaf",
    )
    root = tmp_path / "two-level-root-source"
    _repo(root, origin="https://gitlab.example/team/root")
    _add_local_submodule(
        root,
        child,
        "child",
        "https://gitlab.other.example/team/child",
    )
    return root, child, leaf


def _real_clone_executor(
    root_source: Path,
    child_source: Path,
    leaf_source: Path,
    deepest_source: Path | None = None,
    ultimate_source: Path | None = None,
) -> tuple[type[object], list[str], list[dict[str, str]]]:
    urls = {
        "https://gitlab.other.example/team/child": child_source,
        "https://gitlab.example/team/leaf": leaf_source,
    }
    if deepest_source is not None and ultimate_source is not None:
        urls.update(
            {
                "https://gitlab.third.example/team/deepest": deepest_source,
                "https://gitlab.third.example/team/ultimate": ultimate_source,
            }
        )
    calls: list[str] = []
    environments: list[dict[str, str]] = []

    class RealExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            argv = tuple(getattr(step, "argv"))
            if step_id == "git.clone":
                destination = Path(argv[-1])
                result = subprocess.run(
                    ["git", "clone", str(root_source), str(destination)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    _git(
                        destination,
                        "remote",
                        "set-url",
                        "origin",
                        "https://gitlab.example/team/root",
                    )
            else:
                factory = getattr(step, "runtime_environment_factory", None)
                if callable(factory):
                    runtime_environment, cleanup, _secrets = factory()
                else:
                    runtime_environment = tuple(getattr(step, "environment_overrides"))

                    def cleanup() -> None:
                        return None

                try:
                    runtime_values = dict(runtime_environment)
                    environments.append(runtime_values)
                    command = ["git", "-c", "protocol.file.allow=always"]
                    for url, source in urls.items():
                        command.extend(["-c", f"url.{source.as_uri()}.insteadOf={url}"])
                    command_args = list(argv[1:])
                    for foreach_index in reversed(
                        [index for index, value in enumerate(command_args) if value == "foreach"]
                    ):
                        inner_git = foreach_index + 1
                        for url, source in reversed(tuple(urls.items())):
                            command_args[inner_git + 1 : inner_git + 1] = [
                                "-c",
                                f"url.{source.as_uri()}.insteadOf={url}",
                            ]
                    command.extend(command_args)
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        check=False,
                        env=os.environ | runtime_values,
                    )
                    assert result.returncode == 0, (step_id, command, result.stderr)
                finally:
                    cleanup()
            return proc.ProcessResult(
                argv=argv,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    return RealExecutor, calls, environments


def test_absent_clone_plan_is_dry_and_redacts_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential, identity = _credential()
    destination = tmp_path / "new-repository"

    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo.git",
        destination,
        member_id=MEMBER,
        credential=credential,
        identity=identity,
    )

    assert not destination.exists()
    clone = next(step for step in command.plan.process_steps if step.step_id == "git.clone")
    assert clone.argv[-2] == "https://<redacted>/team/repo"
    assert "token" not in repr(command.plan)

    def fail_if_planned(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("askpass channel must be created only during child execution")

    monkeypatch.setattr(git_frontier, "write_askpass_channel", fail_if_planned)
    recursive = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo.git",
        tmp_path / "recursive-repository",
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
    )

    assert not (tmp_path / "recursive-repository").exists()
    assert "ODCLI_GITLAB_ASKPASS_FILE" not in repr(recursive.plan)


def test_existing_clone_reuses_branch_without_reclone_or_effect(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential()
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo",
        root,
        member_id=MEMBER,
        credential=credential,
        identity=identity,
    )

    assert not any(step.step_id == "git.clone" for step in command.plan.process_steps)
    result = command.run()
    assert result.reused is True
    assert _git(root, "branch", "--show-current").strip() == "feature"


def test_recursive_clone_rejects_hostile_gitmodules_before_update(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".gitmodules").write_text(
        '[submodule "vendor-lib"]\n\tpath = vendor/lib\n\turl = https://gitlab.example/team/evil\n\tupdate = !touch /tmp/should-not-run\n',
        encoding="utf-8",
    )
    credential, identity = _credential()

    with pytest.raises(GitRepositoryError, match="unsafe"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/repo",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
        )

    (root / ".gitmodules").write_text(
        '[submodule "self"]\n\tpath = .\n\turl = https://gitlab.example/team/self\n',
        encoding="utf-8",
    )
    with pytest.raises(GitRepositoryError, match="escapes"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/repo",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
        )

    (root / ".gitmodules").write_text(
        '[submodule "cycle"]\n\tpath = cycle\n\turl = https://gitlab.example/team/cycle\n',
        encoding="utf-8",
    )
    (root / "cycle").symlink_to(root, target_is_directory=True)
    with pytest.raises(PlanValidationError, match="repeats"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/repo",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
        )


def test_recursive_clone_requires_mapping_for_each_cross_host_submodule(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".gitmodules").write_text(
        '[submodule "other"]\n\tpath = other\n\turl = https://gitlab.other.example/team/other\n',
        encoding="utf-8",
    )
    credential, identity = _credential()

    with pytest.raises(GitLabCredentialError, match="unavailable"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/repo",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
        )

    _mapping(tmp_path, (HOST, OTHER_HOST))
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/repo",
        root,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    update = next(
        step for step in command.plan.process_steps if step.step_id == "git.clone.submodule-update"
    )
    assert "--recursive" not in update.argv
    assert not any(
        step.step_id.startswith("git.clone.submodule-update.dynamic")
        for step in command.plan.process_steps
    )
    assert "token-1" not in repr(command.plan)


def test_absent_recursive_clone_uses_real_git_for_two_level_cross_host_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_source, child_source, leaf_source = _two_level_sources(tmp_path)
    credential, identity = _credential()
    _mapping(tmp_path, (HOST, OTHER_HOST, THIRD_HOST))
    executor, calls, environments = _real_clone_executor(root_source, child_source, leaf_source)
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)

    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/root",
        tmp_path / "checkout",
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    result = command.run()

    checkout = tmp_path / "checkout"
    assert result.initialized_submodules == ("child",)
    assert (checkout / "child" / "leaf" / ".git").exists()
    assert _git(checkout, "submodule", "status", "--recursive").count("-") == 0
    assert calls == [
        "git.clone",
        "git.clone.submodule-status",
        "git.clone.submodule-update",
        "git.clone.submodule-status.dynamic-0",
        "git.clone.submodule-update.dynamic-0",
        "git.clone.submodule-status.dynamic-1",
    ]
    planned = {
        step.step_id: step
        for step in command.plan.process_steps
        if step.step_id.startswith("git.clone.submodule-update")
    }
    assert tuple(planned) == ("git.clone.submodule-update",)
    assert "git.clone.submodule-update.depth-1" not in calls
    for environment in environments:
        assert "token-1" not in environment.values()
        assert "token-2" not in environment.values()
    update_environments = environments[1::2]
    assert all("token" not in environment.values() for environment in update_environments)
    assert all("GIT_ASKPASS" in environment for environment in update_environments)
    assert all("token-0" not in environment.values() for environment in update_environments[1:])


def test_absent_recursive_clone_rejects_nested_hostile_config_before_real_nested_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_source, child_source, leaf_source, deepest_source, ultimate_source = _recursive_sources(
        tmp_path, hostile=True
    )
    credential, identity = _credential()
    _mapping(tmp_path, (HOST, OTHER_HOST, THIRD_HOST))
    executor, calls, _environments = _real_clone_executor(
        root_source, child_source, leaf_source, deepest_source, ultimate_source
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)

    with pytest.raises(GitWorkflowError, match="unsafe"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/root",
            tmp_path / "checkout",
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
            identity_transport=_identity_transport,
        ).run()

    checkout = tmp_path / "checkout"
    assert (checkout / "child" / ".git").exists()
    assert not (checkout / "child" / "leaf" / "vendor" / "lib" / ".git").exists()
    assert "git.clone.submodule-update" in calls
    assert "git.clone.submodule-update.dynamic-0" in calls
    assert "git.clone.submodule-update.dynamic-1" not in calls


def test_absent_recursive_clone_rejects_unmapped_deepest_frontier_before_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_source, child_source, leaf_source, deepest_source, ultimate_source = _recursive_sources(
        tmp_path
    )
    credential, identity = _credential()
    _mapping(tmp_path, (HOST, OTHER_HOST))
    executor, calls, _environments = _real_clone_executor(
        root_source, child_source, leaf_source, deepest_source, ultimate_source
    )
    monkeypatch.setattr(proc, "SubprocessExecutor", executor)

    with pytest.raises(GitWorkflowError, match="unavailable"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/root",
            tmp_path / "checkout",
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
            identity_transport=_identity_transport,
        ).run()

    checkout = tmp_path / "checkout"
    assert (checkout / "child" / "leaf" / ".git").exists()
    assert not (checkout / "child" / "leaf" / "vendor" / "lib" / ".git").exists()
    assert "git.clone.submodule-update.dynamic-0" in calls
    assert "git.clone.submodule-update.dynamic-1" not in calls


def test_recursive_clone_accepts_git_relative_sibling_override_and_rejects_project_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ours = tmp_path / "ours-source"
    _repo(ours, origin="https://gitlab.example/team/ours-addon")
    odoo = tmp_path / "odoo-source"
    _repo(odoo, origin="https://gitlab.example/team/odoo-addon")
    root = tmp_path / "b-source"
    _repo(root, origin="https://gitlab.example/team/b")
    _add_local_submodule(root, ours, "ours", "../ours-addon.git")
    _add_local_submodule(root, odoo, "odoo", "../odoo-addon.git")
    _git(root, "config", "submodule.ours.url", "https://gitlab.example/team/ours-addon.git")
    _git(root, "config", "submodule.odoo.url", "https://gitlab.example/team/odoo-addon.git")

    matching = git_repository.validate_submodule_configuration(root, allowed_hosts=(HOST,))
    assert matching == (
        ("ours", "https://gitlab.example/team/ours-addon", HOST),
        ("odoo", "https://gitlab.example/team/odoo-addon", HOST),
    )

    credential, identity = _credential()
    _mapping(tmp_path, (HOST,))
    channel_calls: list[object] = []
    monkeypatch.setattr(
        git_frontier,
        "write_askpass_channel",
        lambda *args, **kwargs: channel_calls.append((args, kwargs)),
    )
    command = GitResource(_instance(tmp_path)).clone_command(
        "https://gitlab.example/team/b",
        root,
        member_id=MEMBER,
        recurse_submodules=True,
        credential=credential,
        identity=identity,
        user_root=tmp_path,
        identity_transport=_identity_transport,
    )
    assert command.plan.process_steps

    _git(root, "config", "submodule.odoo.url", "https://gitlab.example/team/other.git")
    with pytest.raises(GitRepositoryError, match="unsafe"):
        GitResource(_instance(tmp_path)).clone_command(
            "https://gitlab.example/team/b",
            root,
            member_id=MEMBER,
            recurse_submodules=True,
            credential=credential,
            identity=identity,
            user_root=tmp_path,
            identity_transport=_identity_transport,
        )
    assert channel_calls == []


def test_generic_commit_uses_staged_scope_and_verified_identity(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / "staged.txt").write_text("staged\n", encoding="utf-8")
    (root / "unstaged.txt").write_text("unstaged\n", encoding="utf-8")
    _git(root, "add", "staged.txt")
    credential, identity = _credential()

    command = GitResource(_instance(tmp_path)).generic_commit_command(
        "make the change",
        repository=root,
        member_id=MEMBER,
        ticket="PROJ-12",
        credential=credential,
        identity=identity,
    )
    commit_step = next(
        step for step in command.plan.process_steps if step.step_id == "git.generic-commit"
    )
    assert commit_step.argv[-2:] == ("-m", "PROJ-12: make the change")
    assert "GIT_AUTHOR_NAME" in dict(commit_step.environment_overrides)
    result = command.run()

    assert result.subject == "PROJ-12: make the change"
    assert (
        _git(root, "show", "--format=%s", "--name-only", "--no-renames", "-1").find("staged.txt")
        >= 0
    )
    assert "unstaged.txt" not in _git(root, "show", "--format=", "--name-only", "-1")


def test_initialized_odoo_dispatch_keeps_scope_and_selected_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")
    _git(root, "add", ".odcli/project.toml")
    _git(root, "commit", "-qm", "project config")
    module = root / "addons" / "sale"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    (module / "models.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "addons/sale")
    _git(root, "config", "user.name", "Ambient User")
    _git(root, "config", "user.email", "ambient@example.test")
    _credential_context, selected_identity = _credential()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_explicit.resolve_credentials",
        lambda *_args, **_kwargs: (_credential_context, selected_identity),
    )

    command = GitResource(_instance(root)).commit_command(
        description="add a model",
        ticket="PROJ-12",
        repository=root,
        member_id=MEMBER,
    )
    commit_step = next(step for step in command.plan.process_steps if step.step_id == "git.commit")
    assert commit_step.argv[-1].startswith("[ADD] sale: PROJ-12 add a model")
    private_commit_step = cast(
        "proc.PreparedStep",
        next(step for step in command._prepared().steps if step.step_id == "git.commit"),
    )
    commit_environment = dict(private_commit_step.environment)
    assert commit_environment["GIT_AUTHOR_NAME"] == selected_identity.name
    assert commit_environment["GIT_AUTHOR_EMAIL"] == selected_identity.email
    assert commit_environment["GIT_COMMITTER_NAME"] == selected_identity.name
    assert commit_environment["GIT_COMMITTER_EMAIL"] == selected_identity.email
    command.run()
    assert (
        _git(root, "show", "-s", "--format=%an <%ae> %cn <%ce>").strip()
        == "Alice Example <alice@example.test> Alice Example <alice@example.test>"
    )


def test_generic_sync_plan_captures_branch_lease_and_rejects_stale_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _repo(root)
    credential, identity = _credential()
    remote_sha = "a" * 40
    fail_integrate = False
    calls: list[str] = []

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            nonlocal fail_integrate
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            stdout = ""
            returncode = 0
            if step_id == "git.sync.remote":
                stdout = "https://gitlab.example/team/repo\n"
            elif step_id == "git.sync.authoritative-remote-sha":
                stdout = f"{remote_sha}\trefs/heads/feature\n"
            elif step_id == "git.sync.branch":
                stdout = "feature\n"
            elif step_id == "git.sync.upstream":
                stdout = "origin/feature\n"
            elif step_id == "git.sync.fetched-sha":
                stdout = f"{remote_sha}\n"
            elif step_id == "git.sync.integrate" and fail_integrate:
                returncode = 1
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=returncode,
                stdout=stdout,
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    command = GitResource(_instance(tmp_path)).generic_sync_command(
        repository=root,
        member_id=MEMBER,
        base="main",
        push=True,
        credential=credential,
        identity=identity,
    )
    lease = next(
        step for step in command.plan.process_steps if step.step_id == "git.sync.push-lease"
    )
    assert any(
        argument.startswith("--force-with-lease=refs/heads/feature:") for argument in lease.argv
    )
    assert any(step.step_id == "git.sync.integrate" for step in command.plan.process_steps)

    fail_integrate = True
    with pytest.raises(GitSyncError, match="rebase conflict"):
        command.run()
    assert "git.sync.push-lease" not in calls

    _git(root, "remote", "set-url", "origin", "https://gitlab.example/team/changed")
    fail_integrate = False
    with pytest.raises(GitSyncError, match="origin or branch changed"):
        command.run()


def test_generic_sync_does_not_mutate_parent_gitlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = tmp_path / "child"
    _repo(child)
    parent = tmp_path / "parent"
    _repo(parent)
    _git(parent, "-c", "protocol.file.allow=always", "submodule", "add", str(child), "child")
    _git(child, "remote", "set-url", "origin", "https://gitlab.example/team/child")
    _git(parent, "commit", "-qm", "add child")
    parent_head = _git(parent, "rev-parse", "HEAD").strip()
    parent_index = _git(parent, "write-tree").strip()

    credential, identity = _credential()
    remote_sha = "a" * 40

    class PlanningExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:
            step_id = getattr(step, "step_id")
            stdout = ""
            if step_id == "git.sync.remote":
                stdout = "https://gitlab.example/team/child\n"
            elif step_id == "git.sync.authoritative-remote-sha":
                stdout = f"{remote_sha}\trefs/heads/feature\n"
            elif step_id == "git.sync.branch":
                stdout = "feature\n"
            elif step_id == "git.sync.upstream":
                stdout = "origin/feature\n"
            elif step_id == "git.sync.fetched-sha":
                stdout = f"{remote_sha}\n"
            return proc.ProcessResult(
                argv=tuple(getattr(step, "argv")),
                returncode=0,
                stdout=stdout,
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    monkeypatch.setattr(proc, "SubprocessExecutor", PlanningExecutor)
    GitResource(_instance(tmp_path)).generic_sync_command(
        repository=child,
        member_id=MEMBER,
        base="main",
        push=False,
        credential=credential,
        identity=identity,
    ).run()

    assert _git(parent, "rev-parse", "HEAD").strip() == parent_head
    assert _git(parent, "write-tree").strip() == parent_index
