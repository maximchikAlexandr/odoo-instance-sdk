"""Typed Git command adapters and lifecycle for credentialed child commands."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar, cast

import msgspec

from odcli_multica.models import (
    ContextVerificationError,
    GitLabCredentialContext,
    RootCreatorContext,
    VerifiedTaskContext,
)
from odoo_instance_sdk import Command
from odoo_instance_sdk.commands.output import action_command
from odoo_instance_sdk.internal.proc import PreparedAction, RunContext, prepared_command
from odoo_instance_sdk.models import CommandResult, GitSyncResult

T_co = TypeVar("T_co", covariant=True)


class Runnable(Protocol[T_co]):
    def run(self) -> T_co: ...


class GitPassthrough(Protocol):
    def passthrough_command(
        self,
        args: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        secret_values: Sequence[str] = (),
        hooks_path: str | None = None,
    ) -> Command[CommandResult]: ...


class GitSync(Protocol):
    def sync_command(
        self,
        *,
        base: str | None = None,
        push: bool = False,
        environment: Mapping[str, str] | None = None,
        secret_values: Sequence[str] = (),
        remote_allowed: Callable[[str], bool] | None = None,
        hooks_path: str | None = None,
    ) -> Command[GitSyncResult]: ...


class GitResourceLike(GitPassthrough, GitSync, Protocol):
    def passthrough(self, args: Sequence[str]) -> CommandResult: ...

    def commit_command(
        self,
        *,
        description: str,
        ticket: str | None = None,
        tag: str | None = None,
        member_id: str | None = None,
    ) -> Command[CommandResult]: ...


@dataclass(frozen=True, slots=True)
class GitCredentialSnapshot:
    """Immutable private host/credential state captured at command creation."""

    host: str
    credential: GitLabCredentialContext


def root_creator_command(
    context: VerifiedTaskContext,
    require_contract: Callable[[], None],
    resolve_root_creator: Callable[[VerifiedTaskContext], RootCreatorContext],
) -> Command[RootCreatorContext]:
    """Build the verified root-creator observation used by commit commands."""
    require_contract()
    return cast(
        "Command[RootCreatorContext]",
        action_command(
            "multica.root-creator",
            lambda: resolve_root_creator(context),
            description="Resolve the human creator of the verified task root issue",
        ),
    )


def project_commit_command(
    context: VerifiedTaskContext,
    *,
    git_resource: GitResourceLike,
    description: str,
    ticket: str | None,
    tag: str | None,
    root_creator: Callable[[VerifiedTaskContext], RootCreatorContext],
    revalidate_root_creator: Callable[[VerifiedTaskContext], RootCreatorContext],
) -> Command[CommandResult]:
    """Capture a project commit and revalidate its verified human before execution."""
    root = root_creator(context)
    if (
        root.workspace_id != context.workspace_id
        or (context.root_issue_id is not None and root.root_issue_id != context.root_issue_id)
        or (context.root_creator_id is not None and root.root_creator_id != context.root_creator_id)
    ):
        raise ContextVerificationError("verified task root identity changed")
    commit_command = git_resource.commit_command(
        description=description,
        ticket=ticket,
        tag=tag,
        member_id=root.root_creator_id,
    )
    revalidate = PreparedAction(
        step_id="multica.commit.revalidate-root-creator",
        action="multica.commit.revalidate-root-creator",
        description="Revalidate the verified task root before committing",
        read_only=True,
        details={
            "workspace_id": context.workspace_id,
            "root_issue_id": context.root_issue_id,
            "root_creator_id": context.root_creator_id,
        },
    )
    prepared = commit_command._prepared()
    steps = (revalidate, *prepared.steps)

    def run_commit(run_context: RunContext[CommandResult]) -> CommandResult:
        run_context.action(revalidate.step_id)
        current = revalidate_root_creator(context)
        if (
            current.workspace_id != context.workspace_id
            or current.root_issue_id != context.root_issue_id
            or current.root_creator_id != context.root_creator_id
            or current.root_issue_id != root.root_issue_id
            or current.root_creator_id != root.root_creator_id
        ):
            raise ContextVerificationError("verified task root identity changed")
        run_context.complete_action(revalidate.step_id)
        return prepared.callback(run_context)

    return Command.from_prepared(
        msgspec.structs.replace(
            commit_command.plan,
            steps=tuple(step.public_projection() for step in steps),
            fingerprint="",
        ).with_fingerprint(),
        prepared_command(
            run_commit,
            steps,
            executor=prepared.executor,
            private_projection=prepared.private_projection,
        ),
    )


def write_askpass_channel(credential: GitLabCredentialContext) -> str:
    """Materialize one owner-only helper channel for exactly one execution."""
    fd, path = tempfile.mkstemp(prefix="odcli-gitlab-", suffix=".askpass")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as channel:
            json.dump(
                {
                    "host": credential.host,
                    "login": credential.login,
                    "token": credential._token,
                },
                channel,
                separators=(",", ":"),
            )
            channel.write("\n")
        os.chmod(path, 0o600)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise
    return path


def cleanup_secret_channel(path: str | None) -> None:
    if path is not None:
        with contextlib.suppress(OSError):
            os.unlink(path)


def cleanup_hooks_path(path: str | None) -> None:
    if path is not None:
        with contextlib.suppress(OSError):
            os.rmdir(path)


def execute_child(
    snapshot: GitCredentialSnapshot | None,
    *,
    verify_transport: Callable[[], None],
    askpass: Callable[[], str],
    build_child: Callable[[Mapping[str, str], Sequence[str], str | None], Runnable[T_co]],
) -> T_co:
    """Verify, materialize, execute, and clean one credentialed child.

    The snapshot is private state captured before the public command is
    returned.  Transport is still checked immediately before the channel and
    child are created, closing the prepare/run race without persisting a
    channel for a never-run command.
    """
    channel_path: str | None = None
    hooks_path: str | None = None
    try:
        environment: Mapping[str, str] = {}
        secret_values: tuple[str, ...] = ()
        if snapshot is not None:
            verify_transport()
            channel_path = write_askpass_channel(snapshot.credential)
            hooks_path = tempfile.mkdtemp(prefix="odcli-git-hooks-")
            environment = snapshot.credential.askpass_environment(
                askpass(), channel_path=channel_path
            )
            secret_values = snapshot.credential._redaction_secrets()
        child = build_child(environment, secret_values, hooks_path)
        return child.run()
    finally:
        cleanup_secret_channel(channel_path)
        cleanup_hooks_path(hooks_path)


__all__ = [
    "GitCredentialSnapshot",
    "GitPassthrough",
    "GitResourceLike",
    "GitSync",
    "Runnable",
    "cleanup_hooks_path",
    "cleanup_secret_channel",
    "execute_child",
    "write_askpass_channel",
]
