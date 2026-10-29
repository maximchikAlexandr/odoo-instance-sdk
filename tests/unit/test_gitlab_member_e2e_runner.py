from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import e2e_gitlab_member_workflow as runner


@pytest.mark.unit
def test_cache_generation_stops_validates_starts_and_checks_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(multica="multica", profile="isolated")
    calls: list[tuple[tuple[str, ...], dict[str, str] | None]] = []
    events: list[str] = []

    def execute(
        argv: list[str], *, env: dict[str, str] | None = None, **_kwargs: object
    ) -> SimpleNamespace:
        calls.append((tuple(argv), env))
        if argv[-3:] == ["status", "--output", "json"]:
            return SimpleNamespace(
                stdout=json.dumps({"profile": "isolated", "status": "running", "pid": 42})
            )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(runner, "_run", execute)
    monkeypatch.setattr(runner, "_profile_stop", lambda _args: events.append("stop"))
    monkeypatch.setattr(
        runner,
        "_validate_cache_helper",
        lambda *_args: events.append("validate-installed-helper"),
    )

    runner._stop_and_validate_cache_generation(
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
    status = runner._profile_start(
        args,
        {
            "MULTICA_DAEMON_PORT": "41001",
            "MULTICA_TOKEN": "task-token-fixture",
            "ODCLI_GITLAB_PURPOSE": "cache-read",
        },
    )

    assert events == ["stop", "validate-installed-helper"]
    assert [call[0] for call in calls] == [
        ("multica", "--profile", "isolated", "daemon", "start"),
        ("multica", "--profile", "isolated", "daemon", "status", "--output", "json"),
    ]
    assert calls[0][1] is not None
    assert "MULTICA_TOKEN" not in calls[0][1]
    assert "MULTICA_DAEMON_PORT" not in calls[0][1]
    assert status["status"] == "running"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("returncode", "stdout", "accepted"),
    [
        (0, "username=alice\npassword=secret-fixture\n", True),
        (1, "", False),
        (0, "username=mallory\npassword=secret-fixture\n", False),
    ],
)
def test_installed_helper_validation_uses_git_fill_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    accepted: bool,
) -> None:
    args = Namespace(git="git", cache_host="gitlab.example")
    captured: list[tuple[list[str], str, dict[str, str]]] = []
    monkeypatch.setattr(
        runner,
        "read_gitlab_credential_mappings",
        lambda: (SimpleNamespace(user_id="member", host="gitlab.example", login="alice"),),
    )

    def execute(
        argv: list[str], *, input: str, env: dict[str, str], **_kwargs: object
    ) -> SimpleNamespace:
        captured.append((argv, input, env))
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", execute)
    if accepted:
        runner._validate_cache_helper(
            args, {"ODCLI_GITLAB_PURPOSE": "cache-read"}, "member", ("secret-fixture",)
        )
    else:
        with pytest.raises(RuntimeError, match="rejected"):
            runner._validate_cache_helper(
                args, {"ODCLI_GITLAB_PURPOSE": "cache-read"}, "member", ("secret-fixture",)
            )

    assert len(captured) == 1
    argv, request, _environment = captured[0]
    assert argv == ["git", "credential", "fill"]
    assert request == "protocol=https\nhost=gitlab.example\n\n"
    assert "secret-fixture" not in request


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
def test_profile_start_rejects_a_stopped_or_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(multica="multica", profile="isolated")
    calls: list[tuple[str, ...]] = []

    def execute(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(tuple(argv))
        if argv[-3:] == ["status", "--output", "json"]:
            return SimpleNamespace(stdout=json.dumps({"profile": "isolated", "status": "stopped"}))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(runner, "_run", execute)
    with pytest.raises(RuntimeError, match="did not become ready"):
        runner._profile_start(args, {})

    assert calls == [
        ("multica", "--profile", "isolated", "daemon", "start"),
        ("multica", "--profile", "isolated", "daemon", "status", "--output", "json"),
    ]


@pytest.mark.unit
def test_native_checkout_uses_the_public_daemon_repo_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setattr(Path, "cwd", classmethod(lambda _cls: tmp_path))
    calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )

    def execute(
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> SimpleNamespace:
        calls.append((argv, cwd, env))
        return SimpleNamespace(stdout="checked out")

    monkeypatch.setattr(runner, "_run", execute)
    monkeypatch.setattr(runner, "_git", lambda *_args, **_kwargs: args.repository_a)
    runner._native_cache_checkout(
        args,
        tmp_path / "run",
        {
            "MULTICA_DAEMON_PORT": "41001",
            "MULTICA_TOKEN": "task-token-fixture",
            "ODCLI_GITLAB_PURPOSE": "cache-read",
        },
        ("secret-fixture",),
    )

    assert len(calls) == 1
    argv, cwd, environment = calls[0]
    assert argv == [
        "multica",
        "--profile",
        "isolated",
        "repo",
        "checkout",
        args.repository_a,
    ]
    assert cwd == tmp_path / "run" / "native-cache-checkout"
    assert environment is not None
    assert environment["MULTICA_DAEMON_PORT"] == "41001"
    assert environment["MULTICA_TOKEN"] == "task-token-fixture"


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
    calls: list[list[str]] = []

    def execute(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="member unavailable")

    monkeypatch.setattr(subprocess, "run", execute)
    runner._assert_public_member_refusal(
        args,
        checkout=Path("/checkout"),
        member_id="invalid-member-fixture",
        secret_values=("secret-fixture",),
        prior_environment={},
    )

    assert len(calls) == 1
    assert calls[0][-6:] == [
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
        runner._assert_public_member_refusal(
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
    runner._assert_cache_helper_negative_inputs(
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
@pytest.mark.parametrize(
    ("inherited", "raises"),
    [
        ({"GIT_CONFIG_VALUE_1": ""}, False),
        ({"GIT_CONFIG_VALUE_1": "odcli"}, True),
    ],
)
def test_rollback_starts_prior_generation_and_rejects_replacement_inputs(
    monkeypatch: pytest.MonkeyPatch,
    inherited: dict[str, str],
    raises: bool,
) -> None:
    args = Namespace()
    events: list[str] = []

    def start_prior(_args: Namespace, _environment: dict[str, str]) -> dict[str, object]:
        events.append("start-prior")
        return {"pid": 42}

    monkeypatch.setattr(runner, "_profile_stop", lambda _args: events.append("stop"))
    monkeypatch.setattr(runner, "_profile_start", start_prior)
    monkeypatch.setattr(runner, "_daemon_environment", lambda _status: inherited)
    cache_environment = {
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "cache-member",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }

    if raises:
        with pytest.raises(RuntimeError, match="inherited replacement"):
            runner._rollback_cache_generation(args, {}, cache_environment)
    else:
        runner._rollback_cache_generation(args, {}, cache_environment)

    assert events == ["stop", "start-prior"]
