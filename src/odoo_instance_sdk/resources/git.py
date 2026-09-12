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
_MESSAGE = re.compile(r"^\[(?P<tag>[A-Z][A-Z0-9_-]*)\] (?P<module>[^:]+): .+$")
_SUBJECT_TICKET = re.compile(r"^(?P<ticket>[A-Z][A-Z0-9]+-\d+)(?:_\d+)?(?:\s+|$)")
_PROTECTED = {"main", "master", "develop"}
_ALLOWED_TAGS = frozenset(
    {"ADD", "DEL", "PORT", "I18N", "UI", "TEST", "DOC", "CI", "IMP", "FIX", "REF"}
)
_SEMANTIC_DIRS = {
    "i18n": "I18N",
    "static": "UI",
    "tests": "TEST",
    "docs": "DOC",
    ".github": "CI",
    ".gitlab": "CI",
    "migrations": "PORT",
}


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


def _ticket(value: str | None) -> str | None:
    if value is None:
        return None
    match = _TICKET.fullmatch(value.strip())
    if match is None:
        raise PlanValidationError("ticket must match the configured uppercase key format")
    return match.group(1)


def _safe_ticket(value: str | None) -> str | None:
    """Extract a history ticket without turning malformed history into a plan error."""
    if value is None:
        return None
    match = _TICKET.fullmatch(value.strip())
    return match.group(1) if match is not None else None


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


def _semantic_tag(paths: Sequence[str], statuses: Sequence[str], *, outside: bool) -> str:
    if statuses and all(status == "A" for status in statuses):
        return "ADD"
    if statuses and all(status == "D" for status in statuses):
        return "DEL"
    if any("migrations" in Path(path).parts for path in paths):
        return "PORT"
    buckets = {_SEMANTIC_DIRS.get(part) for path in paths for part in Path(path).parts}
    buckets.discard(None)
    if outside and buckets:
        if "DOC" in buckets:
            return "DOC"
        if buckets == {"CI"}:
            return "CI"
    if len(buckets) == 1:
        return cast("str", next(iter(buckets)))
    return "IMP" if not outside else ("DOC" if "DOC" in buckets else "CI" if buckets else "IMP")


def _module_scope(instance: OdooInstance, root: Path, paths: Sequence[str]) -> tuple[str, bool]:
    modules = getattr(getattr(instance, "modules", None), "catalogue", lambda: ())()
    matches: set[str] = set()
    path_matches: list[set[str]] = []
    for raw in paths:
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts:
            raise GitScopeError(f"staged path is outside the repository: {raw}")
        current: set[str] = set()
        for module in modules:
            try:
                module_root = Path(module.path).resolve().relative_to(root.resolve())
            except (ValueError, OSError):
                continue
            if path == module_root or module_root in path.parents:
                matches.add(module.name)
                current.add(module.name)
        path_matches.append(current)
    if len(matches) > 1:
        raise GitScopeError("staged changes span multiple Odoo modules")
    if matches:
        if any(not current for current in path_matches):
            raise GitScopeError("staged changes mix an Odoo module with repository files")
        return next(iter(matches)), False
    return root.name, True


def _project_settings(root: Path) -> TicketLinkSettings | None:
    try:
        project = ProjectConfig.load(root)
    except Exception:
        return None
    return effective_ticket_settings(project)


def _base_ref(instance: OdooInstance, requested: str | None) -> str:
    if requested and requested.strip():
        return requested.strip()
    environment_id = getattr(instance, "_environment_id", None)
    if environment_id:
        try:
            row = instance._client.get_catalog().get_environment(str(environment_id))
        except Exception:
            row = None
        if row is not None:
            base_value = row["base_ref"]
            if isinstance(base_value, str) and base_value.strip():
                return base_value.strip()
    try:
        project = ProjectConfig.load(_worktree(instance))
    except Exception:
        project = None
    if project is not None and project.default_base_ref:
        return project.default_base_ref
    raise GitCheckFailedError(
        "no Git base configured; pass --base or configure an environment/project base"
    )


def _history_records(value: str) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    records: list[tuple[str, str, tuple[str, ...]]] = []
    for raw_record in value.split("\x1e"):
        record = raw_record.strip("\n")
        if not record:
            continue
        header, separator, raw_paths = record.partition("\x1f")
        if not separator:
            header, raw_paths = record, ""
        sha, separator, message = header.partition("\0")
        if not separator or not sha:
            continue
        records.append(
            (sha, message.rstrip("\n"), tuple(path for path in raw_paths.splitlines() if path))
        )
    return tuple(records)


