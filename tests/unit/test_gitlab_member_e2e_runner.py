from __future__ import annotations

import json
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import _gitlab_member_cache as cache, e2e_gitlab_member_workflow as runner


@pytest.mark.unit
def test_installed_helper_provenance_accepts_exact_candidate_installation() -> None:
    import sys

    repository = Path(__file__).parents[2].resolve()
    helper_dir = Path(sys.executable).parent

    helper = cache._installed_helper_provenance(
        Namespace(helper_dir=helper_dir, core_project=repository), {"PATH": str(helper_dir)}
    )

    assert helper == (helper_dir / "git-credential-odcli").resolve()
    assert helper.is_relative_to(helper_dir.resolve())


@pytest.mark.unit
def test_installed_helper_provenance_rejects_escaped_launcher_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper_dir = tmp_path / "owned" / "bin"
    helper_dir.mkdir(parents=True)
    foreign_helper = tmp_path / "foreign-helper"
    foreign_helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    foreign_helper.chmod(0o700)
    (helper_dir / "git-credential-odcli").symlink_to(foreign_helper)
    monkeypatch.setattr(
        shutil, "which", lambda *_args, **_kwargs: str(helper_dir / "git-credential-odcli")
    )

    with pytest.raises(RuntimeError, match="escapes its owned installation"):
        cache._installed_helper_provenance(
            Namespace(helper_dir=helper_dir, core_project=Path(__file__).parents[2]), {}
        )


@pytest.mark.unit
def test_installed_helper_provenance_rejects_stale_package_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib
    import sys

    repository = Path(__file__).parents[2].resolve()
    helper_dir = Path(sys.executable).parent
    package = importlib.import_module("odoo_instance_sdk")
    helper_module = importlib.import_module("odoo_instance_sdk.internal.git_credential_helper")
    assert package.__file__ is not None
    assert helper_module.__file__ is not None
    assert sys.executable is not None
    stale_metadata = {
        "executable": sys.executable,
        "prefix": sys.prefix,
        "package": str(Path(package.__file__).parent.resolve()),
        "helper": str(Path(helper_module.__file__).resolve()),
        "files": {"internal/git_credential_helper.py": "stale-source-hash"},
        "entry_points": ["odoo_instance_sdk.internal.git_credential_helper:main"],
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(stale_metadata)),
    )

    with pytest.raises(RuntimeError, match="does not match the exact candidate source"):
        cache._installed_helper_provenance(
            Namespace(helper_dir=helper_dir, core_project=repository), {"PATH": str(helper_dir)}
        )


@pytest.mark.unit
def test_public_cache_runner_rejects_global_helper_before_daemon_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("TEST_GIT_HELPER_DIR", str(tmp_path / "owned" / "bin"))
    monkeypatch.setenv("TEST_CORE_PROJECT", str(Path(__file__).parents[2].resolve()))
    monkeypatch.setenv("PATH", str(tmp_path / "global-bin"))
    owned_bin = tmp_path / "owned" / "bin"
    owned_bin.mkdir(parents=True)
    global_bin = tmp_path / "global-bin"
    global_bin.mkdir()
    global_helper = global_bin / "git-credential-odcli"
    global_helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    global_helper.chmod(0o700)
    monkeypatch.setattr(
        cache,
        "_validate_integrated_cache_inputs",
        lambda _args: ("member", "a", "b", (tmp_path, tmp_path / "other")),
    )
    monkeypatch.setattr(cache, "_validate_integrated_checkouts", lambda *_args: None)
    monkeypatch.setattr(cache, "_secret_corpus", lambda _member: ("secret-fixture",))
    monkeypatch.setattr(
        cache, "_integrated_cache_environments", lambda *_args: ({"PATH": str(global_bin)}, {})
    )
    lifecycle = Mock(side_effect=AssertionError("daemon lifecycle mutation occurred"))
    monkeypatch.setattr(cache, "_profile_status", lifecycle)
    monkeypatch.setattr(cache, "_profile_stop", lifecycle)

    result = runner.main(["--mode", "multica-cache"])
    response = json.loads(capsys.readouterr().out)

    assert result == 1
    assert response["ok"] is False
    assert response["error"] == "candidate SDK credential helper is unavailable"
    lifecycle.assert_not_called()


