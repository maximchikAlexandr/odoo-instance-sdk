"""Concrete, tracker-neutral Git workflow operations."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    GitAbsorbNotFoundError,
    GitCheckFailedError,
    GitScopeError,
    GitSyncError,
    GitWorkflowError,
    PlanValidationError,
)
from odoo_instance_sdk.internal.executables import resolve_optional_executable
from odoo_instance_sdk.internal.git_policy import (
    check_log as _check_log,
)
from odoo_instance_sdk.internal.git_policy import (
    module_scope as _module_scope,
)
from odoo_instance_sdk.internal.git_policy import (
    semantic_tag as _semantic_tag,
)
from odoo_instance_sdk.internal.git_policy import (
    ticket as _ticket,
)
from odoo_instance_sdk.internal.git_sync import SyncSteps, execute_sync
from odoo_instance_sdk.models import (
    CommandResult,
    GitAbsorbResult,
    GitCheckIssue,
    GitCheckResult,
    GitCommitContext,
    GitSyncResult,
)
from odoo_instance_sdk.project import ProjectConfig, effective_ticket_settings

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessResult,
        RunContext,
    )
    from odoo_instance_sdk.project import TicketLinkSettings
    from odoo_instance_sdk.resources.instance import OdooInstance


_TICKET = re.compile(r"(?<![A-Za-z])([A-Z][A-Z0-9]+-\d+)(?:_\d+)?(?![A-Za-z0-9])")
_PROTECTED = {"main", "master", "develop"}
_ALLOWED_TAGS = frozenset(
    {"ADD", "DEL", "PORT", "I18N", "UI", "TEST", "DOC", "CI", "IMP", "FIX", "REF"}
)
_FULL_SHA = re.compile(r"[0-9a-fA-F]{40}")


def _worktree(instance: OdooInstance) -> Path:
    value = getattr(instance.config, "default_cwd", None)
    if value is None:
        start = getattr(instance.config, "start_config", None)
        config_path = getattr(start, "config_path", None)
        value = Path(config_path).parent if config_path else Path.cwd()
    root = Path(value).resolve()
    if not root.is_dir():
        raise GitWorkflowError(f"Git worktree is unavailable: {root}")
    return root


def _text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value if isinstance(value, str) else ""


def _command_result(value: ProcessResult, step: PreparedStep) -> CommandResult:
    return CommandResult(
        args=list(step.public_projection().argv),
        returncode=value.returncode,
        stdout=_text(value.stdout),
        stderr=_text(value.stderr),
        duration=value.duration,
        cwd=value.cwd,
        environment=step.public_projection().environment_overrides,
    )


def _step(
    root: Path,
    step_id: str,
    args: Sequence[str],
    *,
    mutating: bool = False,
) -> PreparedStep:
    from odoo_instance_sdk.internal.proc import PreparedStep

    return PreparedStep(
        step_id=step_id,
        argv=("git", "-C", str(root), *tuple(args)),
        cwd=str(root),
        read_only=not mutating,
        mutating=mutating,
        text=True,
    )


def _plan(
    steps: Sequence[PreparedStep | PreparedAction],
    *,
    observations: Sequence[JsonValue] = (),
) -> ExecutionPlan:
    from odoo_instance_sdk.execution import ExecutionPlan

    return ExecutionPlan(
        steps=tuple(step.public_projection() for step in steps),
        observations=tuple(observations),
    ).with_fingerprint()


def _planning_observation(scope: str, steps: Sequence[PreparedStep]) -> JsonValue:
    return cast(
        "JsonValue",
        {
            "kind": "planning-inspection",
            "scope": scope,
            "step_ids": [step.step_id for step in steps],
            "budget_seconds": 30.0,
            "read_only": True,
            "executed_during_planning": True,
        },
    )


def _capture_probe(step: PreparedStep, *, allow_failure: bool = False) -> ProcessResult:
    """Capture a read-only planning probe; the same step is retained in the plan."""
    from odoo_instance_sdk.internal.proc import SubprocessExecutor

    result = SubprocessExecutor().execute(step)
    if result.returncode != 0 and not allow_failure:
        raise GitWorkflowError(f"Git planning probe failed: {step.step_id}")
    return result


def _remote_head(value: str) -> str | None:
    """Decode the exact branch head returned by ``git ls-remote``."""
    for line in value.splitlines():
        sha, separator, ref = line.strip().partition("\t")
        if separator and ref.startswith("refs/heads/") and re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            return sha
    return None


def _validate_description(description: str) -> str:
    description = description.strip()
    if not description or "\n" in description or "\r" in description:
        raise PlanValidationError("description must be one non-empty line")
    try:
        description.encode("ascii")
    except UnicodeEncodeError as exc:
        raise PlanValidationError("description must be English text") from exc
    return description


def _commit_context_from_results(
    instance: OdooInstance,
    root: Path,
    *,
    description: str,
    ticket: str | None,
    tag: str | None,
    staged_result: ProcessResult,
    branch_result: ProcessResult,
    status_result: ProcessResult,
    tree_result: ProcessResult,
) -> GitCommitContext:
    entries = _staged(_text(staged_result.stdout))
    if not entries:
        raise GitScopeError("git commit requires staged changes")
    paths = _entry_paths(entries)
    module, outside = _module_scope(instance, root, paths)
    resolved_ticket = _ticket(ticket)
    branch_name = _text(branch_result.stdout).strip()
    if resolved_ticket is None:
        resolved_ticket = _ticket_from_branch(_registered_environment_branch(instance) or "")
    if resolved_ticket is None:
        resolved_ticket = _ticket_from_branch(branch_name)
    settings = _project_settings(root)
    if settings is not None and settings.enabled and resolved_ticket is None:
        raise PlanValidationError("ticket link is enabled but no Ticket was resolved")
    link = None
    if resolved_ticket is not None and settings is not None and settings.enabled:
        if not settings.base_url:
            raise PlanValidationError("ticket link is enabled but ticket_base_url is missing")
        link = settings.base_url.rstrip("/") + "/" + resolved_ticket
    resolved_tag = (
        tag.upper()
        if tag is not None
        else _semantic_tag(paths, tuple(status for status, _ in entries), outside=outside)
    )
    if resolved_tag not in _ALLOWED_TAGS:
        raise PlanValidationError("tag must be one of the supported commit prefixes")
    first = f"[{resolved_tag}] {module}: "
    if resolved_ticket is not None:
        first += resolved_ticket + " "
    message = first + description
    if link is not None:
        message += "\n\n" + link
    return GitCommitContext(
        module=module,
        tag=resolved_tag,
        description=description,
        ticket=resolved_ticket,
        ticket_link=link,
        message=message,
        staged_paths=paths,
        branch=branch_name,
        repository=root.name,
        command=("git", "commit", "-m", message),
        staged_entries=entries,
        unrelated_paths=_status_paths(_text(status_result.stdout), staged_paths=paths),
        index_tree=_text(tree_result.stdout).strip(),
    )


def _staged(value: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    tokens = value.split("\0")
    result: list[tuple[str, tuple[str, ...]]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if not status:
            continue
        if index >= len(tokens):
            break
        paths = [tokens[index]]
        index += 1
        if status[0] in "RC" and index < len(tokens):
            paths.append(tokens[index])
            index += 1
        result.append((status[0], tuple(paths)))
    return tuple(result)


def _entry_paths(entries: Sequence[tuple[str, tuple[str, ...]]]) -> tuple[str, ...]:
    return tuple(path for _, paths in entries for path in paths)


def _status_paths(value: str, *, staged_paths: Sequence[str]) -> tuple[str, ...]:
    staged = set(staged_paths)
    unrelated: list[str] = []
    for raw in value.splitlines():
        if len(raw) < 4:
            continue
        state, path = raw[:2], raw[3:]
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if state[1] != " " or path not in staged:
            unrelated.append(path)
    return tuple(dict.fromkeys(unrelated))


def _unmapped_lines(*values: str) -> tuple[str, ...]:
    lines = [
        line.strip()
        for value in values
        for line in value.splitlines()
        if line.strip() and ("unmapped" in line.lower() or "not mapped" in line.lower())
    ]
    return tuple(dict.fromkeys(lines))


def _ticket_from_branch(branch: str) -> str | None:
    match = _TICKET.search(branch)
    return match.group(1) if match else None


def _registered_environment_branch(instance: OdooInstance) -> str | None:
    environment_id = getattr(instance, "_environment_id", None)
    if not environment_id:
        return None
    try:
        row = instance._client.get_catalog().get_environment(str(environment_id))
    except Exception:
        return None
    if row is None:
        return None
    branch = row["branch"]
    return branch.strip() if isinstance(branch, str) and branch.strip() else None


def _project_settings(root: Path) -> TicketLinkSettings | None:
    try:
        project = ProjectConfig.load(root)
    except Exception:
        return None
    return effective_ticket_settings(project)


def _base_ref(instance: OdooInstance, requested: str | None) -> str:  # noqa: C901
    if requested and requested.strip():
        candidate = requested.strip()
        if candidate.startswith("-"):
            raise GitCheckFailedError("Git base ref must not start with '-'")
        return candidate
    environment_id = getattr(instance, "_environment_id", None)
    if environment_id:
        try:
            row = instance._client.get_catalog().get_environment(str(environment_id))
        except Exception:
            row = None
        if row is not None:
            base_value = row["base_ref"]
            if isinstance(base_value, str) and base_value.strip():
                candidate = base_value.strip()
                if candidate.startswith("-"):
                    raise GitCheckFailedError("Git base ref must not start with '-'")
                return candidate
    try:
        project = ProjectConfig.load(_worktree(instance))
    except Exception:
        project = None
    if project is not None and project.default_base_ref:
        if project.default_base_ref.startswith("-"):
            raise GitCheckFailedError("Git base ref must not start with '-'")
        return project.default_base_ref
    raise GitCheckFailedError(
        "no Git base configured; pass --base or configure an environment/project base"
    )


def _verified_base_ref(root: Path, candidate: str) -> str:
    """Resolve a configured base before interpolating it into Git revisions."""
    if candidate.startswith("-"):
        raise GitCheckFailedError("Git base ref must not start with '-'")
    probe = _step(
        root,
        "git.base.verify",
        ("rev-parse", "--verify", "--end-of-options", f"{candidate}^{{commit}}"),
    )
    result = _capture_probe(probe, allow_failure=True)
    values = _text(result.stdout).splitlines()
    if result.returncode != 0 or len(values) != 1 or _FULL_SHA.fullmatch(values[0]) is None:
        raise GitCheckFailedError(f"Git base ref is unresolved: {candidate}")
    return values[0]


class GitResource:
    """One concrete Git resource bound to an :class:`OdooInstance`."""

    def __init__(self, instance: OdooInstance) -> None:
        self._instance = instance

    def commit_context_command(
        self, description: str, *, ticket: str | None = None, tag: str | None = None
    ) -> Command[GitCommitContext]:
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        description = _validate_description(description)
        root = _worktree(self._instance)
        staged = _step(root, "git.commit.staged", ("diff", "--cached", "--name-status", "-z"))
        branch = _step(root, "git.commit.branch", ("symbolic-ref", "--quiet", "--short", "HEAD"))
        status = _step(root, "git.commit.status", ("status", "--porcelain=v1"))
        tree = _step(root, "git.commit.index-tree", ("write-tree",))

        def callback(context: RunContext[GitCommitContext]) -> GitCommitContext:
            staged_result = cast("ProcessResult", context.process(staged.step_id))
            branch_result = cast("ProcessResult", context.process(branch.step_id))
            status_result = cast("ProcessResult", context.process(status.step_id))
            tree_result = cast("ProcessResult", context.process(tree.step_id))
            if (
                staged_result.returncode != 0
                or branch_result.returncode != 0
                or status_result.returncode != 0
                or tree_result.returncode != 0
            ):
                raise GitWorkflowError("unable to inspect the staged Git context")
            return _commit_context_from_results(
                self._instance,
                root,
                description=description,
                ticket=ticket,
                tag=tag,
                staged_result=staged_result,
                branch_result=branch_result,
                status_result=status_result,
                tree_result=tree_result,
            )

        return Command.create(
            _plan((staged, branch, status, tree)),
            callback,
            (staged, branch, status, tree),
            executor=SubprocessExecutor(),
        )

    def commit_context(
        self, description: str, *, ticket: str | None = None, tag: str | None = None
    ) -> GitCommitContext:
        return self.commit_context_command(description, ticket=ticket, tag=tag).run()

    def commit_command(
        self,
        context: GitCommitContext | None = None,
        *,
        description: str | None = None,
        ticket: str | None = None,
        tag: str | None = None,
    ) -> Command[CommandResult]:
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import PreparedStep, SubprocessExecutor

        planning_observations: tuple[JsonValue, ...] = ()
        if context is None:
            if description is None:
                raise PlanValidationError("commit description is required")
            planning_command = self.commit_context_command(description, ticket=ticket, tag=tag)
            context = planning_command.run()
            capture_steps = tuple(
                step
                for step in planning_command._prepared().steps
                if isinstance(step, PreparedStep)
            )
            planning_observations = (_planning_observation("git.commit", capture_steps),)
        else:
            root = _worktree(self._instance)
            staged = _step(root, "git.commit.staged", ("diff", "--cached", "--name-status", "-z"))
            branch = _step(
                root, "git.commit.branch", ("symbolic-ref", "--quiet", "--short", "HEAD")
            )
            status = _step(root, "git.commit.status", ("status", "--porcelain=v1"))
            tree = _step(root, "git.commit.index-tree", ("write-tree",))
            capture_steps = (staged, branch, status, tree)
        root = _worktree(self._instance)
        staged, branch, status, tree = capture_steps
        verify = _step(
            root, "git.commit.verify-staged", ("diff", "--cached", "--name-status", "-z")
        )
        verify_tree = _step(root, "git.commit.verify-index-tree", ("write-tree",))
        commit_step = _step(root, "git.commit", ("commit", "-m", context.message), mutating=True)
        all_steps = (
            *capture_steps,
            verify,
            verify_tree,
            commit_step,
        )

        def callback(run: RunContext[CommandResult]) -> CommandResult:
            captured = cast("ProcessResult", run.process(staged.step_id))
            captured_branch = cast("ProcessResult", run.process(branch.step_id))
            captured_status = cast("ProcessResult", run.process(status.step_id))
            captured_tree = cast("ProcessResult", run.process(tree.step_id))
            if (
                captured.returncode != 0
                or captured_branch.returncode != 0
                or captured_status.returncode != 0
                or captured_tree.returncode != 0
            ):
                run.skip(verify.step_id)
                run.skip(verify_tree.step_id)
                run.skip(commit_step.step_id)
                raise GitWorkflowError("unable to revalidate the staged Git context")
            captured_entries = _staged(_text(captured.stdout))
            planned = context
            if planned is None:
                planned = _commit_context_from_results(
                    self._instance,
                    root,
                    description=cast("str", description),
                    ticket=ticket,
                    tag=tag,
                    staged_result=captured,
                    branch_result=captured_branch,
                    status_result=captured_status,
                    tree_result=captured_tree,
                )
            if (
                captured_entries != planned.staged_entries
                or _text(captured_branch.stdout).strip() != planned.branch
                or _text(captured_tree.stdout).strip() != planned.index_tree
            ):
                run.skip(verify.step_id)
                run.skip(verify_tree.step_id)
                run.skip(commit_step.step_id)
                raise GitWorkflowError("staged changes changed after commit planning")
            check = cast("ProcessResult", run.process(verify.step_id))
            check_tree = cast("ProcessResult", run.process(verify_tree.step_id))
            if (
                _staged(_text(check.stdout)) != planned.staged_entries
                or check_tree.returncode != 0
                or _text(check_tree.stdout).strip() != planned.index_tree
            ):
                run.skip(commit_step.step_id)
                raise GitWorkflowError("staged changes changed after commit planning")
            result = cast("ProcessResult", run.process(commit_step.step_id))
            if result.returncode != 0:
                raise GitWorkflowError(
                    "git commit hook or commit failed"
                    + (f": {_text(result.stderr).strip()}" if _text(result.stderr).strip() else "")
                )
            return _command_result(result, commit_step)

        return Command.create(
            _plan(all_steps, observations=planning_observations),
            callback,
            all_steps,
            executor=SubprocessExecutor(),
        )

    def commit(
        self, description: str, *, ticket: str | None = None, tag: str | None = None
    ) -> CommandResult:
        return self.commit_command(description=description, ticket=ticket, tag=tag).run()

    def check_command(self, *, base: str | None = None) -> Command[GitCheckResult]:
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        root = _worktree(self._instance)
        resolved = _base_ref(self._instance, base)
        verified = _verified_base_ref(root, resolved)
        log = _step(
            root,
            "git.check.log",
            (
                "log",
                "--format=%x1e%H%x00%B%x1f",
                "--name-only",
                "--end-of-options",
                f"{verified}..HEAD",
            ),
        )
        branch = _step(root, "git.check.branch", ("symbolic-ref", "--quiet", "--short", "HEAD"))
        dirty = _step(root, "git.check.dirty", ("status", "--porcelain=v1"))

        def callback(context: RunContext[GitCheckResult]) -> GitCheckResult:
            log_result = cast("ProcessResult", context.process(log.step_id))
            branch_result = cast("ProcessResult", context.process(branch.step_id))
            dirty_result = cast("ProcessResult", context.process(dirty.step_id))
            branch_name = _text(branch_result.stdout).strip() or "detached"
            result = _check_log(
                _text(log_result.stdout),
                base=resolved,
                branch=branch_name,
                instance=self._instance,
                root=root,
            )
            issues = list(result.issues)
            if log_result.returncode != 0:
                issues.append(
                    GitCheckIssue(
                        code="base_unresolved", message=f"base ref is unavailable: {resolved}"
                    )
                )
            if branch_result.returncode != 0 or branch_name == "detached":
                issues.append(GitCheckIssue(code="detached", message="HEAD is detached"))
            if branch_name in _PROTECTED or branch_name.startswith("release/"):
                issues.append(
                    GitCheckIssue(
                        code="protected_branch", message=f"branch is protected: {branch_name}"
                    )
                )
            if _text(dirty_result.stdout).strip():
                issues.append(
                    GitCheckIssue(code="dirty", message="worktree has uncommitted changes")
                )
            return msgspec.structs.replace(result, valid=not issues, issues=tuple(issues))

        return Command.create(
            _plan((log, branch, dirty)),
            callback,
            (log, branch, dirty),
            executor=SubprocessExecutor(),
        )

    def check(self, *, base: str | None = None) -> GitCheckResult:
        return self.check_command(base=base).run()

    def absorb_command(
        self, *, base: str | None = None, dry_run: bool = False, and_rebase: bool = False
    ) -> Command[GitAbsorbResult]:
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        capability = resolve_optional_executable("git-absorb")
        if capability.path is None:
            raise GitAbsorbNotFoundError()
        root = _worktree(self._instance)
        resolved = _base_ref(self._instance, base)
        verified = _verified_base_ref(root, resolved)
        args = ["-C", str(root), "--base", verified]
        if dry_run:
            args.append("--dry-run")
        if and_rebase:
            args.append("--and-rebase")
        from odoo_instance_sdk.internal.proc import PreparedStep

        step = PreparedStep(
            step_id="git.absorb",
            argv=(capability.path, *args),
            cwd=str(root),
            read_only=dry_run,
            mutating=not dry_run,
            text=True,
        )

        def callback(context: RunContext[GitAbsorbResult]) -> GitAbsorbResult:
            result = cast("ProcessResult", context.process(step.step_id))
            output = _text(result.stdout)
            stderr = _text(result.stderr)
            unmapped = _unmapped_lines(output, stderr)
            if result.returncode != 0:
                raise GitWorkflowError(
                    "git-absorb failed" + (f": {stderr.strip()}" if stderr.strip() else "")
                )
            return GitAbsorbResult(
                executable=capability.path or "git-absorb",
                base=resolved,
                returncode=result.returncode,
                stdout=output,
                stderr=stderr,
                unmapped_hunks=unmapped,
            )

        return Command.create(_plan((step,)), callback, (step,), executor=SubprocessExecutor())

    def absorb(
        self, *, base: str | None = None, dry_run: bool = False, and_rebase: bool = False
    ) -> GitAbsorbResult:
        return self.absorb_command(base=base, dry_run=dry_run, and_rebase=and_rebase).run()

    def sync_command(
        self, *, base: str | None = None, push: bool = False
    ) -> Command[GitSyncResult]:
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        root = _worktree(self._instance)
        resolved = _base_ref(self._instance, base)
        status = _step(root, "git.sync.status", ("status", "--porcelain=v1"))
        branch = _step(root, "git.sync.branch", ("symbolic-ref", "--quiet", "--short", "HEAD"))
        branch_probe = _capture_probe(branch)
        actual_branch = _text(branch_probe.stdout).strip()
        if not actual_branch:
            raise GitSyncError("detached or protected branches cannot be synchronized")
        upstream = _step(
            root, "git.sync.upstream", ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        )
        remote = _step(root, "git.sync.remote", ("remote", "get-url", "origin"))
        remote_probe = _capture_probe(remote, allow_failure=True)
        planned_remote_url = _text(remote_probe.stdout).strip()
        authoritative = _step(
            root,
            "git.sync.authoritative-remote-sha",
            ("ls-remote", "--heads", "origin", f"refs/heads/{actual_branch}"),
        )
        authoritative_planned = remote_probe.returncode == 0 and not (
            push and not _ssh_remote(planned_remote_url)
        )
        if not authoritative_planned:
            planned_remote_sha = None
        else:
            authoritative_probe = _capture_probe(authoritative, allow_failure=True)
            planned_remote_sha = _remote_head(_text(authoritative_probe.stdout))
        fetch = _step(root, "git.sync.fetch", ("fetch", "origin", "--prune"), mutating=True)
        fetched_sha = _step(
            root,
            "git.sync.fetched-sha",
            ("rev-parse", "--verify", f"refs/remotes/origin/{actual_branch}"),
        )
        integrate = _step(
            root,
            "git.sync.integrate",
            ("rebase", f"refs/remotes/origin/{actual_branch}"),
            mutating=True,
        )
        rebase = _step(root, "git.sync.rebase", ("rebase", resolved), mutating=True)
        verify = _step(
            root,
            "git.sync.check",
            (
                "log",
                "--format=%x1e%H%x00%B%x1f",
                "--name-only",
                "--end-of-options",
                f"{resolved}..HEAD",
            ),
        )
        ancestry = _step(
            root,
            "git.sync.remote-ancestry",
            (
                "merge-base",
                "--is-ancestor",
                f"refs/remotes/origin/{actual_branch}",
                "HEAD",
            ),
        )
        fast_forward = _step(
            root,
            "git.sync.push-fast-forward",
            ("push", "origin", f"HEAD:refs/heads/{actual_branch}"),
            mutating=True,
        )
        rewritten = (
            _step(
                root,
                "git.sync.push-lease",
                (
                    "push",
                    f"--force-with-lease=refs/heads/{actual_branch}:{planned_remote_sha}",
                    "origin",
                    f"HEAD:refs/heads/{actual_branch}",
                ),
                mutating=True,
            )
            if planned_remote_sha is not None
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
            *((rewritten,) if rewritten is not None else ()),
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

        def callback(context: RunContext[GitSyncResult]) -> GitSyncResult:
            return execute_sync(
                context,
                sync_steps,
                actual_branch=actual_branch,
                planned_remote_sha=planned_remote_sha,
                resolved=resolved,
                push=push,
                instance=self._instance,
                root=root,
                ssh_remote=_ssh_remote,
            )

        planning_steps = (
            (branch, remote, authoritative) if authoritative_planned else (branch, remote)
        )
        planning_observations = (_planning_observation("git.sync", planning_steps),)
        return Command.create(
            _plan(steps, observations=planning_observations),
            callback,
            steps,
            executor=SubprocessExecutor(),
        )

    def sync(self, *, base: str | None = None, push: bool = False) -> GitSyncResult:
        return self.sync_command(base=base, push=push).run()


def _ssh_remote(value: str) -> bool:
    return value.startswith(("git@", "ssh://"))


__all__ = [
    "GitAbsorbResult",
    "GitCheckIssue",
    "GitCheckResult",
    "GitCommitContext",
    "GitResource",
    "GitSyncResult",
]