def _subject_parts(message: str) -> tuple[str, str, str | None] | None:
    subject = message.splitlines()[0] if message.splitlines() else ""
    match = _MESSAGE.fullmatch(subject)
    if match is None:
        return None
    body = subject[match.end("module") + 2 :]
    ticket_match = _SUBJECT_TICKET.match(body)
    return (
        match.group("tag"),
        match.group("module").strip(),
        ticket_match.group("ticket") if ticket_match is not None else None,
    )


def _message_issue(message: str, *, root: Path) -> str | None:
    paragraphs = tuple(part for part in message.strip("\n").split("\n\n") if part)
    if len(paragraphs) not in {1, 2} or any("\n" in part for part in paragraphs[:1]):
        return "commit message must contain one subject or a subject plus one link paragraph"
    parts = _subject_parts(message)
    if parts is None:
        return "commit message does not match the Odoo format"
    if parts[0] not in _ALLOWED_TAGS:
        return "commit message uses an unsupported prefix"
    settings = _project_settings(root)
    if settings is not None and settings.enabled:
        if len(paragraphs) != 2 or not settings.base_url:
            return "configured ticket links require a second message paragraph"
        if not paragraphs[1].startswith(settings.base_url.rstrip("/") + "/"):
            return "commit ticket link does not use the configured base URL"
        if _TICKET.search(paragraphs[1].rsplit("/", 1)[-1]) is None:
            return "commit ticket link does not contain a valid Ticket key"
    elif len(paragraphs) == 2 and not re.fullmatch(r"https?://\S+", paragraphs[1]):
        return "the second commit paragraph must be an HTTP(S) link"
    return None


