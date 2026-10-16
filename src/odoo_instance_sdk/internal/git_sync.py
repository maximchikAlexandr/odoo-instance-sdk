"""Typed state transitions for the concrete Git synchronization workflow."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast

from odoo_instance_sdk.exceptions import GitSyncError
from odoo_instance_sdk.internal.git_policy import check_log
from odoo_instance_sdk.models import GitCheckResult, GitSyncResult

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RunContext
    from odoo_instance_sdk.resources.instance import OdooInstance


@dataclass(frozen=True)
class SyncSteps:
    status: PreparedStep
    branch: PreparedStep
    upstream: PreparedStep
    remote: PreparedStep
    authoritative: PreparedStep
    fetch: PreparedStep
    fetched_sha: PreparedStep
    integrate: PreparedStep
    rebase: PreparedStep
    verify: PreparedStep
    ancestry: PreparedStep
    fast_forward: PreparedStep
    rewritten: PreparedStep | None


@dataclass(slots=True)
class AuthoritativeProbeCapture:
    """Keep only the parsed shape of one private authoritative probe."""

    observed: tuple[bool, str | None] | None = None

    def capture(self, value: str | bytes | None) -> None:
        output = _text(value)
        self.observed = (bool(output.strip()), _remote_head(output))

    def take(self, result: ProcessResult) -> tuple[bool, str | None]:
        observed = self.observed
        self.observed = None
        if observed is not None:
            return observed
        output = _text(result.stdout)
        return bool(output.strip()), _remote_head(output)

    def planned_sha(self, result: ProcessResult) -> str | None:
        if result.returncode != 0:
            raise GitSyncError(
                "authoritative remote branch probe failed; no fetch or publication was attempted"
            )
        has_output, planned = self.take(result)
        if has_output and planned is None:
            raise GitSyncError(
                "authoritative remote branch probe returned malformed output; "
                "no fetch or publication was attempted"
            )
        return planned


def _text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value if isinstance(value, str) else ""


def _remote_head(value: str) -> str | None:
    for line in value.splitlines():
        sha, separator, ref = line.strip().partition("\t")
        if separator and ref.startswith("refs/heads/") and re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            return sha
    return None


def _abort(context: RunContext[GitSyncResult], message: str) -> NoReturn:
    context.skip_remaining()
    raise GitSyncError(message)


def _capture_for_step(step: PreparedStep) -> AuthoritativeProbeCapture | None:
    owner = getattr(step.private_stdout_consumer, "__self__", None)
    return owner if isinstance(owner, AuthoritativeProbeCapture) else None


def _validate_authoritative_probe(
    context: RunContext[GitSyncResult],
    result: ProcessResult,
    *,
    planned_remote_sha: str | None,
    capture: AuthoritativeProbeCapture | None,
) -> None:
    if result.returncode != 0:
        _abort(
            context,
            "authoritative remote branch probe failed; no fetch or publication was attempted",
        )
    has_output, observed_remote_sha = (
        capture.take(result) if capture is not None else AuthoritativeProbeCapture().take(result)
    )
    if has_output and observed_remote_sha is None:
        _abort(
            context,
            "authoritative remote branch probe returned malformed output; "
            "no fetch or publication was attempted",
        )
    if planned_remote_sha is None:
        if observed_remote_sha is not None:
            _abort(context, "remote branch changed after sync planning")
    elif observed_remote_sha != planned_remote_sha:
        _abort(context, "remote branch changed after sync planning")


def _validate_initial_state(
    context: RunContext[GitSyncResult],
    steps: SyncSteps,
    *,
    actual_branch: str,
    push: bool,
    ssh_remote: Callable[[str], bool],
    generic: bool,
) -> str:
    status_result = cast("ProcessResult", context.process(steps.status.step_id))
    branch_result = cast("ProcessResult", context.process(steps.branch.step_id))
    upstream_result = cast("ProcessResult", context.process(steps.upstream.step_id))
    remote_result = cast("ProcessResult", context.process(steps.remote.step_id))
    observed_branch = _text(branch_result.stdout).strip()
    upstream_name = _text(upstream_result.stdout).strip()
    if status_result.returncode != 0 or _text(status_result.stdout).strip():
        _abort(context, "Git synchronization requires a clean worktree")
    if (
        branch_result.returncode != 0
        or observed_branch != actual_branch
        or observed_branch in {"main", "master", "develop"}
        or observed_branch.startswith("release/")
    ):
        _abort(context, "detached or protected branches cannot be synchronized")
    if remote_result.returncode != 0:
        _abort(context, "origin remote is unavailable; no fetch or publication was attempted")
    if upstream_name and upstream_name != f"origin/{observed_branch}":
        _abort(context, f"upstream must be origin/{observed_branch}")
    if generic and not ssh_remote(_text(remote_result.stdout).strip()):
        _abort(context, "repository origin changed after sync planning")
    if push and not ssh_remote(_text(remote_result.stdout).strip()):
        _abort(context, "publication requires an SSH origin")
    return observed_branch


def _capture_fetched_state(
    context: RunContext[GitSyncResult],
    steps: SyncSteps,
    *,
    planned_remote_sha: str | None,
) -> tuple[str | None, bool]:
    authoritative_result = cast("ProcessResult", context.process(steps.authoritative.step_id))
    _validate_authoritative_probe(
        context,
        authoritative_result,
        planned_remote_sha=planned_remote_sha,
        capture=_capture_for_step(steps.authoritative),
    )
    fetched = cast("ProcessResult", context.process(steps.fetch.step_id))
    if fetched.returncode != 0:
        _abort(context, "fetch failed; no rebase or publication was attempted")
    fetched_sha_result = cast("ProcessResult", context.process(steps.fetched_sha.step_id))
    remote_sha = _text(fetched_sha_result.stdout).strip() or None
    if planned_remote_sha is None:
        if fetched_sha_result.returncode == 0 and remote_sha is not None:
            _abort(context, "remote branch changed after sync planning")
        remote_sha = None
    elif fetched_sha_result.returncode != 0 or remote_sha != planned_remote_sha:
        _abort(context, "remote branch changed after sync planning")
    integrated = False
    if remote_sha is not None:
        integrated_result = cast("ProcessResult", context.process(steps.integrate.step_id))
        integrated = True
        if integrated_result.returncode != 0:
            context.skip(steps.rebase.step_id)
            raise GitSyncError(
                "rebase conflict; run `git rebase --continue` or `git rebase --abort`"
            )
    else:
        context.skip(steps.integrate.step_id)
    return remote_sha, integrated


def _validate_and_rebase(
    context: RunContext[GitSyncResult],
    steps: SyncSteps,
    *,
    resolved: str,
    branch: str,
    instance: OdooInstance,
    root: Path,
    generic: bool,
) -> tuple[ProcessResult, GitCheckResult]:
    rebased = cast("ProcessResult", context.process(steps.rebase.step_id))
    if rebased.returncode != 0:
        raise GitSyncError("rebase conflict; run `git rebase --continue` or `git rebase --abort`")
    checked = cast("ProcessResult", context.process(steps.verify.step_id))
    if generic:
        checked_result = GitCheckResult(base=resolved, branch=branch, valid=checked.returncode == 0)
    else:
        checked_result = check_log(
            _text(checked.stdout), base=resolved, branch=branch, instance=instance, root=root
        )
        if checked.returncode != 0 or not checked_result.valid:
            raise GitSyncError("git check failed before publication")
    if not checked_result.valid:
        raise GitSyncError("git check failed before publication")
    return rebased, checked_result


def _publish_transition(
    context: RunContext[GitSyncResult],
    steps: SyncSteps,
    *,
    push: bool,
    remote_sha: str | None,
) -> bool:
    if not push:
        context.skip(steps.ancestry.step_id)
        context.skip(steps.fast_forward.step_id)
        if steps.rewritten is not None:
            context.skip(steps.rewritten.step_id)
        return False
    if remote_sha is None:
        context.skip(steps.ancestry.step_id)
        if steps.rewritten is not None:
            context.skip(steps.rewritten.step_id)
        published_result = cast("ProcessResult", context.process(steps.fast_forward.step_id))
    else:
        ancestry_result = cast("ProcessResult", context.process(steps.ancestry.step_id))
        if ancestry_result.returncode == 0:
            if steps.rewritten is not None:
                context.skip(steps.rewritten.step_id)
            published_result = cast("ProcessResult", context.process(steps.fast_forward.step_id))
        else:
            if steps.rewritten is None:
                context.skip(steps.fast_forward.step_id)
                raise GitSyncError("rewritten publication has no fetched lease")
            context.skip(steps.fast_forward.step_id)
            published_result = cast("ProcessResult", context.process(steps.rewritten.step_id))
    if published_result.returncode != 0:
        raise GitSyncError("publication failed; a stale lease was not retried")
    return True


def execute_sync(
    context: RunContext[GitSyncResult],
    steps: SyncSteps,
    *,
    actual_branch: str,
    planned_remote_sha: str | None,
    resolved: str,
    push: bool,
    instance: OdooInstance,
    root: Path,
    ssh_remote: Callable[[str], bool],
    generic: bool = False,
) -> GitSyncResult:
    """Run captured Git sync phases and return the typed public result."""
    observed_branch = _validate_initial_state(
        context,
        steps,
        actual_branch=actual_branch,
        push=push,
        ssh_remote=ssh_remote,
        generic=generic,
    )
    remote_sha, integrated = _capture_fetched_state(
        context, steps, planned_remote_sha=planned_remote_sha
    )
    rebased, _checked = _validate_and_rebase(
        context,
        steps,
        resolved=resolved,
        branch=observed_branch,
        instance=instance,
        root=root,
        generic=generic,
    )
    published = _publish_transition(context, steps, push=push, remote_sha=remote_sha)
    return GitSyncResult(
        branch=observed_branch,
        base=resolved,
        fetched_sha=remote_sha,
        rebased=rebased.returncode == 0 or integrated,
        pushed=published,
    )


__all__ = [
    "AuthoritativeProbeCapture",
    "SyncSteps",
    "execute_sync",
]
