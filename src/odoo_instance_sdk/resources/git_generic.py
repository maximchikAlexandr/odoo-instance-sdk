"""Generic GitLab repository operations shared by the public Git resource."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from odoo_instance_sdk.exceptions import (
    GitRepositoryChangedError,
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
)
from odoo_instance_sdk.internal.git_repository import (
    normalize_repository_url,
    resolve_repository,
    revalidate_repository,
)
from odoo_instance_sdk.internal.git_sync import SyncSteps, execute_sync
from odoo_instance_sdk.internal.gitlab_credentials import https_host
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


def _text(value: str | bytes | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""


def _remote_head(value: str) -> str | None:
    for line in value.splitlines():
        sha, separator, ref = line.strip().partition("\t")
        if separator and ref.startswith("refs/heads/") and _FULL_SHA.fullmatch(sha):
            return sha
    return None


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


def _cleanup() -> None:
    return None


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
            if recurse_submodules:
                for depth in range(3):
                    validate_frontier(
                        member_id,
                        root,
                        depth,
                        selected=selected,
                        user_root=user_root,
                        mapping_path=mapping_path,
                        environment_path=environment_path,
                        process_environment=process_environment,
                        identity_transport=identity_transport,
                    )
        credential_options = credential_environment(credentials, askpass)
        direct_options = frontier_environment(
            selected,
            member_id=member_id,
            user_root=user_root,
            mapping_path=mapping_path,
            environment_path=environment_path,
            include_selected=True,
        )
        nested_options = frontier_environment(
            selected,
            member_id=member_id,
            user_root=user_root,
            mapping_path=mapping_path,
            environment_path=environment_path,
            include_selected=False,
        )
        environment = credential_options["environment"]
        secrets = credential_options["secrets"]
        credential_helper = credential_options["helper"]
        hooks = os.devnull
        steps: list[PreparedStep] = []
        if not reused:
            steps.append(
                _step(
                    root.parent,
                    "git.clone",
                    ("clone", origin, str(root)),
                    environment=environment,
                    secrets=secrets,
                    hooks_path=hooks,
                    credential_helper=credential_helper,
                    mutating=True,
                )
            )
        if recurse_submodules:
            steps.extend(
                (
                    _step(
                        root,
                        "git.clone.submodule-status",
                        ("submodule", "status", "--recursive"),
                        environment=environment,
                        secrets=secrets,
                        hooks_path=hooks,
                        credential_helper=credential_helper,
                    ),
                    _step(
                        root,
                        "git.clone.submodule-update",
                        ("submodule", "update", "--init", "--"),
                        environment=direct_options["environment"],
                        secrets=direct_options["secrets"],
                        hooks_path=hooks,
                        credential_helper=direct_options["helper"],
                        mutating=True,
                    ),
                    _step(
                        root,
                        "git.clone.submodule-status.depth-0",
                        ("submodule", "status", "--recursive"),
                        environment=environment,
                        secrets=secrets,
                        hooks_path=hooks,
                        credential_helper=credential_helper,
                    ),
                    _step(
                        root,
                        "git.clone.submodule-update.depth-1",
                        ("submodule", "foreach", "git", "submodule", "update", "--init", "--"),
                        environment=nested_options["environment"],
                        secrets=nested_options["secrets"],
                        hooks_path=hooks,
                        credential_helper=nested_options["helper"],
                        mutating=True,
                    ),
                    _step(
                        root,
                        "git.clone.submodule-status.depth-1",
                        ("submodule", "status", "--recursive"),
                        environment=environment,
                        secrets=secrets,
                        hooks_path=hooks,
                        credential_helper=credential_helper,
                    ),
                    _step(
                        root,
                        "git.clone.submodule-update.depth-2",
                        (
                            "submodule",
                            "foreach",
                            "git",
                            "submodule",
                            "foreach",
                            "git",
                            "submodule",
                            "update",
                            "--init",
                            "--",
                        ),
                        environment=nested_options["environment"],
                        secrets=nested_options["secrets"],
                        hooks_path=hooks,
                        credential_helper=nested_options["helper"],
                        mutating=True,
                    ),
                    _step(
                        root,
                        "git.clone.submodule-status.depth-2",
                        ("submodule", "status", "--recursive"),
                        environment=environment,
                        secrets=secrets,
                        hooks_path=hooks,
                        credential_helper=credential_helper,
                    ),
                )
            )

        executor = SubprocessExecutor()

        def callback(context: RunContext[GitCloneResult]) -> GitCloneResult:  # noqa: C901
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
                        validate_frontier(
                            member_id,
                            root,
                            0,
                            selected=selected,
                            user_root=user_root,
                            mapping_path=mapping_path,
                            environment_path=environment_path,
                            process_environment=process_environment,
                            identity_transport=identity_transport,
                        )
                        if initialized:
                            update = cast(
                                "ProcessResult", context.process("git.clone.submodule-update")
                            )
                            if update.returncode != 0:
                                context.skip_remaining()
                                raise GitWorkflowError("submodule initialization failed")
                        else:
                            context.skip("git.clone.submodule-update")
                        for depth, (status_id, update_id, status_error) in enumerate(
                            (
                                (
                                    "git.clone.submodule-status.depth-0",
                                    "git.clone.submodule-update.depth-1",
                                    "submodule status is unavailable",
                                ),
                                (
                                    "git.clone.submodule-status.depth-1",
                                    "git.clone.submodule-update.depth-2",
                                    "submodule status is unavailable",
                                ),
                            ),
                            start=1,
                        ):
                            previous = cast("ProcessResult", context.process(status_id))
                            if previous.returncode != 0:
                                context.skip_remaining()
                                raise GitWorkflowError(status_error)
                            validate_frontier(
                                member_id,
                                root,
                                depth,
                                selected=selected,
                                user_root=user_root,
                                mapping_path=mapping_path,
                                environment_path=environment_path,
                                process_environment=process_environment,
                                identity_transport=identity_transport,
                            )
                            if _status_missing(previous.stdout):
                                nested = cast("ProcessResult", context.process(update_id))
                                if nested.returncode != 0:
                                    context.skip_remaining()
                                    raise GitWorkflowError("nested submodule initialization failed")
                            else:
                                context.skip(update_id)
                        final = cast(
                            "ProcessResult", context.process("git.clone.submodule-status.depth-2")
                        )
                        if final.returncode != 0:
                            context.skip_remaining()
                            raise GitWorkflowError("submodule status is unavailable")
                        if _status_missing(final.stdout):
                            context.skip_remaining()
                            raise GitWorkflowError(
                                "recursive submodule depth exceeds supported bound"
                            )
                    except PlanError as exc:
                        context.skip_remaining()
                        raise GitWorkflowError(str(exc)) from None
                return GitCloneResult(
                    destination=str(root),
                    origin=origin,
                    reused=reused,
                    initialized_submodules=initialized,
                )
            finally:
                _cleanup()

        return Command.create(_plan(tuple(steps)), callback, tuple(steps), executor=executor)

    def clone(
        self,
        url: str,
        destination: Path | str,
        *,
        member_id: str,
        recurse_submodules: bool = False,
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
            process_environment=process_environment,
            identity_transport=identity_transport,
        )
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
        )
        remote_probe = SubprocessExecutor().execute(remote)
        planned_remote_url = _text(remote_probe.stdout).strip()
        if remote_probe.returncode != 0 or https_host(planned_remote_url) != snapshot.host:
            raise GitSyncError("publication requires the verified HTTPS origin")
        authoritative = _step(
            root,
            "git.sync.authoritative-remote-sha",
            ("ls-remote", "--heads", "origin", f"refs/heads/{branch_name}"),
            environment=environment,
            secrets=selected._redaction_secrets(),
            hooks_path=hooks,
        )
        authoritative_probe = SubprocessExecutor().execute(authoritative)
        planned_remote_sha = _remote_head(_text(authoritative_probe.stdout))
        options: _SyncOptions = {
            "environment": environment,
            "secrets": selected._redaction_secrets(),
            "hooks_path": hooks,
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
            return https_host(value) == snapshot.host

        def callback(context: RunContext[GitSyncResult]) -> GitSyncResult:
            try:
                try:
                    revalidate_repository(snapshot)
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
                _cleanup()

        return Command.create(_plan(steps), callback, steps, executor=SubprocessExecutor())