@pytest.mark.unit
def test_cache_generation_stops_validates_starts_and_checks_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(multica="multica", profile="isolated")
    events: list[str] = []

    responses = iter(
        (
            ("start", SimpleNamespace(stdout="")),
            (
                "status",
                SimpleNamespace(
                    stdout=json.dumps({"profile": "isolated", "status": "running", "pid": 42})
                ),
            ),
        )
    )

    def execute(*_args: object, **_kwargs: object) -> SimpleNamespace:
        event, response = next(responses)
        events.append(event)
        return response

    run_mock = Mock(side_effect=execute)
    monkeypatch.setattr(cache, "_run", run_mock)
    monkeypatch.setattr(cache, "_profile_stop", lambda _args: events.append("stop"))
    monkeypatch.setattr(
        cache,
        "_validate_cache_helper",
        lambda *_args, **_kwargs: events.append("validate-installed-helper"),
    )

    cache._stop_and_validate_cache_generation(
        args,
        {
            "MULTICA_DAEMON_PORT": "41001",
            "MULTICA_TOKEN": "task-token-fixture",
            "ODCLI_GITLAB_PURPOSE": "cache-read",
        },
        "member",
        ("secret-fixture",),
        stop_running_generation=True,
    )
    status = cache._profile_start(
        args,
        {
            "MULTICA_DAEMON_PORT": "41001",
            "MULTICA_TOKEN": "task-token-fixture",
            "ODCLI_GITLAB_PURPOSE": "cache-read",
        },
    )

    assert events == ["stop", "validate-installed-helper", "start", "status"]
    invocations = run_mock.call_args_list
    assert [tuple(invocation.args[0]) for invocation in invocations] == [
        ("multica", "--profile", "isolated", "daemon", "start"),
        ("multica", "--profile", "isolated", "daemon", "status", "--output", "json"),
    ]
    start_environment = invocations[0].kwargs["env"]
    assert "MULTICA_TOKEN" not in start_environment
    assert "MULTICA_DAEMON_PORT" not in start_environment
    assert status["status"] == "running"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, ""), (0, "username=mallory\npassword=secret-fixture\n")],
    ids=["helper-refusal", "wrong-login"],
)
def test_installed_helper_validation_rejects_refusal_and_wrong_login(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str
) -> None:
    args = Namespace(git="git", cache_host="gitlab.example")
    captured: list[tuple[list[str], str, dict[str, str]]] = []
    monkeypatch.setattr(
        cache,
        "read_gitlab_credential_mappings",
        lambda: (SimpleNamespace(user_id="member", host="gitlab.example", login="alice"),),
    )

    def execute(
        argv: list[str], *, input: str, env: dict[str, str], **_kwargs: object
    ) -> SimpleNamespace:
        captured.append((argv, input, env))
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(
        cache, "_installed_helper_provenance", lambda *_args: Path("/owned/bin/helper")
    )
    monkeypatch.setattr(subprocess, "run", execute)
    with pytest.raises(RuntimeError, match="rejected"):
        cache._validate_cache_helper(
            args, {"ODCLI_GITLAB_PURPOSE": "cache-read"}, "member", ("secret-fixture",)
        )

    assert len(captured) == 1
    argv, request, _environment = captured[0]
    assert argv == ["git", "credential", "fill"]
    assert request == "protocol=https\nhost=gitlab.example\n\n"
    assert "secret-fixture" not in request


