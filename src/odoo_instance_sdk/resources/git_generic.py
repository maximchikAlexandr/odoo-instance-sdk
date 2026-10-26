"""Generic GitLab repository operations shared by the public Git resource."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from odoo_instance_sdk.exceptions import (
    GitRepositoryChangedError,
    GitRepositoryError,
    GitScopeError,
    GitSyncError,
    GitWorkflowError,
    PlanError,
    PlanValidationError,
)
from odoo_instance_sdk.internal.git_frontier import (
    credentials as resolve_credentials,
    environment as credential_environment,
    frontier_environment,
    validate_frontier,
    validated_frontier_credentials,
)
from odoo_instance_sdk.internal.git_repository import (
    missing_submodules,
    normalize_repository_url,
    repository_push_origin,
    resolve_repository,
    revalidate_repository,
    submodule_frontier_transport_arguments,
    submodule_recursive_transport_arguments,
    submodule_recursive_update_arguments,
    submodule_update_target,
    validate_credentialed_transport_configuration,
    validate_submodule_cache,
)
from odoo_instance_sdk.internal.git_sync import (
    AuthoritativeProbeCapture,
    SyncSteps,
    execute_sync,
)
from odoo_instance_sdk.internal.gitlab_credentials import (
    cleanup_askpass_channel,
    mapped_credential_hosts,
)
from odoo_instance_sdk.models import (
    GitCloneResult,
    GitCommitResult,
    GitLabCredentialContext,
    GitLabIdentity,
    GitRepositorySnapshot,
    GitSyncResult,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
    from odoo_instance_sdk.internal.gitlab_identity import GitLabUserTransport
    from odoo_instance_sdk.internal.proc import (
        PreparedStep,
        ProcessResult,
        RunContext,
    )
    from odoo_instance_sdk.resources.instance import OdooInstance


_FULL_SHA = re.compile(r"[0-9a-fA-F]{40}")
_PROTECTED = {"main", "master", "develop"}


class _SyncOptions(TypedDict):
    environment: Mapping[str, str]
    secrets: Sequence[str]
    hooks_path: str
    runtime_environment: Callable[
        [], tuple[tuple[tuple[str, str], ...], Callable[[], None], tuple[str, ...]]
    ]


def _text(value: str | bytes | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""


def _normalized_origin(value: str) -> str | None:
    try:
        return normalize_repository_url(value)[0]
    except GitRepositoryError:
        return None


def _remote_head(value: str) -> str | None:
    for line in value.splitlines():
        sha, separator, ref = line.strip().partition("\t")
        if separator and ref.startswith("refs/heads/") and _FULL_SHA.fullmatch(sha):
            return sha
    return None


def _planned_remote_sha(
    probe: ProcessResult, capture: AuthoritativeProbeCapture | None = None
) -> str | None:
    if probe.returncode != 0:
        raise GitSyncError(
            "authoritative remote branch probe failed; no fetch or publication was attempted"
        )
    has_output, planned = (
        capture.take(probe) if capture is not None else AuthoritativeProbeCapture().take(probe)
    )
    if not has_output:
        return None
    if planned is None:
        raise GitSyncError(
            "authoritative remote branch probe returned malformed output; "
            "no fetch or publication was attempted"
        )
    return planned


def _plan(
    steps: Sequence[PreparedStep], *, observations: Sequence[JsonValue] = ()
) -> ExecutionPlan:
    from odoo_instance_sdk.execution import ExecutionPlan

    return ExecutionPlan(
        steps=tuple(step.public_projection() for step in steps), observations=tuple(observations)
    ).with_fingerprint()


def _step(
    root: Path,
    step_id: str,
    args: Sequence[str],
    *,
    environment: Mapping[str, str],
    secrets: Sequence[str],
    hooks_path: str,
    credential_helper: str | None = None,
    runtime_environment: Callable[
        [], tuple[tuple[tuple[str, str], ...], Callable[[], None], tuple[str, ...]]
    ]
    | None = None,
    private_stdout_consumer: Callable[[str | bytes | None], None] | None = None,
    mutating: bool = False,
) -> PreparedStep:
    from odoo_instance_sdk.internal.proc import PreparedStep

    controls = (
        "-c",
        f"credential.helper={credential_helper or ''}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={hooks_path}",
        "-c",
        "protocol.ssh.allow=never",
        "-c",
        "protocol.ext.allow=never",
    )
    return PreparedStep(
        step_id=step_id,
        argv=("git", "-C", str(root), *controls, *args),
        cwd=str(root),
        read_only=not mutating,
        mutating=mutating,
        text=True,
        environment=tuple(sorted(environment.items())),
        secret_values=tuple(secrets),
        environment_policy="explicit",
        runtime_environment_factory=runtime_environment,
        private_stdout_consumer=private_stdout_consumer,
    )


def _status_missing(value: str | bytes | None) -> tuple[str, ...]:
    missing: list[str] = []
    for line in _text(value).splitlines():
        if not line.startswith("-"):
            continue
        fields = line[1:].strip().split(maxsplit=1)
        if len(fields) == 2:
            missing.append(fields[1].split(" ", 1)[0])
    return tuple(dict.fromkeys(missing))


def _cleanup(*environments: Mapping[str, str]) -> None:
    for environment in environments:
        channel = None
        channel = environment.get("ODCLI_GITLAB_ASKPASS_FILE") or environment.get(
            "ODCLI_GITLAB_TOKEN_FILE"
        )
        if isinstance(channel, str):
            cleanup_askpass_channel(channel)


def _description(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise PlanValidationError("description must be one non-empty line")
    return value.strip()


def _protected(branch: str | None) -> bool:
    return branch is None or branch in _PROTECTED or branch.startswith("release/")


class GenericGitMixin:
    """Public generic Git methods mixed into :class:`GitResource`."""

    _instance: OdooInstance

    def clone_command(  # noqa: C901
        self,
        url: str,
        destination: Path | str,
        *,
        member_id: str,
        recurse_submodules: bool = False,
        branch: str | None = None,
        credential: GitLabCredentialContext | None = None,
        identity: GitLabIdentity | None = None,
        identity_transport: GitLabUserTransport
        | Callable[[str, str], Mapping[str, JsonValue]]
        | None = None,
        user_root: Path | str | None = None,
        mapping_path: Path | str | None = None,
        environment_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
        askpass: str | None = None,
    ) -> Command[GitCloneResult]:
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        if branch is not None and (
            not branch
            or branch.startswith("-")
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in branch)
        ):
            raise GitWorkflowError("clone branch must be a non-empty Git branch name")
        origin, host, _project = normalize_repository_url(url)
        root = Path(destination).expanduser().resolve()
        selected, _verified = resolve_credentials(
            member_id,
            host,
            credential=credential,
            identity=identity,
            user_root=user_root,
            mapping_path=mapping_path,
            environment_path=environment_path,
            project_root=Path.cwd(),
            process_environment=process_environment,
            identity_transport=identity_transport,
        )
        reused = root.exists()
        snapshot: GitRepositorySnapshot | None = None
        credentials: tuple[GitLabCredentialContext, ...] = (selected,)
        if reused:
            if not root.is_dir():
                raise GitWorkflowError("clone destination is not a directory")
            snapshot = resolve_repository(root)
            if snapshot.origin != origin:
                raise GitWorkflowError("clone destination has a different origin")
            if branch is not None and snapshot.branch != branch:
                raise GitWorkflowError(
                    "existing destination branch does not match --branch; checkout was preserved"
                )
            if recurse_submodules:
                validate_frontier(
                    member_id,
                    root,
                    None,
                    selected=selected,
                    user_root=user_root,
                    mapping_path=mapping_path,
                    environment_path=environment_path,
                    process_environment=process_environment,
                    identity_transport=identity_transport,
                )
        credential_options = credential_environment(credentials, askpass)
        environment = credential_options["environment"]
        secrets = credential_options["secrets"]
        credential_helper = credential_options["helper"]
        hooks = os.devnull
        steps: list[PreparedStep] = []
        update_step_ids: tuple[str, ...] = ()
        if not reused:
            steps.append(
                _step(
                    root.parent,
                    "git.clone",
                    (
                        "clone",
                        *(("--branch", branch) if branch is not None else ()),
                        origin,
                        str(root),
                    ),
                    environment=environment,
                    secrets=secrets,
                    hooks_path=hooks,
                    credential_helper=credential_helper,
                    runtime_environment=credential_options["runtime_environment"],
                    mutating=True,
                )
            )
        if recurse_submodules:
            mapped_hosts = mapped_credential_hosts(
                selected, user_root=user_root, mapping_path=mapping_path
            )

            steps.append(
                _step(
                    root,
                    "git.clone.submodule-status",
                    ("submodule", "status", "--recursive"),
                    environment=environment,
                    secrets=secrets,
                    hooks_path=hooks,
                    credential_helper=credential_helper,
                    runtime_environment=credential_options["runtime_environment"],
                )
            )

            active_frontier_credentials: tuple[GitLabCredentialContext, ...] = (selected,)

            def runtime_frontier_credentials() -> tuple[GitLabCredentialContext, ...]:
                return active_frontier_credentials

            recursive_options = frontier_environment(
                selected,
                member_id=member_id,
                user_root=user_root,
                mapping_path=mapping_path,
                environment_path=environment_path,
                include_selected=False,
                credentials_provider=runtime_frontier_credentials,
            )

            def recursive_runtime_environment() -> tuple[
                tuple[tuple[str, str], ...], Callable[[], None], tuple[str, ...]
            ]:
                return frontier_environment(
                    selected,
                    member_id=member_id,
                    user_root=user_root,
                    mapping_path=mapping_path,
                    environment_path=environment_path,
                    include_selected=False,
                    credentials_provider=runtime_frontier_credentials,
                )["runtime_environment"]()

            direct_arguments = submodule_recursive_transport_arguments(mapped_hosts)[:-1]
            direct_root = root
            if reused:
                try:
                    missing_paths = missing_submodules(root)
                except GitRepositoryError:
                    missing_paths = ()
                update_step_ids = tuple(
                    f"git.clone.submodule-update.{index}"
                    for index, _path in enumerate(missing_paths)
                )
                for index, missing_path in enumerate(missing_paths):
                    owner, child_path = submodule_update_target(root, missing_path)
                    try:
                        arguments = submodule_recursive_update_arguments(
                            owner,
                            child_path,
                            allowed_hosts=mapped_hosts,
                        )
                    except GitRepositoryError:
                        arguments = (
                            *submodule_recursive_transport_arguments(mapped_hosts)[:-1],
                            "--recursive",
                            "--",
                            child_path,
                        )
                    steps.append(
                        _step(
                            owner,
                            f"git.clone.submodule-update.{index}",
                            arguments,
                            environment=recursive_options["environment"],
                            secrets=recursive_options["secrets"],
                            hooks_path=hooks,
                            runtime_environment=recursive_runtime_environment,
                            mutating=True,
                        )
                    )
            if not update_step_ids:
                update_step_ids = ("git.clone.submodule-update",)
                steps.append(
                    _step(
                        direct_root,
                        "git.clone.submodule-update",
                        direct_arguments,
                        environment=recursive_options["environment"],
                        secrets=recursive_options["secrets"],
                        hooks_path=hooks,
                        runtime_environment=recursive_runtime_environment,
                        mutating=True,
                    )
                )
            steps.append(
                _step(
                    root,
                    "git.clone.submodule-frontier",
                    submodule_frontier_transport_arguments(mapped_hosts),
                    environment=recursive_options["environment"],
                    secrets=recursive_options["secrets"],
                    hooks_path=hooks,
                    runtime_environment=recursive_runtime_environment,
                    mutating=True,
                )
            )
            steps.append(
                _step(
                    root,
                    "git.clone.submodule-update-recursive",
                    submodule_recursive_transport_arguments(mapped_hosts),
                    environment=recursive_options["environment"],
                    secrets=recursive_options["secrets"],
                    hooks_path=hooks,
                    runtime_environment=recursive_runtime_environment,
                    mutating=True,
                )
            )
            steps.append(
                _step(
                    root,
                    "git.clone.submodule-status-final",
                    ("submodule", "status", "--recursive"),
                    environment=environment,
                    secrets=secrets,
                    hooks_path=hooks,
                    credential_helper=credential_helper,
                    runtime_environment=credential_options["runtime_environment"],
                )
            )

        executor = SubprocessExecutor()

        def callback(context: RunContext[GitCloneResult]) -> GitCloneResult:  # noqa: C901
            nonlocal active_frontier_credentials
            try:
                if not reused:
                    result = cast("ProcessResult", context.process("git.clone"))
                    if result.returncode != 0:
                        raise GitWorkflowError("git clone failed")
                initialized: tuple[str, ...] = ()
                if recurse_submodules:
                    try:
                        if snapshot is not None:
                            revalidate_repository(snapshot)
                        else:
                            resolve_repository(root)
                        status = cast(
                            "ProcessResult", context.process("git.clone.submodule-status")
                        )
                        if status.returncode != 0:
                            context.skip_remaining()
                            raise GitWorkflowError("submodule status is unavailable")
                        initialized = _status_missing(status.stdout)
                        if initialized:
                            frontier_credentials = validated_frontier_credentials(
                                member_id,
                                root,
                                None,
                                selected=selected,
                                user_root=user_root,
                                mapping_path=mapping_path,
                                environment_path=environment_path,
                                process_environment=process_environment,
                                identity_transport=identity_transport,
                            )
                            if frontier_credentials:
                                active_frontier_credentials = frontier_credentials
                                validate_submodule_cache(root, allowed_hosts=mapped_hosts)
                                validate_frontier(
                                    member_id,
                                    root,
                                    None,
                                    selected=selected,
                                    user_root=user_root,
                                    mapping_path=mapping_path,
                                    environment_path=environment_path,
                                    process_environment=process_environment,
                                    identity_transport=identity_transport,
                                )
                                for update_step_id in update_step_ids:
                                    result = cast("ProcessResult", context.process(update_step_id))
                                    if result.returncode != 0:
                                        raise GitWorkflowError(
                                            _text(result.stderr)
                                            or "submodule initialization failed"
                                        )
                                if reused:
                                    follow_up_status = cast(
                                        "ProcessResult",
                                        context.process("git.clone.submodule-status-final"),
                                    )
                                    if follow_up_status.returncode != 0:
                                        raise GitWorkflowError("submodule status is unavailable")
                                    if _status_missing(follow_up_status.stdout):
                                        raise GitWorkflowError(
                                            "submodule initialization left missing paths"
                                        )
                                    context.skip_remaining()
                                    return GitCloneResult(
                                        destination=str(root),
                                        origin=origin,
                                        reused=reused,
                                        initialized_submodules=initialized,
                                    )
                                validate_submodule_cache(root, allowed_hosts=mapped_hosts)
                                validate_frontier(
                                    member_id,
                                    root,
                                    None,
                                    selected=selected,
                                    user_root=user_root,
                                    mapping_path=mapping_path,
                                    environment_path=environment_path,
                                    process_environment=process_environment,
                                    identity_transport=identity_transport,
                                )
                                active_frontier_credentials = validated_frontier_credentials(
                                    member_id,
                                    root,
                                    None,
                                    selected=selected,
                                    user_root=user_root,
                                    mapping_path=mapping_path,
                                    environment_path=environment_path,
                                    process_environment=process_environment,
                                    identity_transport=identity_transport,
                                )
                                result = cast(
                                    "ProcessResult",
                                    context.process("git.clone.submodule-frontier"),
                                )
                                if result.returncode != 0:
                                    raise GitWorkflowError(
                                        _text(result.stderr) or "submodule initialization failed"
                                    )
                                validate_submodule_cache(root, allowed_hosts=mapped_hosts)
                                active_frontier_credentials = validated_frontier_credentials(
                                    member_id,
                                    root,
                                    None,
                                    selected=selected,
                                    user_root=user_root,
                                    mapping_path=mapping_path,
                                    environment_path=environment_path,
                                    process_environment=process_environment,
                                    identity_transport=identity_transport,
                                )
                                result = cast(
                                    "ProcessResult",
                                    context.process("git.clone.submodule-update-recursive"),
                                )
                                if result.returncode != 0:
                                    raise GitWorkflowError(
                                        _text(result.stderr) or "submodule initialization failed"
                                    )
                                final_status = cast(
                                    "ProcessResult",
                                    context.process("git.clone.submodule-status-final"),
                                )
                                if final_status.returncode != 0 or _status_missing(
                                    final_status.stdout
                                ):
                                    raise GitWorkflowError(
                                        "submodule initialization left missing paths"
                                    )
                            else:
                                context.skip_remaining()
                        else:
                            context.skip_remaining()
                        validate_frontier(
                            member_id,
                            root,
                            None,
                            selected=selected,
                            user_root=user_root,
                            mapping_path=mapping_path,
                            environment_path=environment_path,
                            process_environment=process_environment,
                            identity_transport=identity_transport,
                        )
                    except (PlanError, GitRepositoryError) as exc:
                        context.skip_remaining()
                        raise GitWorkflowError(str(exc)) from None
                return GitCloneResult(
                    destination=str(root),
                    origin=origin,
                    reused=reused,
                    initialized_submodules=initialized,
                )
            finally:
                _cleanup(credential_options["environment"])

        observations: tuple[JsonValue, ...] = (
            ({"submodule_initialization": "recursive HTTPS update is captured in the plan"},)
            if recurse_submodules
            else ()
        )
        return Command.create(
            _plan(tuple(steps), observations=observations),
            callback,
            tuple(steps),
            executor=executor,
        )

    def clone(
        self,
        url: str,
        destination: Path | str,
        *,
        member_id: str,
        recurse_submodules: bool = False,
        branch: str | None = None,
        credential: GitLabCredentialContext | None = None,
        identity: GitLabIdentity | None = None,
        identity_transport: GitLabUserTransport
        | Callable[[str, str], Mapping[str, JsonValue]]
        | None = None,
        user_root: Path | str | None = None,
        mapping_path: Path | str | None = None,
        environment_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
        askpass: str | None = None,
    ) -> GitCloneResult:
        return self.clone_command(
            url,
            destination,
            member_id=member_id,
            recurse_submodules=recurse_submodules,
            branch=branch,
            credential=credential,
            identity=identity,
            identity_transport=identity_transport,
            user_root=user_root,
            mapping_path=mapping_path,
            environment_path=environment_path,
            process_environment=process_environment,
            askpass=askpass,
        ).run()

    def generic_commit_command(
        self,
        description: str,
        *,
        repository: Path | str,
        member_id: str,
        ticket: str | None = None,
        credential: GitLabCredentialContext | None = None,
        identity: GitLabIdentity | None = None,
        identity_transport: GitLabUserTransport
        | Callable[[str, str], Mapping[str, JsonValue]]
        | None = None,
        user_root: Path | str | None = None,
        mapping_path: Path | str | None = None,
        environment_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> Command[GitCommitResult]:
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        root = Path(repository).expanduser().resolve()
        snapshot = resolve_repository(root)
        if _protected(snapshot.branch):
            raise GitWorkflowError("detached or protected branches cannot be committed")
        if not snapshot.staged_paths:
            raise GitScopeError("git commit requires staged changes")
        _selected, verified = resolve_credentials(
            member_id,
            snapshot.host,
            credential=credential,
            identity=identity,
            user_root=user_root,
            mapping_path=mapping_path,
            environment_path=environment_path,
            project_root=root,
            process_environment=process_environment,
            identity_transport=identity_transport,
        )
        subject = (
            f"{ticket.strip()}: {_description(description)}"
            if ticket
            else _description(description)
        )
        if ticket is not None and (not ticket.strip() or "\n" in ticket or "\r" in ticket):
            raise PlanValidationError("ticket must be a non-empty one-line value")
        environment = {
            "GIT_AUTHOR_NAME": verified.name,
            "GIT_AUTHOR_EMAIL": verified.email,
            "GIT_COMMITTER_NAME": verified.name,
            "GIT_COMMITTER_EMAIL": verified.email,
        }
        hooks = os.devnull
        steps = (
            _step(
                root,
                "git.generic-commit.staged",
                ("diff", "--cached", "--name-status", "-z"),
                environment=environment,
                secrets=(),
                hooks_path=hooks,
            ),
            _step(
                root,
                "git.generic-commit.branch",
                ("symbolic-ref", "--quiet", "--short", "HEAD"),
                environment=environment,
                secrets=(),
                hooks_path=hooks,
            ),
            _step(
                root,
                "git.generic-commit.index-tree",
                ("write-tree",),
                environment=environment,
                secrets=(),
                hooks_path=hooks,
            ),
            _step(
                root,
                "git.generic-commit",
                ("commit", "-m", subject),
                environment=environment,
                secrets=(),
                hooks_path=hooks,
                mutating=True,
            ),
            _step(
                root,
                "git.generic-commit.head",
                ("rev-parse", "HEAD"),
                environment=environment,
                secrets=(),
                hooks_path=hooks,
            ),
        )

        def callback(context: RunContext[GitCommitResult]) -> GitCommitResult:
            try:
                try:
                    revalidate_repository(snapshot)
                except GitRepositoryChangedError:
                    context.skip_remaining()
                    raise GitWorkflowError(
                        "repository evidence changed after commit planning"
                    ) from None
                for step in steps[:3]:
                    result = cast("ProcessResult", context.process(step.step_id))
                    if result.returncode != 0:
                        context.skip("git.generic-commit")
                        context.skip("git.generic-commit.head")
                        raise GitWorkflowError("staged changes changed after commit planning")
                commit = cast("ProcessResult", context.process("git.generic-commit"))
                if commit.returncode != 0:
                    context.skip("git.generic-commit.head")
                    raise GitWorkflowError("git commit hook or commit failed")
                head = cast("ProcessResult", context.process("git.generic-commit.head"))
                sha = _text(head.stdout).strip()
                if head.returncode != 0 or not _FULL_SHA.fullmatch(sha):
                    raise GitWorkflowError("committed revision is unavailable")
                return GitCommitResult(
                    repository=str(root),
                    commit=sha,
                    subject=subject,
                    author=verified,
                    committer=verified,
                )
            finally:
                _cleanup()

        return Command.create(_plan(steps), callback, steps, executor=SubprocessExecutor())

    def generic_sync_command(
        self,
        *,
        repository: Path | str,
        member_id: str,
        base: str,
        push: bool = False,
        credential: GitLabCredentialContext | None = None,
        identity: GitLabIdentity | None = None,
        identity_transport: GitLabUserTransport
        | Callable[[str, str], Mapping[str, JsonValue]]
        | None = None,
        user_root: Path | str | None = None,
        mapping_path: Path | str | None = None,
        environment_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
        askpass: str | None = None,
    ) -> Command[GitSyncResult]:
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        root = Path(repository).expanduser().resolve()
        snapshot = resolve_repository(root)
        if _protected(snapshot.branch):
            raise GitSyncError("detached or protected branches cannot be synchronized")
        if not base or base.startswith("-"):
            raise GitSyncError("Git base ref must be explicit and must not start with '-'")
        selected, _verified = resolve_credentials(
            member_id,
            snapshot.host,
            credential=credential,
            identity=identity,
            user_root=user_root,
            mapping_path=mapping_path,
            environment_path=environment_path,
            project_root=root,
            process_environment=process_environment,
            identity_transport=identity_transport,
        )
        validate_credentialed_transport_configuration(root)
        if push:  # noqa: SIM102 - keep push-origin validation explicitly conditional
            if repository_push_origin(root) != snapshot.origin:
                raise GitSyncError("Git push origin must match the verified repository origin")
        credential_options = credential_environment((selected,), askpass)
        environment = credential_options["environment"]
        hooks = os.devnull
        branch_name = cast("str", snapshot.branch)
        remote = _step(
            root,
            "git.sync.remote",
            ("remote", "get-url", "origin"),
            environment=environment,
            secrets=selected._redaction_secrets(),
            hooks_path=hooks,
            runtime_environment=credential_options["runtime_environment"],
        )
        remote_probe = SubprocessExecutor().execute(remote)
        planned_remote_url = _text(remote_probe.stdout).strip()
        if (
            remote_probe.returncode != 0
            or _normalized_origin(planned_remote_url) != snapshot.origin
        ):
            raise GitSyncError("publication requires the verified HTTPS origin")
        authoritative_capture = AuthoritativeProbeCapture()
        authoritative = _step(
            root,
            "git.sync.authoritative-remote-sha",
            ("ls-remote", "--heads", "origin", f"refs/heads/{branch_name}"),
            environment=environment,
            secrets=selected._redaction_secrets(),
            hooks_path=hooks,
            runtime_environment=credential_options["runtime_environment"],
            private_stdout_consumer=authoritative_capture.capture,
        )
        authoritative_probe = SubprocessExecutor().execute(authoritative)
        planned_remote_sha = _planned_remote_sha(authoritative_probe, authoritative_capture)
        options: _SyncOptions = {
            "environment": environment,
            "secrets": selected._redaction_secrets(),
            "hooks_path": hooks,
            "runtime_environment": credential_options["runtime_environment"],
        }
        status = _step(root, "git.sync.status", ("status", "--porcelain=v1"), **options)
        branch = _step(
            root, "git.sync.branch", ("symbolic-ref", "--quiet", "--short", "HEAD"), **options
        )
        upstream = _step(
            root,
            "git.sync.upstream",
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"),
            **options,
        )
        fetch = _step(
            root, "git.sync.fetch", ("fetch", "origin", "--prune"), mutating=True, **options
        )
        fetched_sha = _step(
            root,
            "git.sync.fetched-sha",
            ("rev-parse", "--verify", f"refs/remotes/origin/{branch_name}"),
            **options,
        )
        integrate = _step(
            root,
            "git.sync.integrate",
            ("rebase", f"refs/remotes/origin/{branch_name}"),
            mutating=True,
            **options,
        )
        rebase = _step(root, "git.sync.rebase", ("rebase", base), mutating=True, **options)
        verify = _step(
            root,
            "git.sync.check",
            (
                "log",
                "--format=%x1e%H%x00%B%x1f",
                "--name-only",
                "--end-of-options",
                f"{base}..HEAD",
            ),
            **options,
        )
        ancestry = _step(
            root,
            "git.sync.remote-ancestry",
            ("merge-base", "--is-ancestor", f"refs/remotes/origin/{branch_name}", "HEAD"),
            **options,
        )
        fast_forward = _step(
            root,
            "git.sync.push-fast-forward",
            ("push", "origin", f"HEAD:refs/heads/{branch_name}"),
            mutating=True,
            **options,
        )
        rewritten = (
            _step(
                root,
                "git.sync.push-lease",
                (
                    "push",
                    f"--force-with-lease=refs/heads/{branch_name}:{planned_remote_sha}",
                    "origin",
                    f"HEAD:refs/heads/{branch_name}",
                ),
                mutating=True,
                **options,
            )
            if planned_remote_sha
            else None
        )
        steps = (
            status,
            branch,
            upstream,
            remote,
            authoritative,
            fetch,
            fetched_sha,
            integrate,
            rebase,
            verify,
            ancestry,
            fast_forward,
            *((rewritten,) if rewritten else ()),
        )
        sync_steps = SyncSteps(
            status=status,
            branch=branch,
            upstream=upstream,
            remote=remote,
            authoritative=authoritative,
            fetch=fetch,
            fetched_sha=fetched_sha,
            integrate=integrate,
            rebase=rebase,
            verify=verify,
            ancestry=ancestry,
            fast_forward=fast_forward,
            rewritten=rewritten,
        )

        def remote_allowed(value: str) -> bool:
            return _normalized_origin(value) == snapshot.origin

        def callback(context: RunContext[GitSyncResult]) -> GitSyncResult:
            try:
                try:
                    expected_push_origin = snapshot.origin if push else None
                    revalidate_repository(snapshot, expected_push_origin=expected_push_origin)
                    validate_credentialed_transport_configuration(root)
                except GitRepositoryChangedError:
                    context.skip_remaining()
                    raise GitSyncError(
                        "repository origin or branch changed after sync planning"
                    ) from None
                return execute_sync(
                    context,
                    sync_steps,
                    actual_branch=branch_name,
                    planned_remote_sha=planned_remote_sha,
                    resolved=base,
                    push=push,
                    instance=self._instance,
                    root=root,
                    ssh_remote=remote_allowed,
                    generic=True,
                )
            finally:
                _cleanup(options["environment"])

        return Command.create(_plan(steps), callback, steps, executor=SubprocessExecutor())