def _check_log(  # noqa: C901
    value: str,
    *,
    base: str,
    branch: str,
    instance: OdooInstance | None = None,
    root: Path | None = None,
) -> GitCheckResult:
    issues: list[GitCheckIssue] = []
    commits: list[str] = []
    pending: list[str] = []
    for sha, message, paths in _history_records(value):
        commits.append(sha)
        subject = message.splitlines()[0] if message.splitlines() else ""
        if subject.startswith(("fixup!", "squash!")):
            pending.append(sha)
            issues.append(
                GitCheckIssue(
                    code="pending_fixup", message="fixup/squash commit remains", commit=sha
                )
            )
        message_issue = _message_issue(message, root=root or Path.cwd())
        if message_issue is not None:
            issues.append(GitCheckIssue(code="message_invalid", message=message_issue, commit=sha))
        resolved_module: str | None = None
        scope_error: str | None = None
        if instance is not None and root is not None and paths:
            try:
                resolved_module, _ = _module_scope(instance, root, tuple(paths))
            except GitScopeError as exc:
                scope_error = str(exc)
        if scope_error is not None:
            issues.append(
                GitCheckIssue(code="scope_ambiguous", message=scope_error, commit=sha, paths=paths)
            )
        parts = _subject_parts(message)
        if parts is not None and resolved_module is not None and parts[1] != resolved_module:
            issues.append(
                GitCheckIssue(
                    code="scope_ambiguous",
                    message="commit subject module does not match staged module",
                    commit=sha,
                    paths=paths,
                )
            )
        settings = _project_settings(root or Path.cwd())
        if parts is not None and settings is not None and settings.enabled:
            paragraphs = tuple(part for part in message.strip("\n").split("\n\n") if part)
            link_ticket = (
                _safe_ticket(paragraphs[1].rsplit("/", 1)[-1])
                if len(paragraphs) == 2 and "/" in paragraphs[1]
                else None
            )
            if parts[2] is None:
                issues.append(
                    GitCheckIssue(
                        code="message_invalid",
                        message="configured ticket link requires a subject Ticket key",
                        commit=sha,
                    )
                )
            elif link_ticket != parts[2]:
                issues.append(
                    GitCheckIssue(
                        code="message_invalid",
                        message="subject Ticket key does not match the configured link",
                        commit=sha,
                    )
                )
    return GitCheckResult(
        base=base,
        branch=branch,
        valid=not issues,
        commits=tuple(commits),
        issues=tuple(issues),
        pending_fixups=tuple(pending),
    )


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
        log = _step(
            root,
            "git.check.log",
            ("log", "--format=%x1e%H%x00%B%x1f", "--name-only", f"{resolved}..HEAD"),
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
        args = ["-C", str(root), "--base", resolved]
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

    def sync_command(  # noqa: C901
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
        rebase = _step(
            root, "git.sync.rebase", ("rebase", f"refs/remotes/origin/{resolved}"), mutating=True
        )
        verify = _step(
            root,
            "git.sync.check",
            ("log", "--format=%x1e%H%x00%B%x1f", "--name-only", f"{resolved}..HEAD"),
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

        def callback(context: RunContext[GitSyncResult]) -> GitSyncResult:  # noqa: C901
            status_result = cast("ProcessResult", context.process(status.step_id))
            branch_result = cast("ProcessResult", context.process(branch.step_id))
            upstream_result = cast("ProcessResult", context.process(upstream.step_id))
            remote_result = cast("ProcessResult", context.process(remote.step_id))
            observed_branch = _text(branch_result.stdout).strip()
            upstream_name = _text(upstream_result.stdout).strip()
            if status_result.returncode != 0 or _text(status_result.stdout).strip():
                context.skip_remaining()
                raise GitSyncError("Git synchronization requires a clean worktree")
            if (
                branch_result.returncode != 0
                or observed_branch != actual_branch
                or observed_branch in _PROTECTED
                or observed_branch.startswith("release/")
            ):
                context.skip_remaining()
                raise GitSyncError("detached or protected branches cannot be synchronized")
            if remote_result.returncode != 0:
                context.skip_remaining()
                raise GitSyncError(
                    "origin remote is unavailable; no fetch or publication was attempted"
                )
            if upstream_name and upstream_name != f"origin/{observed_branch}":
                context.skip_remaining()
                raise GitSyncError(f"upstream must be origin/{observed_branch}")
            if push and not _ssh_remote(_text(remote_result.stdout).strip()):
                context.skip_remaining()
                raise GitSyncError("publication requires an SSH origin")
            authoritative_result = cast("ProcessResult", context.process(authoritative.step_id))
            if (
                authoritative_result.returncode != 0
                or _remote_head(_text(authoritative_result.stdout)) != planned_remote_sha
            ):
                context.skip_remaining()
                raise GitSyncError("remote branch changed after sync planning")
            fetched = cast("ProcessResult", context.process(fetch.step_id))
            if fetched.returncode != 0:
                context.skip_remaining()
                raise GitSyncError("fetch failed; no rebase or publication was attempted")
            integrated = False
            fetched_sha_result = cast("ProcessResult", context.process(fetched_sha.step_id))
            remote_sha = _text(fetched_sha_result.stdout).strip() or None
            if planned_remote_sha is None:
                if fetched_sha_result.returncode == 0 and remote_sha is not None:
                    context.skip_remaining()
                    raise GitSyncError("remote branch changed after sync planning")
                remote_sha = None
            elif fetched_sha_result.returncode != 0 or remote_sha != planned_remote_sha:
                context.skip_remaining()
                raise GitSyncError("remote branch changed after sync planning")
            if remote_sha is not None:
                integrated_result = cast("ProcessResult", context.process(integrate.step_id))
                integrated = True
                if integrated_result.returncode != 0:
                    context.skip(rebase.step_id)
                    raise GitSyncError(
                        "rebase conflict; run `git rebase --continue` or `git rebase --abort`"
                    )
            else:
                context.skip(integrate.step_id)
            rebased = cast("ProcessResult", context.process(rebase.step_id))
            if rebased.returncode != 0:
                raise GitSyncError(
                    "rebase conflict; run `git rebase --continue` or `git rebase --abort`"
                )
            checked = cast("ProcessResult", context.process(verify.step_id))
            checked_result = _check_log(
                _text(checked.stdout),
                base=resolved,
                branch=observed_branch,
                instance=self._instance,
                root=root,
            )
            if checked.returncode != 0 or not checked_result.valid:
                raise GitSyncError("git check failed before publication")
            published = False
            if push:
                if remote_sha is None:
                    context.skip(ancestry.step_id)
                    if rewritten is not None:
                        context.skip(rewritten.step_id)
                    published_result = cast("ProcessResult", context.process(fast_forward.step_id))
                else:
                    ancestry_result = cast("ProcessResult", context.process(ancestry.step_id))
                    if ancestry_result.returncode == 0:
                        if rewritten is not None:
                            context.skip(rewritten.step_id)
                        published_result = cast(
                            "ProcessResult", context.process(fast_forward.step_id)
                        )
                    else:
                        if rewritten is None:
                            context.skip(fast_forward.step_id)
                            raise GitSyncError("rewritten publication has no fetched lease")
                        context.skip(fast_forward.step_id)
                        published_result = cast("ProcessResult", context.process(rewritten.step_id))
                if published_result.returncode != 0:
                    raise GitSyncError("publication failed; a stale lease was not retried")
                published = True
            else:
                context.skip(ancestry.step_id)
                context.skip(fast_forward.step_id)
                if rewritten is not None:
                    context.skip(rewritten.step_id)
            return GitSyncResult(
                branch=observed_branch,
                base=resolved,
                fetched_sha=remote_sha,
                rebased=rebased.returncode == 0 or integrated,
                pushed=published,
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