@pytest.mark.unit
def test_installed_helper_validation_accepts_exact_git_fill_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(git="git", cache_host="gitlab.example")
    environment = {"ODCLI_GITLAB_PURPOSE": "cache-read", "PATH": "/owned/bin:/usr/bin"}
    result = SimpleNamespace(returncode=0, stdout="username=alice\npassword=secret-fixture\n")
    run_mock = Mock(return_value=result)
    monkeypatch.setattr(
        cache,
        "_installed_helper_provenance",
        lambda *_args: Path("/owned/bin/git-credential-odcli"),
    )
    monkeypatch.setattr(
        cache,
        "read_gitlab_credential_mappings",
        lambda: (SimpleNamespace(user_id="member", host="gitlab.example", login="alice"),),
    )
    monkeypatch.setattr(subprocess, "run", run_mock)

    cache._validate_cache_helper(args, environment, "member", ("secret-fixture",))

    assert run_mock.call_args.args[0] == ["git", "credential", "fill"]
    assert run_mock.call_args.kwargs["input"] == "protocol=https\nhost=gitlab.example\n\n"
    assert run_mock.call_args.kwargs["env"] == environment
    assert run_mock.call_args.kwargs["capture_output"] is True


@pytest.mark.unit
def test_member_sync_command_keeps_complete_public_argv() -> None:
    args = Namespace(
        odcli_multica="odcli-multica",
        core_project=Path("/core"),
        multica_project="project",
        profile="isolated",
        workspace_id="workspace",
        repository_a="https://gitlab.example/team/repo",
    )

    command = runner._multica_sync_command(
        args,
        checkout=Path("/checkout"),
        issue="MYL-1",
        run_id="run-1",
        member_id="not-a-valid-member-id",
        dry_run=True,
    )

    assert command == [
        "odcli-multica",
        "git",
        "sync",
        "--project",
        "/core",
        "--multica-project",
        "project",
        "--issue",
        "MYL-1",
        "--run",
        "run-1",
        "--repository-url",
        "https://gitlab.example/team/repo",
        "--profile",
        "isolated",
        "--workspace-id",
        "workspace",
        "--member-id",
        "not-a-valid-member-id",
        "/checkout",
        "--format",
        "json",
        "--dry-run",
    ]


@pytest.mark.unit
def test_multica_cache_parser_accepts_exact_sha_and_negative_fixture_inputs() -> None:
    args = runner._parser().parse_args(
        [
            "--mode",
            "multica-cache",
            "--sdk-sha",
            "a" * 40,
            "--member-invalid",
            "invalid-member-fixture",
            "--cache-broader-scope-member",
            "broader-scope-fixture",
        ]
    )

    assert args.mode == "multica-cache"
    assert args.sdk_sha == "a" * 40
    assert args.member_invalid == "invalid-member-fixture"
    assert args.cache_broader_scope_member == "broader-scope-fixture"


@pytest.mark.unit
@pytest.mark.parametrize("status", ["stopped", "error"], ids=["stopped", "error"])
def test_profile_start_rejects_a_stopped_or_error_status(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    args = Namespace(multica="multica", profile="isolated")
    run_mock = Mock(
        side_effect=[
            SimpleNamespace(stdout=""),
            SimpleNamespace(stdout=json.dumps({"profile": "isolated", "status": status})),
        ]
    )
    monkeypatch.setattr(cache, "_run", run_mock)
    with pytest.raises(RuntimeError, match="did not become ready"):
        cache._profile_start(args, {})

    assert [tuple(call.args[0]) for call in run_mock.call_args_list] == [
        ("multica", "--profile", "isolated", "daemon", "start"),
        ("multica", "--profile", "isolated", "daemon", "status", "--output", "json"),
    ]


def _public_checkout_response(actual_checkout: Path, repository: str) -> SimpleNamespace:
    actual_checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(actual_checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(actual_checkout), "remote", "add", "origin", repository],
        check=True,
    )
    return SimpleNamespace(stdout=f"{actual_checkout}\n", stderr="checked out\n")


