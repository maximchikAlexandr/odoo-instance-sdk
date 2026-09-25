"""Local bug-report draft, review validation, and submit internals.

The CLI exposes ``bug_report_init_command()`` and ``bug_report_submit_command()``
as the only public SDK primitives for the bug-report workflow.  This module
holds the on-disk layout, payload hashing, review validation, ``gh`` argv, and
the user-config repository reader.  Expression does not appear anywhere in this
flow; the lock, submit-intent ActionStep, and ``gh`` ProcessStep use the
existing typed execution boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import (
    BugReportInvalidError,
    BugReportNotFoundError,
)
from odoo_instance_sdk.internal.paths import get_config_root, get_locks_dir, get_user_root
from odoo_instance_sdk.models.bug_report import (
    BugReportReviewEntry,
    BugReportVerdict,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue

_DEFAULT_REPOSITORY = "maximchikAlexandr/odoo-instance-sdk"
_BUG_REPORT_LABELS: tuple[str, ...] = ("bug",)
_REPORT_MAX_BYTES = 262144
_MAX_REVIEW_ROUNDS = 3
_REVIEW_ROUNDS: tuple[int, ...] = (1, 2, 3)
_REPORT_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_REPORT_TEMPLATE = """\
# {title}

Kind: {kind}

## Context

- OdCLI version: {odcli_version}
- VCS SHA: {vcs_sha}
- OS: {os}
- Python: {python}
- Odoo version: {odoo_version}
- PostgreSQL version: {postgres_version}

## Reproduction or scenario

<describe the minimal steps that trigger the problem>

## Actual vs expected behaviour

Actual:

<what happens>

Expected:

<what should happen, and the basis for that expectation>

## Evidence

Facts:

<observable facts: commands, outputs, file paths, exit codes>

Hypotheses:

<separate guesses from facts>

## Minimal sufficient solution direction

<the smallest change that addresses the root cause, with explicit bounds>

## Acceptance criteria and regression scenario

- <verifiable criterion>
- Regression: <the smallest test that fails if the fix regresses>

## Project constraints and change consequences

