from __future__ import annotations

from typing import Literal

import msgspec

BugReportKind = Literal["bug", "enhancement", "tech-debt"]
BugReportVerdict = Literal["approved", "changes_requested"]


class BugReportReviewEntry(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    round: int
    reviewed_payload_sha256: str
    reviewer_session_ref: str | None = None
    verdict: BugReportVerdict
    findings: str = ""


class BugReportInitResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    report_id: str
    directory: str
    report_path: str
    metadata_path: str
    reviews_dir: str
    kind: BugReportKind
    title: str


class BugReportSubmitResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    report_id: str
    report_valid: bool
    submit_ready: bool
    repository: str
    title: str
    body: str
    labels: tuple[str, ...]
    payload_sha256: str
    report_errors: tuple[str, ...] = ()
    submit_blockers: tuple[str, ...] = ()
    issue_url: str | None = None
    issue_number: int | None = None
    review_round: int | None = None
    review_verdict: BugReportVerdict | None = None
    outcome: Literal["published", "already_published", "blocked", "dry_run"] = "blocked"


__all__ = [
    "BugReportInitResult",
    "BugReportKind",
    "BugReportReviewEntry",
    "BugReportSubmitResult",
    "BugReportVerdict",
]