@pytest.mark.unit
def test_native_checkout_uses_the_public_daemon_repo_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    wrapper = tmp_path / "run" / "native-cache-checkout"
    actual_checkout = wrapper / "odoo-instance-sdk"
    daemon_status = {"profile": "isolated", "status": "running", "pid": 42}
    cache_environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "member-fixture",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
    daemon_environment = {
        **cache_environment,
        "MULTICA_DAEMON_PORT": "41001",
    }
    profile_status_mock = Mock(side_effect=[daemon_status, daemon_status])
    calls: list[tuple[tuple[str, ...], Path | None, dict[str, str] | None]] = []
    response_steps = iter(
        (
            lambda: SimpleNamespace(stdout=json.dumps(daemon_status)),
            lambda: _public_checkout_response(actual_checkout, args.repository_a),
            lambda: SimpleNamespace(stdout=json.dumps(daemon_status)),
        )
    )

    def execute(
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> SimpleNamespace:
        calls.append((tuple(argv), cwd, env))
        return next(response_steps)()

    run_mock = Mock(side_effect=execute)
    monkeypatch.setattr(cache, "_run", run_mock)
    monkeypatch.setattr(cache, "_profile_status", profile_status_mock)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: daemon_environment)
    cache._native_cache_checkout(
        args,
        tmp_path / "run",
        cache_environment,
        ("secret-fixture",),
        daemon_status,
    )
    recorded_calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = [
        (list(call.args[0]), call.kwargs.get("cwd"), call.kwargs.get("env"))
        for call in run_mock.call_args_list
    ]

    checkout_calls = [call for call in recorded_calls if call[0][1:3] != ["daemon", "status"]]
    assert len(checkout_calls) == 1
    argv, cwd, environment = checkout_calls[0]
    assert argv == ["multica", "repo", "checkout", args.repository_a]
    assert cwd == wrapper
    assert environment is not None
    assert environment["MULTICA_DAEMON_PORT"] == "41001"
    assert environment["MULTICA_TOKEN"] == "task-token-fixture"
    assert environment["MULTICA_TASK_ID"] == "task-fixture"
    assert environment["MULTICA_WORKSPACE_ID"] == "workspace-fixture"
    status_calls = [call for call in recorded_calls if call[0][1:3] == ["daemon", "status"]]
    assert len(status_calls) == 2
    assert all(
        call[0] == ["multica", "daemon", "status", "--output", "json"] for call in status_calls
    )
    assert all(call[2] is not None for call in status_calls)
    assert all(call[2]["MULTICA_DAEMON_PORT"] == "41001" for call in status_calls if call[2])
    assert all(call[2]["MULTICA_TOKEN"] == "task-token-fixture" for call in status_calls if call[2])
    assert profile_status_mock.call_count == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("checked out", "one absolute worktree path"),
        ("/outside/checkout", "outside the owned checkout root"),
    ],
)
def test_native_checkout_rejects_invalid_public_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str, message: str
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    status = {"profile": "isolated", "status": "running", "pid": 42}
    environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "member-fixture",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
    monkeypatch.setattr(cache, "_profile_status", lambda _args: status)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: environment)
    run_mock = Mock(
        side_effect=[
            SimpleNamespace(stdout=json.dumps(status)),
            SimpleNamespace(stdout=stdout),
        ]
    )
    monkeypatch.setattr(cache, "_run", run_mock)

    with pytest.raises(RuntimeError, match=message):
        cache._native_cache_checkout(args, tmp_path / "run", environment, (), status)

    calls = run_mock.call_args_list
    assert len(calls) == 2
    assert calls[1].args[0] == ["multica", "repo", "checkout", args.repository_a]
    assert calls[1].kwargs["cwd"] == tmp_path / "run" / "native-cache-checkout"


@pytest.mark.unit
def test_native_checkout_fails_closed_on_invalid_task_status_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    status = {"profile": "isolated", "status": "running", "pid": 42}
    environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "member-fixture",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
    commands: list[list[str]] = []

    def execute(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(argv)
        return SimpleNamespace(stdout="not-json")

    monkeypatch.setattr(cache, "_profile_status", lambda _args: status)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: environment)
    monkeypatch.setattr(cache, "_run", execute)

    with pytest.raises(RuntimeError, match="task daemon status response is invalid"):
        cache._native_cache_checkout(args, tmp_path / "run", environment, (), status)
    assert commands == [["multica", "daemon", "status", "--output", "json"]]