<applicable SDK rules, contracts, or consequences of the proposed change>
"""

_REQUIRED_SECTIONS: tuple[str, ...] = (
    "## Context",
    "## Reproduction or scenario",
    "## Actual vs expected behaviour",
    "## Evidence",
    "## Minimal sufficient solution direction",
    "## Acceptance criteria and regression scenario",
    "## Project constraints and change consequences",
)


@dataclass(frozen=True, slots=True)
class BugReportPayload:
    """The validated publishable payload for one draft."""

    report_id: str
    title: str
    body: str
    repository: str
    labels: tuple[str, ...]

    def sha256(self) -> str:
        material = "\n".join(
            (self.title, self.body, self.repository, ",".join(self.labels))
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()


def bug_reports_root(*, ensure_exists: bool = False) -> Path:
    """Return ``get_user_root()/bug-reports/`` without creating it by default."""

    root = get_user_root(ensure_exists=False) / "bug-reports"
    if ensure_exists:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
    return root


def bug_report_lock_path(report_id: str) -> Path:
    if not _REPORT_ID_RE.match(report_id):
        raise ValueError("report_id must be a complete UUID")
    return get_locks_dir() / f"bug-report-{report_id}.lock"


def read_bug_report_repository() -> str:
    """Return the configured repository, defaulting to the project repository."""

    path = get_config_root(ensure_exists=False) / "user.toml"
    if not path.is_file():
        return _DEFAULT_REPOSITORY
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return _DEFAULT_REPOSITORY
    section = data.get("bug_report") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return _DEFAULT_REPOSITORY
    value = section.get("repository")
    if not isinstance(value, str) or not value.strip():
        return _DEFAULT_REPOSITORY
    return value.strip()


def bug_report_labels() -> tuple[str, ...]:
    """Return the frozen GitHub labels used for every bug-report submit."""

    return _BUG_REPORT_LABELS


def report_max_bytes() -> int:
    return _REPORT_MAX_BYTES


def max_review_rounds() -> int:
    return _MAX_REVIEW_ROUNDS


def _validate_report_id(report_id: str) -> None:
    if not _REPORT_ID_RE.match(report_id):
        raise BugReportNotFoundError(f"report_id must be a complete UUID: {report_id!r}")


def _draft_directory(report_id: str) -> Path:
    _validate_report_id(report_id)
    return bug_reports_root(ensure_exists=False) / report_id


def _write_file_mode_0600(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, 0o600)


def _read_metadata(report_dir: Path) -> dict[str, JsonValue]:
    path = report_dir / "metadata.json"
    if not path.is_file():
        raise BugReportNotFoundError(f"missing metadata.json for draft {report_dir.name}")
    try:
        with open(path, "rb") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise BugReportInvalidError(f"metadata.json is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise BugReportInvalidError("metadata.json must be a JSON object")
    return raw


def _write_metadata(report_dir: Path, metadata: Mapping[str, JsonValue]) -> None:
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _write_file_mode_0600(report_dir / "metadata.json", encoded.encode("utf-8"))


def _list_reviews(report_dir: Path) -> list[BugReportReviewEntry]:
    reviews_dir = report_dir / "reviews"
    if not reviews_dir.is_dir():
        return []
    entries: list[BugReportReviewEntry] = []
    for n in _REVIEW_ROUNDS:
        path = reviews_dir / f"{n}.json"
        if not path.is_file():
            continue
        try:
            with open(path, "rb") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise BugReportInvalidError(f"reviews/{n}.json is unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise BugReportInvalidError(f"reviews/{n}.json must be a JSON object")
        try:
            entry = BugReportReviewEntry(
                round=int(raw.get("round", n)),
                reviewed_payload_sha256=str(raw.get("reviewed_payload_sha256", "")),
                reviewer_session_ref=(
                    str(raw["reviewer_session_ref"])
                    if raw.get("reviewer_session_ref") is not None
                    else None
                ),
                verdict=cast("BugReportVerdict", raw.get("verdict")),
                findings=str(raw.get("findings", "")),
            )
        except (TypeError, ValueError) as exc:
            raise BugReportInvalidError(f"reviews/{n}.json has invalid fields: {exc}") from exc
        if entry.round != n:
            raise BugReportInvalidError(f"reviews/{n}.json round must be {n}")
        if entry.verdict not in ("approved", "changes_requested"):
            raise BugReportInvalidError(
                f"reviews/{n}.json verdict must be approved or changes_requested"
            )
        if not entry.reviewed_payload_sha256:
            raise BugReportInvalidError(f"reviews/{n}.json reviewed_payload_sha256 is required")
        entries.append(entry)
    entries.sort(key=lambda item: item.round)
    return entries


def _latest_review(reviews: Sequence[BugReportReviewEntry]) -> BugReportReviewEntry | None:
    if not reviews:
        return None
    return max(reviews, key=lambda item: item.round)


def _count_refusals(reviews: Sequence[BugReportReviewEntry]) -> int:
    return sum(1 for item in reviews if item.verdict == "changes_requested")


def _read_report(report_dir: Path) -> str:
    path = report_dir / "report.md"
    if not path.is_file():
        raise BugReportNotFoundError(f"missing report.md for draft {report_dir.name}")
    with open(path, "rb") as f:
        data = f.read()
    if len(data) > _REPORT_MAX_BYTES:
        raise BugReportInvalidError(
            f"report.md exceeds {_REPORT_MAX_BYTES} bytes (got {len(data)})"
        )
    return data.decode("utf-8", errors="replace")


def _redaction_errors(text: str) -> list[str]:
    from odoo_instance_sdk.internal.sanitize import _SECRET_PATTERNS

    errors: list[str] = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            errors.append("report.md contains a secret-like pattern; remove it before submit")
            break
    return errors


def _structure_errors(text: str) -> list[str]:
    errors: list[str] = []
    for section in _REQUIRED_SECTIONS:
        if section not in text:
            errors.append(f"report.md is missing required section: {section}")
    unfilled = re.findall(r"<[^>\n]{3,}>", text)
    if unfilled:
        errors.append("report.md has unfilled template placeholders")
    return errors


def _build_body(report_id: str, report_text: str) -> str:
    marker = f"REPORT_ID: {report_id}\n\n"
    if marker in report_text:
        return report_text
    return marker + report_text


def _gh_argv(repo: str, title: str) -> tuple[str, ...]:
    return (
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--label",
        "bug",
        "--body-file",
        "-",
    )


def _gh_recheck_search_argv(repository: str, report_id: str) -> tuple[str, ...]:
    return (
        "gh",
        "issue",
        "list",
        "--repo",
        repository,
        "--search",
        f"REPORT_ID: {report_id}",
        "--json",
        "number,url",
        "--limit",
        "1",
    )


def _gh_recheck_view_argv(repository: str, issue_number: int) -> tuple[str, ...]:
    return (
        "gh",
        "issue",
        "view",
        str(issue_number),
        "--repo",
        repository,
        "--json",
        "number,url",
    )


def _parse_issue_json_record(raw: JsonValue) -> tuple[str, int] | None:
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    number = raw.get("number")
    if isinstance(url, str) and isinstance(number, int):
        return url, number
    return None


def _parse_issue_list_json(output: str) -> tuple[str, int] | None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload:
        return None
    return _parse_issue_json_record(payload[0])


def _parse_issue_view_json(output: str) -> tuple[str, int] | None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    return _parse_issue_json_record(payload)


def _parse_issue_url(output: str) -> tuple[str, int] | None:
    for line in output.strip().splitlines():
        match = re.search(r"https?://\S+issues/(\d+)", line)
        if match:
            return match.group(0), int(match.group(1))
    return None


def _path_confined(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


__all__ = [
    "BugReportPayload",
    "bug_report_labels",
    "bug_report_lock_path",
    "bug_reports_root",
    "max_review_rounds",
    "read_bug_report_repository",
    "report_max_bytes",
]
