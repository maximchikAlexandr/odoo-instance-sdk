"""Private, typed lifecycle for credentialed Git child commands."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from odcli_multica.models import GitLabCredentialContext
from odoo_instance_sdk import Command
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


@dataclass(frozen=True, slots=True)
class GitCredentialSnapshot:
    """Immutable private host/credential state captured at command creation."""

    host: str
    credential: GitLabCredentialContext


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