@pytest.mark.unit
def test_native_checkout_rejects_a_stale_candidate_daemon_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    candidate = {"profile": "isolated", "status": "running", "pid": 42}
    stale = {"profile": "isolated", "status": "running", "pid": 99}
    cache_environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
    }
    monkeypatch.setattr(cache, "_profile_status", lambda _args: stale)
    run_mock = Mock(side_effect=[RuntimeError("task daemon status unavailable")])
    monkeypatch.setattr(cache, "_run", run_mock)

    with pytest.raises(RuntimeError, match="generation changed"):
        cache._native_cache_checkout(args, tmp_path / "run", cache_environment, (), candidate)
    run_mock.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("binding_key", "binding_value", "message"),
    [
        ("MULTICA_TASK_ID", "foreign-task", "task binding does not match"),
        ("ODCLI_GITLAB_PURPOSE", "task", "cache binding does not match"),
        ("MULTICA_DAEMON_PORT", "42001", "does not match the candidate daemon"),
    ],
)
def test_native_checkout_rejects_foreign_daemon_bindings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    binding_key: str,
    binding_value: str,
    message: str,
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    status = {"profile": "isolated", "status": "running", "pid": 42}
    expected_environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "member-fixture",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
    daemon_environment = expected_environment | {"MULTICA_DAEMON_PORT": "41001"}
    daemon_environment[binding_key] = binding_value
    monkeypatch.setattr(cache, "_profile_status", lambda _args: status)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: daemon_environment)
    run_mock = Mock(return_value=SimpleNamespace(stdout=json.dumps(status)))
    monkeypatch.setattr(cache, "_run", run_mock)

    with pytest.raises(RuntimeError, match=message):
        cache._native_cache_checkout(args, tmp_path / "run", expected_environment, (), status)
    assert run_mock.call_count == 1
    assert run_mock.call_args.args[0] == ["multica", "daemon", "status", "--output", "json"]


@pytest.mark.unit
def test_native_checkout_preserves_public_cli_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    status = {"profile": "isolated", "status": "running", "pid": 42}
    environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "member-fixture",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
    run_mock = Mock(
        side_effect=[
            SimpleNamespace(stdout=json.dumps(status)),
            RuntimeError("command failed (1): multica checkout refused"),
        ]
    )
    monkeypatch.setattr(cache, "_profile_status", lambda _args: status)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: environment)
    monkeypatch.setattr(cache, "_run", run_mock)

    with pytest.raises(RuntimeError, match="checkout refused"):
        cache._native_cache_checkout(args, tmp_path / "run", environment, (), status)
    checkout_call = run_mock.call_args_list[1]
    assert checkout_call.args[0] == ["multica", "repo", "checkout", args.repository_a]
    assert checkout_call.kwargs["cwd"] == tmp_path / "run" / "native-cache-checkout"
    assert checkout_call.kwargs["env"]["MULTICA_DAEMON_PORT"] == "41001"
    assert checkout_call.kwargs["env"]["MULTICA_TOKEN"] == "task-token-fixture"
    assert checkout_call.kwargs["env"]["MULTICA_TASK_ID"] == "task-fixture"
    assert checkout_call.kwargs["env"]["MULTICA_WORKSPACE_ID"] == "workspace-fixture"


