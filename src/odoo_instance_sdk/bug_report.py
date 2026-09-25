"""Public bug-report init/submit SDK primitives.

``bug_report_init_command()`` and ``bug_report_submit_command()`` are the only
public SDK primitives for the bug-report workflow.  The CLI delegates to them;
no ``cli_only_reason`` is used.  Expression does not appear anywhere in this
flow: the lock, submit-intent ActionStep, and ``gh`` ProcessStep use the
existing typed execution boundary in ``internal.proc``.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from odoo_instance_sdk.exceptions import (
    BugReportInvalidError,
    BugReportNotFoundError,
    BugReportOutcomeUnknownError,
    BugReportReviewLimitError,
    BugReportReviewRequiredError,
    BugReportStaleHashError,
)
from odoo_instance_sdk.internal.bug_report import (
    BugReportPayload,
    _build_body,
    _count_refusals,
    _draft_directory,
    _gh_argv,
    _gh_recheck_search_argv,
    _gh_recheck_view_argv,
    _latest_review,
    _list_reviews,
    _parse_issue_list_json,
    _parse_issue_url,
    _parse_issue_view_json,
    _path_confined,
    _read_metadata,
    _read_report,
    _redaction_errors,
    _structure_errors,
    _validate_report_id,
    _write_file_mode_0600,
    _write_metadata,
    bug_report_labels,
    bug_report_lock_path,
    bug_reports_root,
    max_review_rounds,
    read_bug_report_repository,
    report_max_bytes,
)
from odoo_instance_sdk.internal.locks import exclusive_lock
from odoo_instance_sdk.internal.paths import get_user_root
from odoo_instance_sdk.models.bug_report import (
    BugReportInitResult,
    BugReportKind,
    BugReportReviewEntry,
    BugReportSubmitResult,
    BugReportVerdict,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        ProcessResult,
        RunContext,
    )

_VALID_KINDS: tuple[BugReportKind, ...] = ("bug", "enhancement", "tech-debt")


def _installed_version() -> str:
    try:
        from importlib.metadata import distribution

        dist = distribution("odoo-instance-sdk")
    except Exception:
        return "unknown"
    else:
        return dist.version


def _installed_vcs_sha() -> str:
    try:
        from importlib.metadata import distribution

        dist = distribution("odoo-instance-sdk")
        raw = dist.read_text("direct_url.json")
        if not raw:
            return "unknown"
        payload = json.loads(raw)
        vcs_info = payload.get("vcs_info") if isinstance(payload, dict) else None
        if not isinstance(vcs_info, dict):
            return "unknown"
        commit_id = vcs_info.get("commit_id")
        if isinstance(commit_id, str) and len(commit_id) >= 7:
            return commit_id[:7]
        return "unknown"  # noqa: TRY300
    except Exception:
        return "unknown"


def _odoo_version() -> str:
    return "unknown"


def _postgres_version() -> str:
    return "unknown"


def _report_template(title: str, kind: BugReportKind) -> str:
    from odoo_instance_sdk.internal.bug_report import _REPORT_TEMPLATE

    return _REPORT_TEMPLATE.format(
        title=title,
        kind=kind,
        odcli_version=_installed_version(),
        vcs_sha=_installed_vcs_sha(),
        os=platform.platform(),
        python=sys.version.split()[0],
        odoo_version=_odoo_version(),
        postgres_version=_postgres_version(),
    )


def _metadata_dict(
    *,
    report_id: str,
    title: str,
    kind: BugReportKind,
    repository: str,
    labels: Sequence[str],
) -> dict[str, JsonValue]:
    import datetime as _dt

    return {
        "id": report_id,
        "title": title,
        "kind": kind,
        "repository": repository,
        "labels": list(labels),
        "created_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "submit_intent": None,
        "issue_url": None,
        "issue_number": None,
    }


def _create_draft(
    *,
    report_id: str,
    title: str,
    kind: BugReportKind,
    repository: str,
    labels: Sequence[str],
) -> BugReportInitResult:
    root = bug_reports_root(ensure_exists=True)
    user_root = get_user_root(ensure_exists=False)
    if root.is_symlink() or not _path_confined(user_root, root):
        raise BugReportInvalidError("bug-reports root escapes user root or is a symlink")
    report_dir = root / report_id
    if report_dir.exists():
        raise BugReportInvalidError(f"draft directory already exists: {report_dir}")
    if not _path_confined(root, report_dir) or report_dir.is_symlink():
        raise BugReportInvalidError(f"draft directory escapes bug-reports root: {report_dir}")
    report_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(report_dir, 0o700)
    reviews_dir = report_dir / "reviews"
    reviews_dir.mkdir(mode=0o700, exist_ok=False)
    os.chmod(reviews_dir, 0o700)
    report_path = report_dir / "report.md"
    metadata_path = report_dir / "metadata.json"
    _write_file_mode_0600(report_path, _report_template(title, kind).encode("utf-8"))
    _write_metadata(
        report_dir,
        _metadata_dict(
            report_id=report_id, title=title, kind=kind, repository=repository, labels=labels
        ),
    )
    return BugReportInitResult(
        report_id=report_id,
        directory=str(report_dir),
        report_path=str(report_path),
        metadata_path=str(metadata_path),
        reviews_dir=str(reviews_dir),
        kind=kind,
        title=title,
    )


def bug_report_init_command(
    *,
    title: str,
    kind: BugReportKind = "bug",
) -> Command[BugReportInitResult]:
    """Capture one immutable bug-report init command for preview and execution.

    Creates a UUID ``REPORT_ID`` and writes ``get_user_root()/bug-reports/<REPORT_ID>/``
    with ``report.md``, ``metadata.json``, and an empty ``reviews/`` directory.
    ``--dry-run`` is handled by the CLI; this primitive always writes when run.
    The directory is ``0700`` and the files are ``0600`` on POSIX.
    """

    from odoo_instance_sdk.commands.output import action_command
    from odoo_instance_sdk.internal.bug_report import _DEFAULT_REPOSITORY

    if not isinstance(kind, str) or kind not in _VALID_KINDS:
        raise BugReportInvalidError(f"kind must be one of {_VALID_KINDS}: {kind!r}")
    clean_title = title.strip()
    if not clean_title:
        raise BugReportInvalidError("title must be a non-empty string")
    repository = read_bug_report_repository() or _DEFAULT_REPOSITORY
    labels = bug_report_labels()
    report_id = str(uuid.uuid4())

    return cast(
        "Command[BugReportInitResult]",
        action_command(
            "bug-report.init",
            lambda: _create_draft(
                report_id=report_id,
                title=clean_title,
                kind=kind,
                repository=repository,
                labels=labels,
            ),
            description="Create bug-report draft directory and template files",
            mutating=True,
        ),
    )


def _load_payload(report_id: str) -> tuple[BugReportPayload, dict[str, JsonValue], Path]:
    _validate_report_id(report_id)
    report_dir = _draft_directory(report_id)
    if not report_dir.is_dir():
        raise BugReportNotFoundError(f"bug-report draft not found: {report_id}")
    if not _path_confined(bug_reports_root(ensure_exists=False), report_dir):
        raise BugReportNotFoundError(f"bug-report draft escapes root: {report_id}")
    metadata = _read_metadata(report_dir)
    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip():
        raise BugReportInvalidError("metadata.json title is missing or invalid")
    kind = metadata.get("kind")
    if not isinstance(kind, str) or kind not in _VALID_KINDS:
        raise BugReportInvalidError("metadata.json kind is missing or invalid")
    repository = metadata.get("repository")
    if not isinstance(repository, str) or not repository.strip():
        repository = read_bug_report_repository()
    labels_value = metadata.get("labels")
    if not isinstance(labels_value, list) or not labels_value:
        labels = bug_report_labels()
    else:
        labels = tuple(str(item) for item in labels_value)
    report_text = _read_report(report_dir)
    body = _build_body(report_id, report_text)
    payload = BugReportPayload(
        report_id=report_id,
        title=title.strip(),
        body=body,
        repository=str(repository),
        labels=labels,
    )
    return payload, metadata, report_dir


def _validate_for_submit(
    payload: BugReportPayload,
    *,
    report_text: str,
    reviews: Sequence[BugReportReviewEntry],
) -> tuple[bool, bool, list[str], list[str], BugReportReviewEntry | None]:
    report_errors: list[str] = []
    report_errors.extend(_structure_errors(report_text))
    report_errors.extend(_redaction_errors(report_text))
    if len(report_text.encode("utf-8")) > report_max_bytes():
        report_errors.append(f"report.md exceeds {report_max_bytes()} bytes")
    report_valid = not report_errors

    submit_blockers: list[str] = []
    latest = _latest_review(reviews)
    if latest is None:
        submit_blockers.append("missing independent review (round 1..3 required)")
    else:
        if latest.verdict != "approved":
            submit_blockers.append(f"latest review verdict is {latest.verdict}, not approved")
        if latest.reviewed_payload_sha256 != payload.sha256():
            submit_blockers.append("approved payload hash does not match current payload")
        if _count_refusals(reviews) >= max_review_rounds() and latest.verdict != "approved":
            submit_blockers.append(
                "three review rounds returned changes_requested; publish is stopped"
            )
    submit_ready = report_valid and not submit_blockers
    return report_valid, submit_ready, report_errors, submit_blockers, latest


def _record_submit_intent(
    report_dir: Path,
    metadata: dict[str, JsonValue],
    *,
    repository: str,
    title: str,
    payload_sha256: str,
) -> dict[str, JsonValue]:
    import datetime as _dt

    intent: dict[str, JsonValue] = {
        "recorded_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "repository": repository,
        "title": title,
        "payload_sha256": payload_sha256,
        "labels": list(bug_report_labels()),
    }
    updated = {**metadata, "submit_intent": intent}
    _write_metadata(report_dir, updated)
    return updated


@contextlib.contextmanager
def _bug_report_lock(report_id: str) -> Iterator[None]:
    lock_path = bug_report_lock_path(report_id)
    with exclusive_lock(lock_path):
        yield


def _unresolved_review_questions(
    reviews: Sequence[BugReportReviewEntry],
) -> tuple[str, ...]:
    return tuple(
        item.findings.strip()
        for item in reviews
        if item.verdict == "changes_requested" and item.findings.strip()
    )


def _execute_recheck_step(
    context: RunContext[BugReportSubmitResult],
    step: PreparedStep,
    *,
    parse: Callable[[str], tuple[str, int] | None],
) -> tuple[str, int] | None:
    process_result = cast("ProcessResult", context.process(step.step_id))
    if int(process_result.returncode) != 0:
        return None
    stdout = process_result.stdout if isinstance(process_result.stdout, str) else ""
    return parse(stdout)


def _recheck_before_create(
    context: RunContext[BugReportSubmitResult],
    *,
    metadata: dict[str, JsonValue],
    recheck_view_step: PreparedStep | None,
    recheck_search_step: PreparedStep,
) -> tuple[tuple[str, int] | None, tuple[str, ...]]:
    executed: list[str] = []
    issue_number = metadata.get("issue_number")
    if recheck_view_step is not None and isinstance(issue_number, int):
        issue = _execute_recheck_step(
            context,
            recheck_view_step,
            parse=_parse_issue_view_json,
        )
        executed.append(recheck_view_step.step_id)
        if issue is not None:
            return issue, tuple(executed)
    issue = _execute_recheck_step(
        context,
        recheck_search_step,
        parse=_parse_issue_list_json,
    )
    executed.append(recheck_search_step.step_id)
    return issue, tuple(executed)


def _recheck_after_uncertain_outcome(
    context: RunContext[BugReportSubmitResult],
    recheck_search_step: PreparedStep,
) -> tuple[str, int] | None:
    return _execute_recheck_step(
        context,
        recheck_search_step,
        parse=_parse_issue_list_json,
    )


def _skip_prepared_steps(
    context: RunContext[BugReportSubmitResult],
    prepared: Sequence[PreparedAction | PreparedStep],
    *,
    except_step_ids: set[str],
) -> None:
    for step in prepared:
        if step.step_id not in except_step_ids:
            context.skip(step.step_id)


def _submit_result(
    report_id: str,
    *,
    payload: BugReportPayload,
    payload_sha: str,
    labels: tuple[str, ...],
    latest: BugReportReviewEntry | None,
    issue_url: str,
    issue_number: int,
    outcome: Literal["published", "already_published", "blocked", "dry_run"],
) -> BugReportSubmitResult:
    return BugReportSubmitResult(
        report_id=report_id,
        report_valid=True,
        submit_ready=True,
        repository=payload.repository,
        title=payload.title,
        body=payload.body,
        labels=labels,
        payload_sha256=payload_sha,
        issue_url=issue_url,
        issue_number=issue_number,
        review_round=latest.round if latest is not None else None,
        review_verdict=latest.verdict if latest is not None else None,
        outcome=outcome,
    )


def _persist_issue_outcome(
    report_dir: Path,
    metadata: dict[str, JsonValue],
    *,
    issue_url: str,
    issue_number: int,
) -> dict[str, JsonValue]:
    updated = {**metadata, "issue_url": issue_url, "issue_number": issue_number}
    _write_metadata(report_dir, updated)
    return updated


def _submit_preview_result(
    report_id: str,
    *,
    payload: BugReportPayload,
    report_text: str,
    reviews: Sequence[BugReportReviewEntry],
    latest: BugReportReviewEntry | None,
    report_valid: bool,
    submit_ready: bool,
    report_errors: Sequence[str],
    submit_blockers: Sequence[str],
) -> BugReportSubmitResult:
    labels = bug_report_labels()
    return BugReportSubmitResult(
        report_id=report_id,
        report_valid=report_valid,
        submit_ready=submit_ready,
        repository=payload.repository,
        title=payload.title,
        body=payload.body,
        labels=labels,
        payload_sha256=payload.sha256(),
        report_errors=tuple(report_errors),
        submit_blockers=tuple(submit_blockers),
        review_round=latest.round if latest is not None else None,
        review_verdict=latest.verdict if latest is not None else None,
        outcome="dry_run",
    )


def bug_report_submit_preview(report_id: str) -> BugReportSubmitResult:
    """Return submit validation preview without writing, spawning, or contacting a reviewer."""

    payload, _metadata, report_dir = _load_payload(report_id)
    report_text = _read_report(report_dir)
    reviews = _list_reviews(report_dir)
    report_valid, submit_ready, report_errors, submit_blockers, latest = _validate_for_submit(
        payload,
        report_text=report_text,
        reviews=reviews,
    )
    return _submit_preview_result(
        report_id,
        payload=payload,
        report_text=report_text,
        reviews=reviews,
        latest=latest,
        report_valid=report_valid,
        submit_ready=submit_ready,
        report_errors=report_errors,
        submit_blockers=submit_blockers,
    )


class _SubmissionExecutor:
    def __init__(
        self,
        *,
        report_id: str,
        dry_run: bool,
        report_dir: Path,
        payload: BugReportPayload,
        report_text: str,
        reviews: Sequence[BugReportReviewEntry],
        latest: BugReportReviewEntry | None,
        report_valid: bool,
        submit_ready: bool,
        report_errors: Sequence[str],
        submit_blockers: Sequence[str],
        payload_sha: str,
        labels: tuple[str, ...],
        prepared_steps: Sequence[PreparedAction | PreparedStep],
        submit_intent_action: PreparedAction,
        recheck_view_step: PreparedStep | None,
        recheck_before_step: PreparedStep,
        gh_step: PreparedStep,
        recheck_after_step: PreparedStep,
    ) -> None:
        self.report_id = report_id
        self.dry_run = dry_run
        self.report_dir = report_dir
        self.payload = payload
        self.report_text = report_text
        self.reviews = reviews
        self.latest = latest
        self.report_valid = report_valid
        self.submit_ready = submit_ready
        self.report_errors = report_errors
        self.submit_blockers = submit_blockers
        self.payload_sha = payload_sha
        self.labels = labels
        self.prepared_steps = prepared_steps
        self.submit_intent_action = submit_intent_action
        self.recheck_view_step = recheck_view_step
        self.recheck_before_step = recheck_before_step
        self.gh_step = gh_step
        self.recheck_after_step = recheck_after_step

    def preflight(self) -> BugReportSubmitResult | None:
        if self.dry_run:
            return _submit_preview_result(
                self.report_id,
                payload=self.payload,
                report_text=self.report_text,
                reviews=self.reviews,
                latest=self.latest,
                report_valid=self.report_valid,
                submit_ready=self.submit_ready,
                report_errors=self.report_errors,
                submit_blockers=self.submit_blockers,
            )
        if not self.report_valid:
            raise BugReportInvalidError(
                "report.md failed validation: " + "; ".join(self.report_errors)
            )
        if self.submit_ready:
            return None
        refusals = _count_refusals(self.reviews)
        if refusals >= max_review_rounds():
            raise BugReportReviewLimitError(
                f"three review rounds returned changes_requested for {self.report_id}; "
                f"publish is stopped at {self.report_dir}",
                details={
                    "report_id": self.report_id,
                    "draft_path": str(self.report_dir),
                    "unresolved_questions": list(_unresolved_review_questions(self.reviews)),
                },
            )
        if self.latest is not None and self.latest.verdict != "approved":
            raise BugReportReviewRequiredError(
                f"latest review verdict is {self.latest.verdict}, not approved"
            )
        if self.latest is not None and self.latest.reviewed_payload_sha256 != self.payload_sha:
            raise BugReportStaleHashError(
                "approved payload hash does not match current payload; request a new review"
            )
        raise BugReportReviewRequiredError("missing independent review")

    def _already_published(
        self,
        context: RunContext[BugReportSubmitResult],
        metadata: dict[str, JsonValue],
    ) -> BugReportSubmitResult:
        issue_url = metadata["issue_url"]
        if not isinstance(issue_url, str):
            raise BugReportInvalidError("stored issue_url is not a string")
        _skip_prepared_steps(context, self.prepared_steps, except_step_ids=set())
        issue_number = metadata.get("issue_number")
        return _submit_result(
            self.report_id,
            payload=self.payload,
            payload_sha=self.payload_sha,
            labels=self.labels,
            latest=self.latest,
            issue_url=issue_url,
            issue_number=issue_number if isinstance(issue_number, int) else 0,
            outcome="already_published",
        )

    def _recover_before_create(
        self,
        context: RunContext[BugReportSubmitResult],
        metadata: dict[str, JsonValue],
    ) -> BugReportSubmitResult | None:
        recovered, executed_recheck = _recheck_before_create(
            context,
            metadata=metadata,
            recheck_view_step=self.recheck_view_step,
            recheck_search_step=self.recheck_before_step,
        )
        if recovered is None:
            return None
        issue_url, issue_number = recovered
        _skip_prepared_steps(
            context,
            self.prepared_steps,
            except_step_ids=set(executed_recheck),
        )
        _persist_issue_outcome(
            self.report_dir,
            metadata,
            issue_url=issue_url,
            issue_number=issue_number,
        )
        return _submit_result(
            self.report_id,
            payload=self.payload,
            payload_sha=self.payload_sha,
            labels=self.labels,
            latest=self.latest,
            issue_url=issue_url,
            issue_number=issue_number,
            outcome="published",
        )

    def _create_and_recover(
        self,
        context: RunContext[BugReportSubmitResult],
        metadata: dict[str, JsonValue],
    ) -> BugReportSubmitResult:
        context.action(self.submit_intent_action.step_id)
        updated_metadata = _record_submit_intent(
            self.report_dir,
            metadata,
            repository=self.payload.repository,
            title=self.payload.title,
            payload_sha256=self.payload_sha,
        )
        context.complete_action(self.submit_intent_action.step_id)
        process_result = cast("ProcessResult", context.process(self.gh_step.step_id))
        returncode = int(process_result.returncode)
        stdout = process_result.stdout if isinstance(process_result.stdout, str) else ""
        stderr = process_result.stderr if isinstance(process_result.stderr, str) else ""
        uncertain_detail = (
            f"gh issue create exited {returncode}: {stderr.strip() or stdout.strip()}"
            if returncode != 0
            else f"gh issue create produced no parseable issue URL: {stdout.strip()}"
        )
        if returncode == 0:
            issue = _parse_issue_url(stdout)
            if issue is not None:
                return self._persist_created_issue(context, updated_metadata, issue)
        recovered = _recheck_after_uncertain_outcome(context, self.recheck_after_step)
        if recovered is None:
            raise BugReportOutcomeUnknownError(
                f"{uncertain_detail}; GitHub re-check found no existing issue"
            )
        return self._persist_published(updated_metadata, recovered)

    def _persist_created_issue(
        self,
        context: RunContext[BugReportSubmitResult],
        metadata: dict[str, JsonValue],
        issue: tuple[str, int],
    ) -> BugReportSubmitResult:
        issue_url, issue_number = issue
        try:
            _persist_issue_outcome(
                self.report_dir,
                metadata,
                issue_url=issue_url,
                issue_number=issue_number,
            )
        except OSError as error:
            recovered = _recheck_after_uncertain_outcome(context, self.recheck_after_step)
            if recovered is None:
                raise BugReportOutcomeUnknownError(
                    "gh issue was created but local metadata write failed "
                    "and GitHub re-check found no issue"
                ) from error
            return self._persist_published(metadata, recovered)
        context.skip(self.recheck_after_step.step_id)
        return _submit_result(
            self.report_id,
            payload=self.payload,
            payload_sha=self.payload_sha,
            labels=self.labels,
            latest=self.latest,
            issue_url=issue_url,
            issue_number=issue_number,
            outcome="published",
        )

    def _persist_published(
        self,
        metadata: dict[str, JsonValue],
        issue: tuple[str, int],
    ) -> BugReportSubmitResult:
        issue_url, issue_number = issue
        _persist_issue_outcome(
            self.report_dir,
            metadata,
            issue_url=issue_url,
            issue_number=issue_number,
        )
        return _submit_result(
            self.report_id,
            payload=self.payload,
            payload_sha=self.payload_sha,
            labels=self.labels,
            latest=self.latest,
            issue_url=issue_url,
            issue_number=issue_number,
            outcome="published",
        )

    def run(self, context: RunContext[BugReportSubmitResult]) -> BugReportSubmitResult:
        preflight_result = self.preflight()
        if preflight_result is not None:
            return preflight_result
        with _bug_report_lock(self.report_id):
            current_metadata = _read_metadata(self.report_dir)
            current_issue_url = current_metadata.get("issue_url")
            if isinstance(current_issue_url, str) and current_issue_url:
                return self._already_published(context, current_metadata)
            recovered = self._recover_before_create(context, current_metadata)
            if recovered is not None:
                return recovered
            return self._create_and_recover(context, current_metadata)


def bug_report_submit_command(
    report_id: str,
    *,
    dry_run: bool = False,
    executor: ProcessExecutor | None = None,
    timeout: float | None = 30.0,
) -> Command[BugReportSubmitResult]:
    """Capture one immutable bug-report submit command for preview and execution.

    Validates the draft, review state, and payload hash.  On ``dry_run`` returns
    ``report_valid`` and ``submit_ready`` as separate booleans plus
    ``report_errors`` and ``submit_blockers``; no file is written, no ``gh`` is
    invoked, no reviewer is contacted.  On execute, when the latest review is
    ``approved`` for the current payload hash, records submit intent in
    ``metadata.json`` and invokes ``gh`` through ``internal/proc`` with
    ``shell=False`` and ``--body-file -`` (body on stdin).  The child inherits
    ``GH_TOKEN``; OdCLI does not copy it into config, draft, or argv.  Uncertain
    ``gh`` outcome returns ``submit_outcome_unknown`` and does not blind re-POST.
    """

    from odoo_instance_sdk.execution import (
        ActionStep,
        Command,
        ExecutionPlan,
        ProcessStep,
    )
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        SubprocessExecutor,
    )

    _validate_report_id(report_id)
    payload, initial_metadata, report_dir = _load_payload(report_id)
    report_text = _read_report(report_dir)
    reviews = _list_reviews(report_dir)
    report_valid, submit_ready, report_errors, submit_blockers, latest = _validate_for_submit(
        payload,
        report_text=report_text,
        reviews=reviews,
    )

    labels = bug_report_labels()
    payload_sha = payload.sha256()
    gh_argv = _gh_argv(payload.repository, payload.title)
    recheck_search_argv = _gh_recheck_search_argv(payload.repository, report_id)
    stored_issue_number = initial_metadata.get("issue_number")
    recheck_view_step: PreparedStep | None = None
    recheck_view_process: ProcessStep | None = None
    if isinstance(stored_issue_number, int) and not initial_metadata.get("issue_url"):
        recheck_view_argv = _gh_recheck_view_argv(payload.repository, stored_issue_number)
        recheck_view_step = PreparedStep(
            step_id="bug-report.submit.recheck-view",
            argv=recheck_view_argv,
            timeout=timeout,
            read_only=True,
            mutating=False,
            text=True,
        )
        recheck_view_process = ProcessStep(
            step_id=recheck_view_step.step_id,
            argv=recheck_view_argv,
            display=" ".join(recheck_view_argv),
            executable=recheck_view_argv[0],
            timeout=recheck_view_step.timeout,
            mode="captured",
            read_only=True,
            mutating=False,
        )

    submit_intent_action = PreparedAction(
        step_id="bug-report.submit.intent",
        action="submit_intent",
        description="Record submit intent in metadata.json",
        mutating=True,
    )
    recheck_before_step = PreparedStep(
        step_id="bug-report.submit.recheck-before",
        argv=recheck_search_argv,
        timeout=timeout,
        read_only=True,
        mutating=False,
        text=True,
    )
    gh_step = PreparedStep(
        step_id="bug-report.submit.gh",
        argv=gh_argv,
        stdin=payload.body.encode("utf-8"),
        timeout=timeout,
        read_only=False,
        mutating=True,
        text=True,
    )
    recheck_after_step = PreparedStep(
        step_id="bug-report.submit.recheck-after",
        argv=recheck_search_argv,
        timeout=timeout,
        read_only=True,
        mutating=False,
        text=True,
    )

    plan_steps: list[ActionStep | ProcessStep] = [
        ActionStep(
            step_id=submit_intent_action.step_id,
            action=submit_intent_action.action,
            description=submit_intent_action.description,
            mutating=True,
        ),
        ProcessStep(
            step_id=recheck_before_step.step_id,
            argv=recheck_search_argv,
            display=" ".join(recheck_search_argv),
            executable=recheck_search_argv[0],
            timeout=recheck_before_step.timeout,
            mode="captured",
            read_only=True,
            mutating=False,
        ),
    ]
    if recheck_view_process is not None:
        plan_steps.append(recheck_view_process)
    plan_steps.extend(
        (
            ProcessStep(
                step_id=gh_step.step_id,
                argv=gh_argv,
                display=" ".join(gh_argv),
                executable=gh_argv[0],
                input_preview="<redacted>",
                timeout=gh_step.timeout,
                mode="captured",
                read_only=False,
                mutating=True,
            ),
            ProcessStep(
                step_id=recheck_after_step.step_id,
                argv=recheck_search_argv,
                display=" ".join(recheck_search_argv),
                executable=recheck_search_argv[0],
                timeout=recheck_after_step.timeout,
                mode="captured",
                read_only=True,
                mutating=False,
            ),
        )
    )
    plan = ExecutionPlan(steps=tuple(plan_steps)).with_fingerprint()

    prepared_steps: list[PreparedAction | PreparedStep] = [
        submit_intent_action,
        recheck_before_step,
    ]
    if recheck_view_step is not None:
        prepared_steps.append(recheck_view_step)
    prepared_steps.extend((gh_step, recheck_after_step))

    submission = _SubmissionExecutor(
        report_id=report_id,
        dry_run=dry_run,
        report_dir=report_dir,
        payload=payload,
        report_text=report_text,
        reviews=reviews,
        latest=latest,
        report_valid=report_valid,
        submit_ready=submit_ready,
        report_errors=report_errors,
        submit_blockers=submit_blockers,
        payload_sha=payload_sha,
        labels=labels,
        prepared_steps=prepared_steps,
        submit_intent_action=submit_intent_action,
        recheck_view_step=recheck_view_step,
        recheck_before_step=recheck_before_step,
        gh_step=gh_step,
        recheck_after_step=recheck_after_step,
    )

    def callback(context: RunContext[BugReportSubmitResult]) -> BugReportSubmitResult:
        return submission.run(context)

    command: Command[BugReportSubmitResult] = Command.create(
        plan,
        callback,
        tuple(prepared_steps),
        executor=executor or SubprocessExecutor(),
    )
    return command


def write_review_entry(
    report_id: str,
    *,
    round_number: int,
    verdict: BugReportVerdict,
    reviewed_payload_sha256: str,
    reviewer_session_ref: str | None = None,
    findings: str = "",
) -> None:
    """Helper for the skill/agent to write one ``reviews/N.json`` file.

    OdCLI does not embed an LLM and does not write review files during normal
    CLI flows.  This helper exists so the skill and tests can produce a
    schema-matching review file without re-implementing the on-disk layout.
    """

    from odoo_instance_sdk.internal.bug_report import _REVIEW_ROUNDS

    _validate_report_id(report_id)
    if round_number not in _REVIEW_ROUNDS:
        raise BugReportInvalidError(f"round must be one of {_REVIEW_ROUNDS}")
    report_dir = _draft_directory(report_id)
    reviews_dir = report_dir / "reviews"
    reviews_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(reviews_dir, 0o700)
    entry = BugReportReviewEntry(
        round=round_number,
        reviewed_payload_sha256=reviewed_payload_sha256,
        reviewer_session_ref=reviewer_session_ref,
        verdict=verdict,
        findings=findings,
    )
    encoded = (
        json.dumps(
            {
                "round": entry.round,
                "reviewed_payload_sha256": entry.reviewed_payload_sha256,
                "reviewer_session_ref": entry.reviewer_session_ref,
                "verdict": entry.verdict,
                "findings": entry.findings,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    _write_file_mode_0600(reviews_dir / f"{round_number}.json", encoded)


__all__ = [
    "bug_report_init_command",
    "bug_report_submit_command",
    "bug_report_submit_preview",
    "write_review_entry",
]
