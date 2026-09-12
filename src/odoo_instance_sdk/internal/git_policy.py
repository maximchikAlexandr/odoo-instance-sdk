"""Pure policy and history validation for the concrete Git resource."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import GitScopeError, PlanValidationError
from odoo_instance_sdk.models import GitCheckIssue, GitCheckResult
from odoo_instance_sdk.project import ProjectConfig, effective_ticket_settings

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance


_TICKET = re.compile(r"(?<![A-Za-z])([A-Z][A-Z0-9]+-\d+)(?:_\d+)?(?![A-Za-z0-9])")
_MESSAGE = re.compile(r"^\[(?P<tag>[A-Z][A-Z0-9_-]*)\] (?P<module>[^:]+): .+$")
_SUBJECT_TICKET = re.compile(r"^(?P<ticket>[A-Z][A-Z0-9]+-\d+)(?:_\d+)?(?:\s+|$)")
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


def semantic_tag(paths: Sequence[str], statuses: Sequence[str], *, outside: bool) -> str:
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


def module_scope(instance: OdooInstance, root: Path, paths: Sequence[str]) -> tuple[str, bool]:
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


def safe_ticket(value: str | None) -> str | None:
    if value is None:
        return None
    match = _TICKET.fullmatch(value.strip())
    return match.group(1) if match is not None else None


def ticket(value: str | None) -> str | None:
    if value is None:
        return None
    match = _TICKET.fullmatch(value.strip())
    if match is None:
        raise PlanValidationError("ticket must match the configured uppercase key format")
    return match.group(1)


def history_records(value: str) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
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
    try:
        settings = effective_ticket_settings(ProjectConfig.load(root))
    except Exception:
        settings = None
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


def check_log(  # noqa: C901
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
    for sha, message, paths in history_records(value):
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
        if instance is not None and root is not None and paths:
            try:
                resolved_module, _ = module_scope(instance, root, tuple(paths))
            except GitScopeError as exc:
                issues.append(
                    GitCheckIssue(code="scope_ambiguous", message=str(exc), commit=sha, paths=paths)
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
        try:
            settings = effective_ticket_settings(ProjectConfig.load(root or Path.cwd()))
        except Exception:
            settings = None
        if parts is not None and settings is not None and settings.enabled:
            paragraphs = tuple(part for part in message.strip("\n").split("\n\n") if part)
            link_ticket = (
                safe_ticket(paragraphs[1].rsplit("/", 1)[-1])
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