@pytest.mark.unit
def test_public_member_refusal_accepts_only_nonzero_secret_free_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(
        odcli_multica="odcli-multica",
        core_project=Path("/core"),
        multica_project="project",
        profile="isolated",
        workspace_id="workspace",
        repository_a="https://gitlab.example/team/repo",
        issue_b="MYL-2",
        run_b="run-2",
    )

    run_mock = Mock(
        return_value=SimpleNamespace(returncode=1, stdout="", stderr="member unavailable")
    )
    monkeypatch.setattr(subprocess, "run", run_mock)
    cache._assert_public_member_refusal(
        args,
        checkout=Path("/checkout"),
        member_id="invalid-member-fixture",
        secret_values=("secret-fixture",),
        prior_environment={},
    )

    assert run_mock.call_count == 1
    assert run_mock.call_args.args[0][-6:] == [
        "--member-id",
        "invalid-member-fixture",
        "/checkout",
        "--format",
        "json",
        "--dry-run",
    ]

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="fail-closed"):
        cache._assert_public_member_refusal(
            args,
            checkout=Path("/checkout"),
            member_id="invalid-member-fixture",
            secret_values=("secret-fixture",),
            prior_environment={},
        )


@pytest.mark.unit
def test_cache_negative_fixtures_are_all_executed_through_git_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(
        git="git",
        cache_host="gitlab.example",
        cache_owner_mismatch_member="owner-mismatch",
        cache_inactive_member="inactive",
        cache_broader_scope_member="broader",
        cache_granular_scope_member="granular",
        cache_incompatible_member="incompatible",
    )
    calls: list[tuple[list[str], str, dict[str, str]]] = []

    def execute(
        argv: list[str], *, input: str, env: dict[str, str], **_kwargs: object
    ) -> SimpleNamespace:
        calls.append((argv, input, env))
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", execute)
    evidence: list[dict[str, str | int | bool]] = []
    cache._assert_cache_helper_negative_inputs(
        args,
        {
            "ODCLI_GITLAB_PURPOSE": "cache-read",
            "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        },
        ("secret-fixture",),
        evidence,
    )

    assert len(calls) == 8
    assert calls[0][1] == "protocol=https\nhost=wrong.example\n\n"
    assert calls[1][1] == "protocol=https\nhost=gitlab.example\nhost=wrong.example\n\n"
    assert "ODCLI_GITLAB_PURPOSE" not in calls[2][2]
    assert [call[2]["ODCLI_GITLAB_MEMBER_ID"] for call in calls[3:]] == [
        "owner-mismatch",
        "inactive",
        "broader",
        "granular",
        "incompatible",
    ]
    assert evidence == [
        {"case": "C1", "negative_inputs": 3, "ok": True},
        {"case": "C1", "rejected_fixture": "owner_mismatch", "ok": True},
        {"case": "C1", "rejected_fixture": "inactive_token", "ok": True},
        {"case": "C1", "rejected_fixture": "broader_scope", "ok": True},
        {"case": "C1", "rejected_fixture": "granular_scope", "ok": True},
        {"case": "C1", "rejected_fixture": "incompatible_response", "ok": True},
    ]


@pytest.mark.unit
def test_rollback_starts_prior_generation_without_replacement_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace()
    events: list[str] = []
    cache_environment = _rollback_fixture(monkeypatch, events, {"GIT_CONFIG_VALUE_1": ""})

    cache._rollback_cache_generation(args, {}, cache_environment)

    assert events == ["stop", "start-prior"]


@pytest.mark.unit
def test_rollback_rejects_replacement_generation_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace()
    events: list[str] = []
    cache_environment = _rollback_fixture(monkeypatch, events, {"GIT_CONFIG_VALUE_1": "odcli"})

    with pytest.raises(RuntimeError, match="inherited replacement"):
        cache._rollback_cache_generation(args, {}, cache_environment)

    assert events == ["stop", "start-prior"]


def _rollback_fixture(
    monkeypatch: pytest.MonkeyPatch, events: list[str], inherited: dict[str, str]
) -> dict[str, str]:
    def start_prior(_args: Namespace, _environment: dict[str, str]) -> dict[str, object]:
        events.append("start-prior")
        return {"pid": 42}

    monkeypatch.setattr(cache, "_profile_stop", lambda _args: events.append("stop"))
    monkeypatch.setattr(cache, "_profile_start", start_prior)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: inherited)
    return {
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "cache-member",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
