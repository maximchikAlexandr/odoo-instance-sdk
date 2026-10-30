"""Private support for the existing GitLab cache workflow runner.

This module has no CLI entrypoint; the public orchestration command remains
``scripts/e2e_gitlab_member_workflow.py --mode multica-cache``.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from odoo_instance_sdk.internal.gitlab_credentials import (
    normalize_member_id,
    read_gitlab_credential_mappings,
)
from scripts import _gitlab_member_provenance as provenance, e2e_gitlab_member_workflow as runner

_git = runner._git
_run = runner._run
_secret_corpus = runner._secret_corpus
_repository_identity = runner._repository_identity
_multica_sync_command = runner._multica_sync_command
_member_observation = runner._member_observation


def _profile_status(args: argparse.Namespace) -> dict[str, object]:
    result = _run(
        [args.multica, "--profile", args.profile, "daemon", "status", "--output", "json"],
        env=_daemon_profile_environment(args),
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise TypeError("isolated daemon status is invalid")
    return payload


def _profile_start(args: argparse.Namespace, environment: dict[str, str]) -> dict[str, object]:
    _run(
        [args.multica, "--profile", args.profile, "daemon", "start"],
        env=_daemon_profile_environment(args, environment),
    )
    status = _profile_status(args)
    if status.get("status") != "running" or status.get("profile") != args.profile:
        raise RuntimeError("isolated daemon did not become ready")
    return status


def _profile_stop(args: argparse.Namespace) -> None:
    _run(
        [args.multica, "--profile", args.profile, "daemon", "stop"],
        env=_daemon_profile_environment(args),
    )


def _daemon_profile_environment(
    args: argparse.Namespace, environment: dict[str, str] | None = None
) -> dict[str, str]:
    selected = os.environ.copy() if environment is None else environment.copy()
    selected.pop("MULTICA_TOKEN", None)
    selected.pop("MULTICA_DAEMON_PORT", None)
    selected["PATH"] = getattr(args, "_trusted_search_path", os.defpath)
    return selected


def _daemon_environment(status: dict[str, object]) -> dict[str, str]:
    daemon_pid = status.get("pid")
    if not isinstance(daemon_pid, int):
        raise TypeError("isolated daemon process identity is unavailable")
    try:
        import psutil

        return psutil.Process(daemon_pid).environ()
    except Exception as exc:
        raise RuntimeError("isolated daemon environment could not be verified") from exc


def _validate_integrated_cache_inputs(
    args: argparse.Namespace,
) -> tuple[str, str, str, tuple[Path, Path]]:
    required = (
        args.profile,
        args.helper_dir,
        args.core_project,
        args.sdk_sha,
        args.multica_project,
        args.workspace_id,
        args.cache_member_id,
        args.cache_owner_mismatch_member,
        args.cache_inactive_member,
        args.cache_broader_scope_member,
        args.cache_granular_scope_member,
        args.cache_incompatible_member,
        args.member_id,
        args.member_b,
        args.member_missing,
        args.member_invalid,
        args.member_nonhuman,
        args.member_cross_workspace,
        args.member_stale,
        args.cache_host,
        args.issue_a,
        args.run_a,
        args.issue_b,
        args.run_b,
        args.checkout_a,
        args.checkout_b,
        args.repository_a,
    )
    if any(value is None or value == "" for value in required):
        raise RuntimeError("multica-cache requires an isolated profile and complete test inputs")
    cache_member = normalize_member_id(args.cache_member_id)
    member_a = normalize_member_id(args.member_id)
    member_b = normalize_member_id(args.member_b)
    checkouts = (args.checkout_a.expanduser().resolve(), args.checkout_b.expanduser().resolve())
    if checkouts[0] == checkouts[1] or any(not path.is_dir() for path in checkouts):
        raise RuntimeError("two distinct existing task checkouts are required")
    parsed = urlsplit(args.repository_a)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
    ):
        raise RuntimeError("cache repository must be an HTTPS URL without embedded credentials")
    expected_host = parsed.hostname.casefold()
    if parsed.port is not None:
        expected_host = f"{expected_host}:{parsed.port}"
    if args.cache_host != expected_host:
        raise RuntimeError("cache host does not match the private repository authority")
    provenance.validate_candidate_repository(args)
    return cache_member, member_a, member_b, checkouts


def _validate_integrated_checkouts(args: argparse.Namespace, checkouts: tuple[Path, Path]) -> None:
    for checkout in checkouts:
        if (checkout / ".odcli" / "gitlab-credentials.toml").exists() or (
            checkout / ".odcli" / ".env"
        ).exists():
            raise RuntimeError("task checkouts must not contain project credential copies")
        remote = _git(args.git, checkout, "remote", "get-url", "origin")
        if _repository_identity(remote) != _repository_identity(args.repository_a):
            raise RuntimeError("task checkout origin does not match the private test repository")
        if _git(args.git, checkout, "status", "--porcelain=v1", "--untracked-files=all"):
            raise RuntimeError("task checkouts must start clean")


def _integrated_cache_environments(
    args: argparse.Namespace,
    cache_member: str,
    helper_path: Path,
) -> tuple[dict[str, str], dict[str, str]]:
    cache_environment = os.environ.copy()
    cache_environment.pop("MULTICA_TOKEN", None)
    cache_environment.pop("MULTICA_DAEMON_PORT", None)
    trusted_search_path = getattr(args, "_trusted_search_path", os.defpath)
    cache_environment["PATH"] = trusted_search_path
    cache_environment.update(
        {
            "ODCLI_GITLAB_PURPOSE": "cache-read",
            "ODCLI_GITLAB_MEMBER_ID": cache_member,
            "ODCLI_GITLAB_SELECTED_HOST": args.cache_host,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": f"!{shlex.quote(str(helper_path))}",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    prior_environment = os.environ.copy()
    prior_environment["PATH"] = trusted_search_path
    for key in tuple(prior_environment):
        if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            prior_environment.pop(key, None)
    for key in (
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "ODCLI_GITLAB_PURPOSE",
        "ODCLI_GITLAB_MEMBER_ID",
        "ODCLI_GITLAB_SELECTED_HOST",
    ):
        prior_environment.pop(key, None)
    return cache_environment, prior_environment


@dataclass(frozen=True, slots=True)
class CacheRunState:
    cache_member: str
    member_a: str
    member_b: str
    checkouts: tuple[Path, Path]
    secret_values: tuple[str, ...] = field(repr=False)
    cache_environment: dict[str, str] = field(repr=False)
    prior_environment: dict[str, str] = field(repr=False)
    helper_path: Path = field(repr=False)


def _installed_helper_provenance(
    args: argparse.Namespace, _environment: dict[str, str] | None = None
) -> Path:
    return provenance.validate_installed_helper(args)


def _trusted_git_executable(args: argparse.Namespace, helper_path: Path) -> Path:
    requested = Path(args.git).expanduser()
    resolved = (
        str(requested.resolve(strict=True))
        if requested.is_absolute()
        else shutil.which(args.git, path=getattr(args, "_trusted_search_path", os.defpath))
    )
    if resolved is None:
        raise RuntimeError("trusted Git executable is unavailable outside candidate installation")
    executable = Path(resolved).resolve(strict=True)
    install_root = helper_path.parent.parent.resolve()
    if (
        not executable.is_file()
        or not os.access(executable, os.X_OK)
        or executable.is_relative_to(install_root)
    ):
        raise RuntimeError("trusted Git executable is unavailable outside candidate installation")
    return executable


def _integrated_cache_readiness(
    args: argparse.Namespace,
    root: Path,
) -> CacheRunState:
    if not os.environ.get("MULTICA_DAEMON_PORT") or not os.environ.get("MULTICA_TOKEN"):
        raise RuntimeError("multica-cache requires an isolated daemon task context")
    if not root.resolve().is_relative_to(Path.cwd().resolve()):
        raise RuntimeError("multica-cache work root must be inside the isolated daemon task")
    cache_member, member_a, member_b, checkouts = _validate_integrated_cache_inputs(args)
    _validate_integrated_checkouts(args, checkouts)
    helper_path = _installed_helper_provenance(args)
    trusted_git = _trusted_git_executable(args, helper_path)
    args._trusted_git_path = trusted_git
    cache_environment, prior_environment = _integrated_cache_environments(
        args, cache_member, helper_path
    )
    cache_secrets = _secret_corpus(cache_member)
    secret_values = tuple(
        dict.fromkeys(
            (
                *cache_secrets,
                *tuple(
                    secret
                    for member in (
                        args.cache_owner_mismatch_member,
                        args.cache_inactive_member,
                        args.cache_broader_scope_member,
                        args.cache_granular_scope_member,
                        args.cache_incompatible_member,
                    )
                    for secret in _secret_corpus(member)
                ),
                *_secret_corpus(member_a),
                *_secret_corpus(member_b),
            )
        )
    )
    return CacheRunState(
        cache_member=cache_member,
        member_a=member_a,
        member_b=member_b,
        checkouts=checkouts,
        secret_values=secret_values,
        cache_environment=cache_environment,
        prior_environment=prior_environment,
        helper_path=helper_path,
    )


def _validate_cache_helper(
    args: argparse.Namespace,
    cache_environment: dict[str, str],
    cache_member: str,
    secret_values: tuple[str, ...],
    *,
    expected_helper: Path | None = None,
) -> None:
    helper = _run_cache_helper(
        args,
        cache_environment,
        member_id=cache_member,
        request=f"protocol=https\nhost={args.cache_host}\n\n",
        expected_helper=expected_helper,
    )
    helper_fields = dict(
        line.partition("=")[::2] for line in helper.stdout.splitlines() if "=" in line
    )
    cache_mapping = next(
        (
            mapping
            for mapping in read_gitlab_credential_mappings()
            if mapping.user_id.casefold() == cache_member.casefold()
            and mapping.host == args.cache_host
        ),
        None,
    )
    if (
        helper.returncode != 0
        or cache_mapping is None
        or helper_fields.get("username") != cache_mapping.login
        or helper_fields.get("password") not in secret_values
    ):
        raise RuntimeError("installed cache helper rejected the configured account")


def _credential_helper_environment(
    args: argparse.Namespace,
    environment: dict[str, str],
    pycache_prefix: str,
    member_id: str,
    purpose: bool,
    helper_path: Path,
) -> dict[str, str]:
    allowed = (
        "HOME",
        "USERPROFILE",
        "XDG_CONFIG_HOME",
        "APPDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CURL_CA_BUNDLE",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "NO_PROXY",
        "no_proxy",
        "ODCLI_GITLAB_USER_ROOT",
        "ODCLI_GITLAB_MAPPING_PATH",
        "ODCLI_GITLAB_ENVIRONMENT_PATH",
    )
    selected = {key: environment[key] for key in allowed if environment.get(key)}
    selected.update(
        {
            "PATH": getattr(args, "_trusted_search_path", os.defpath),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPYCACHEPREFIX": pycache_prefix,
            "ODCLI_GITLAB_MEMBER_ID": normalize_member_id(member_id),
            "ODCLI_GITLAB_SELECTED_HOST": args.cache_host,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": f"!{shlex.quote(str(helper_path))}",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if purpose:
        selected["ODCLI_GITLAB_PURPOSE"] = "cache-read"
    selected_members = {normalize_member_id(member_id).casefold()}
    token_keys = {
        mapping.token_key
        for mapping in read_gitlab_credential_mappings()
        if mapping.user_id.casefold() in selected_members
        and mapping.host.casefold() == args.cache_host.casefold()
    }
    selected.update((key, environment[key]) for key in token_keys if environment.get(key))
    return selected


def _run_cache_helper(
    args: argparse.Namespace,
    environment: dict[str, str],
    *,
    member_id: str,
    request: str,
    expected_helper: Path | None = None,
    purpose: bool = True,
) -> subprocess.CompletedProcess[str]:
    helper_path = _installed_helper_provenance(args, environment)
    if expected_helper is not None and helper_path != expected_helper:
        raise RuntimeError("candidate SDK credential helper changed after readiness")
    trusted_git = _trusted_git_executable(args, helper_path)
    if getattr(args, "_trusted_git_path", trusted_git) != trusted_git:
        raise RuntimeError("trusted Git executable changed after cache readiness")
    with tempfile.TemporaryDirectory(prefix="odcli-helper-pycache-") as pycache_prefix:
        helper_environment = _credential_helper_environment(
            args, environment, pycache_prefix, member_id, purpose, helper_path
        )
        return subprocess.run(
            [str(trusted_git), "credential", "fill"],
            input=request,
            env=helper_environment,
            capture_output=True,
            text=True,
            check=False,
        )


def _stop_and_validate_cache_generation(
    args: argparse.Namespace,
    environment: dict[str, str],
    cache_member: str,
    secret_values: tuple[str, ...],
    *,
    stop_running_generation: bool,
    expected_helper: Path | None = None,
) -> None:
    if stop_running_generation:
        _profile_stop(args)
    _validate_cache_helper(
        args, environment, cache_member, secret_values, expected_helper=expected_helper
    )


def _assert_cache_helper_refusal(
    args: argparse.Namespace,
    environment: dict[str, str],
    secret_values: tuple[str, ...],
    *,
    host: str,
    request: str,
    member_id: str,
    expected_helper: Path,
    purpose: bool = True,
) -> None:
    attempt_environment = environment.copy()
    if not purpose:
        attempt_environment.pop("ODCLI_GITLAB_PURPOSE", None)
    attempt_environment["ODCLI_GITLAB_SELECTED_HOST"] = args.cache_host
    result = _run_cache_helper(
        args,
        attempt_environment,
        member_id=member_id,
        request=request.format(host=host),
        expected_helper=expected_helper,
        purpose=purpose,
    )
    if result.returncode == 0 or any(
        secret and secret in result.stdout for secret in secret_values
    ):
        raise RuntimeError("cache helper accepted an invalid request or exposed a secret")


def _assert_cache_helper_negative_inputs(
    args: argparse.Namespace,
    environment: dict[str, str],
    secret_values: tuple[str, ...],
    evidence: list[dict[str, str | int | bool]],
    expected_helper: Path,
) -> None:
    _assert_cache_helper_refusal(
        args,
        environment,
        secret_values,
        host="wrong.example",
        request="protocol=https\nhost={host}\n\n",
        member_id=args.cache_member_id,
        expected_helper=expected_helper,
    )
    _assert_cache_helper_refusal(
        args,
        environment,
        secret_values,
        host=args.cache_host,
        request="protocol=https\nhost={host}\nhost=wrong.example\n\n",
        member_id=args.cache_member_id,
        expected_helper=expected_helper,
    )
    _assert_cache_helper_refusal(
        args,
        environment,
        secret_values,
        host=args.cache_host,
        request="protocol=https\nhost={host}\n\n",
        member_id=args.cache_member_id,
        expected_helper=expected_helper,
        purpose=False,
    )
    evidence.append({"case": "C1", "negative_inputs": 3, "ok": True})

    for label, member in (
        ("owner_mismatch", args.cache_owner_mismatch_member),
        ("inactive_token", args.cache_inactive_member),
        ("broader_scope", args.cache_broader_scope_member),
        ("granular_scope", args.cache_granular_scope_member),
        ("incompatible_response", args.cache_incompatible_member),
    ):
        result = _run_cache_helper(
            args,
            environment,
            member_id=member,
            request=f"protocol=https\nhost={args.cache_host}\n\n",
            expected_helper=expected_helper,
        )
        response = result.stdout + result.stderr
        if result.returncode == 0 or any(secret and secret in response for secret in secret_values):
            raise RuntimeError(f"cache helper did not reject {label} fixture safely")
        evidence.append({"case": "C1", "rejected_fixture": label, "ok": True})


def _native_cache_checkout(
    args: argparse.Namespace,
    root: Path,
    cache_environment: dict[str, str],
    secret_values: tuple[str, ...],
    candidate_status: dict[str, object],
) -> None:
    task_environment = os.environ.copy()
    task_environment["PATH"] = getattr(args, "_trusted_search_path", os.defpath)
    if not task_environment.get("MULTICA_DAEMON_PORT") or not task_environment.get("MULTICA_TOKEN"):
        raise RuntimeError("native checkout requires the isolated daemon task context")
    task_bindings = ("MULTICA_TASK_ID", "MULTICA_WORKSPACE_ID")
    if any(
        not task_environment.get(key) or cache_environment.get(key) != task_environment.get(key)
        for key in task_bindings
    ):
        raise RuntimeError("native checkout requires task and workspace bindings")
    wrapper = root / "native-cache-checkout"
    if not wrapper.resolve().is_relative_to(Path.cwd().resolve()):
        raise RuntimeError("native checkout workspace must be inside the isolated daemon task")
    expected_pid = _validate_native_cache_daemon(
        args, cache_environment, candidate_status, task_environment
    )
    wrapper.mkdir(mode=0o700, parents=True)
    result = _run(
        [args.multica, "repo", "checkout", args.repository_a],
        cwd=wrapper,
        env=task_environment,
        secrets=secret_values,
    )
    checkout = _native_checkout_result_path(result.stdout, wrapper)
    if Path(_git(args.git, checkout, "rev-parse", "--show-toplevel")).resolve() != checkout:
        raise RuntimeError("native Multica checkout response is not the actual worktree root")
    if _repository_identity(
        _git(args.git, checkout, "remote", "get-url", "origin")
    ) != _repository_identity(args.repository_a):
        raise RuntimeError("native Multica checkout origin does not match the private repository")
    final_status = _profile_status(args)
    if any(
        final_status.get(key) != expected
        for key, expected in (
            ("status", "running"),
            ("profile", args.profile),
            ("pid", expected_pid),
        )
    ):
        raise RuntimeError("native checkout candidate daemon generation changed during checkout")
    _assert_task_daemon_binding(args, task_environment, args.profile, expected_pid)


def _validate_native_cache_daemon(
    args: argparse.Namespace,
    cache_environment: dict[str, str],
    candidate_status: dict[str, object],
    task_environment: dict[str, str],
) -> int:
    expected_pid = candidate_status.get("pid")
    if (
        candidate_status.get("status") != "running"
        or candidate_status.get("profile") != args.profile
        or not isinstance(expected_pid, int)
    ):
        raise RuntimeError("native checkout candidate daemon identity is invalid")
    current_status = _profile_status(args)
    if any(
        current_status.get(key) != candidate_status.get(key) for key in ("status", "profile", "pid")
    ):
        raise RuntimeError("native checkout candidate daemon generation changed")
    _assert_task_daemon_binding(args, task_environment, args.profile, expected_pid)
    daemon_environment = _daemon_environment(candidate_status)
    task_bindings = ("MULTICA_TASK_ID", "MULTICA_WORKSPACE_ID")
    if any(daemon_environment.get(key) != cache_environment.get(key) for key in task_bindings):
        raise RuntimeError("native checkout candidate daemon task binding does not match")
    cache_bindings = (
        "ODCLI_GITLAB_PURPOSE",
        "ODCLI_GITLAB_MEMBER_ID",
        "ODCLI_GITLAB_SELECTED_HOST",
        "GIT_CONFIG_VALUE_1",
    )
    if any(daemon_environment.get(key) != cache_environment.get(key) for key in cache_bindings):
        raise RuntimeError("native checkout candidate daemon cache binding does not match")
    daemon_port = daemon_environment.get("MULTICA_DAEMON_PORT")
    if daemon_port is not None and daemon_port != task_environment["MULTICA_DAEMON_PORT"]:
        raise RuntimeError(
            "native checkout task transport port does not match the candidate daemon"
        )
    return expected_pid


def _assert_task_daemon_binding(
    args: argparse.Namespace,
    task_environment: dict[str, str],
    profile: str,
    expected_pid: int,
) -> None:
    result = _run(
        [args.multica, "daemon", "status", "--output", "json"],
        env=task_environment,
    )
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("task daemon status response is invalid") from exc
    if not isinstance(status, dict) or any(
        status.get(key) != expected
        for key, expected in (("status", "running"), ("profile", profile), ("pid", expected_pid))
    ):
        raise RuntimeError("native checkout task transport is not bound to the candidate daemon")


def _native_checkout_result_path(stdout: str, wrapper: Path) -> Path:
    reported_path = stdout.strip()
    if not reported_path or "\n" in reported_path or not Path(reported_path).is_absolute():
        raise RuntimeError("native Multica checkout did not return one absolute worktree path")
    checkout = Path(reported_path).resolve()
    owned_root = wrapper.resolve()
    if checkout == owned_root or not checkout.is_relative_to(owned_root):
        raise RuntimeError("native Multica checkout path is outside the owned checkout root")
    if not checkout.is_dir():
        raise RuntimeError("native Multica checkout path is not an existing directory")
    return checkout


def _run_integrated_member_operations(
    args: argparse.Namespace,
    checkouts: tuple[Path, Path],
    member_a: str,
    member_b: str,
    prior_environment: dict[str, str],
    secret_values: tuple[str, ...],
    evidence: list[dict[str, str | int | bool]],
) -> None:
    for case, checkout, issue, run_id, selected, expected_id in (
        ("C2", checkouts[0], args.issue_a, args.run_a, None, member_a),
        ("C3", checkouts[1], args.issue_b, args.run_b, member_b, member_b),
    ):
        preview = _run(
            _multica_sync_command(
                args,
                checkout=checkout,
                issue=issue,
                run_id=run_id,
                member_id=selected,
                dry_run=True,
            ),
            env=prior_environment,
            secrets=secret_values,
        )
        observation = _member_observation(json.loads(preview.stdout))
        expected_source = "root_creator" if selected is None else "explicit"
        if observation != (expected_id, expected_source):
            raise RuntimeError(f"{case} selected-member observation is incorrect")
        _run(
            _multica_sync_command(
                args,
                checkout=checkout,
                issue=issue,
                run_id=run_id,
                member_id=selected,
                dry_run=False,
            ),
            env=prior_environment,
            secrets=secret_values,
        )
        evidence.append({"case": case, "sync": True, "ok": True})

    failed = subprocess.run(
        _multica_sync_command(
            args,
            checkout=checkouts[1],
            issue=args.issue_b,
            run_id=args.run_b,
            member_id=normalize_member_id(args.member_missing),
            dry_run=False,
        ),
        env=prior_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    failure_text = failed.stdout + failed.stderr
    if failed.returncode == 0 or any(secret and secret in failure_text for secret in secret_values):
        raise RuntimeError("missing selected credentials did not fail safely")
    evidence.append({"case": "C3", "missing_member_failed": True, "ok": True})

    for label, invalid_member in (
        ("invalid", args.member_invalid),
        ("nonhuman", args.member_nonhuman),
        ("cross_workspace", args.member_cross_workspace),
        ("stale", args.member_stale),
    ):
        _assert_public_member_refusal(
            args,
            checkout=checkouts[1],
            member_id=invalid_member,
            secret_values=secret_values,
            prior_environment=prior_environment,
        )
        evidence.append({"case": "C2", "rejected_member": label, "ok": True})


def _assert_public_member_refusal(
    args: argparse.Namespace,
    *,
    checkout: Path,
    member_id: str,
    secret_values: tuple[str, ...],
    prior_environment: dict[str, str],
) -> None:
    rejected = subprocess.run(
        _multica_sync_command(
            args,
            checkout=checkout,
            issue=args.issue_b,
            run_id=args.run_b,
            member_id=member_id,
            dry_run=True,
        ),
        env=prior_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    rejection = rejected.stdout + rejected.stderr
    if rejected.returncode == 0 or any(secret and secret in rejection for secret in secret_values):
        raise RuntimeError("selected-member rejection was not fail-closed and secret-safe")


def _rollback_cache_generation(
    args: argparse.Namespace,
    prior_environment: dict[str, str],
    cache_environment: dict[str, str],
) -> None:
    _profile_stop(args)
    status = _profile_start(args, prior_environment)
    inherited = _daemon_environment(status)
    replacement = (
        "ODCLI_GITLAB_PURPOSE",
        "ODCLI_GITLAB_MEMBER_ID",
        "ODCLI_GITLAB_SELECTED_HOST",
        "GIT_CONFIG_VALUE_1",
    )
    if any(inherited.get(key) == cache_environment.get(key) for key in replacement):
        raise RuntimeError("rollback daemon inherited replacement cache settings")


def integrated_multica_cache(
    args: argparse.Namespace,
    root: Path,
    evidence: list[dict[str, str | int | bool]],
) -> tuple[str, ...]:
    state = _integrated_cache_readiness(args, root)
    cache_member = state.cache_member
    member_a = state.member_a
    member_b = state.member_b
    checkouts = state.checkouts
    secret_values = state.secret_values
    cache_environment = state.cache_environment
    prior_environment = state.prior_environment
    initial_status = _profile_status(args)
    if initial_status.get("profile") != args.profile:
        raise RuntimeError("Multica returned a different profile")

    candidate_started = False
    rollback_started = False
    try:
        _stop_and_validate_cache_generation(
            args,
            cache_environment,
            cache_member,
            secret_values,
            stop_running_generation=initial_status.get("status") == "running",
            expected_helper=state.helper_path,
        )
        evidence.append({"case": "C1", "helper_exit": 0, "ok": True})
        _assert_cache_helper_negative_inputs(
            args, cache_environment, secret_values, evidence, state.helper_path
        )

        candidate_started = True
        candidate_status = _profile_start(args, cache_environment)
        candidate_process_environment = _daemon_environment(candidate_status)
        if any(
            candidate_process_environment.get(key) != cache_environment.get(key)
            for key in (
                "ODCLI_GITLAB_PURPOSE",
                "ODCLI_GITLAB_MEMBER_ID",
                "ODCLI_GITLAB_SELECTED_HOST",
                "GIT_CONFIG_VALUE_1",
            )
        ):
            raise RuntimeError("candidate daemon did not inherit cache-read settings")
        _native_cache_checkout(args, root, cache_environment, secret_values, candidate_status)
        evidence.append({"case": "C1", "native_cache_checkout": True, "ok": True})

        _run_integrated_member_operations(
            args, checkouts, member_a, member_b, prior_environment, secret_values, evidence
        )

        _profile_stop(args)
        candidate_started = False
        rollback_started = True
        _rollback_cache_generation(args, prior_environment, cache_environment)
        evidence.append({"case": "C4", "rollback_started": True, "ok": True})
    finally:
        if candidate_started or rollback_started:
            _profile_stop(args)
    return secret_values
