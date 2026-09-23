from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.bug_report import (
    bug_report_submit_command,
    bug_report_submit_preview,
    write_review_entry,
)
from odoo_instance_sdk.exceptions import BugReportReviewLimitError, BugReportReviewRequiredError
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from tests.unit.test_bug_report import _filled_report, _init_draft, _write_filled_report


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[2] / ".agents" / "skills" / "odcli-bug-report"


@pytest.mark.parametrize(
    ("rounds", "expected_ready"),
    (
        ([("approved", 1)], True),
        ([("changes_requested", 1), ("approved", 2)], True),
    ),
)
def test_review_scenarios_become_submit_ready(
    rounds: list[tuple[str, int]],
    expected_ready: bool,
) -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    preview = bug_report_submit_preview(draft.report_id)
    payload_hash = preview.payload_sha256
    for verdict, round_number in rounds:
        if verdict == "changes_requested" and round_number > 1:
            preview = bug_report_submit_preview(draft.report_id)
            payload_hash = preview.payload_sha256
        write_review_entry(
            draft.report_id,
            round_number=round_number,
            verdict=verdict,  # type: ignore[arg-type]
            reviewed_payload_sha256=payload_hash,
        )
    final_preview = bug_report_submit_preview(draft.report_id)
    assert final_preview.submit_ready is expected_ready


def test_three_refusals_skill_scenario_blocks_publish() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    preview = bug_report_submit_preview(draft.report_id)
    payload_hash = preview.payload_sha256
    for round_number in (1, 2, 3):
        write_review_entry(
            draft.report_id,
            round_number=round_number,
            verdict="changes_requested",
            reviewed_payload_sha256=payload_hash,
            findings="need clearer reproduction",
        )
    blocked = bug_report_submit_preview(draft.report_id)
    assert blocked.submit_ready is False
    with pytest.raises(BugReportReviewLimitError) as error:
        bug_report_submit_command(draft.report_id, executor=RecordingExecutor()).run()
    assert error.value.details["report_id"] == draft.report_id
    assert "need clearer reproduction" in error.value.details["unresolved_questions"]


def test_unavailable_reviewer_does_not_count_as_approval() -> None:
    draft = _init_draft()
    _write_filled_report(Path(draft.directory))
    preview = bug_report_submit_preview(draft.report_id)
    assert preview.submit_ready is False
    with pytest.raises(BugReportReviewRequiredError):
        bug_report_submit_command(draft.report_id, executor=RecordingExecutor()).run()
    assert not any((Path(draft.reviews_dir) / f"{n}.json").exists() for n in (1, 2, 3))


def test_changed_text_after_approval_requires_new_review_round() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    preview = bug_report_submit_preview(draft.report_id)
    write_review_entry(
        draft.report_id,
        round_number=1,
        verdict="approved",
        reviewed_payload_sha256=preview.payload_sha256,
    )
    (report_dir / "report.md").write_text(
        _filled_report("Edited after approval"),
        encoding="utf-8",
    )
    stale = bug_report_submit_preview(draft.report_id)
    assert stale.submit_ready is False
    write_review_entry(
        draft.report_id,
        round_number=2,
        verdict="approved",
        reviewed_payload_sha256=stale.payload_sha256,
    )
    ready = bug_report_submit_preview(draft.report_id)
    assert ready.submit_ready is True
    result = bug_report_submit_command(
        draft.report_id,
        executor=RecordingExecutor(
            results={
                "bug-report.submit.gh": ProcessResult(
                    argv=("gh",),
                    returncode=0,
                    stdout="https://github.com/example/repo/issues/7\n",
                    stderr="",
                    duration=0.0,
                    cwd=None,
                    environment=(),
                )
            }
        ),
    ).run()
    assert result.outcome == "published"


def test_skill_files_are_runtime_agnostic_and_include_emergency_unblock() -> None:
    skill_md = (_skill_root() / "SKILL.md").read_text(encoding="utf-8")
    reviewer_md = (_skill_root() / "reviewer-prompt.md").read_text(encoding="utf-8")
    assert skill_md.startswith("---\n")
    assert "name: odcli-bug-report" in skill_md
    assert "Codex" not in skill_md or "runtime-agnostic" in skill_md
    assert "Ponytail" in skill_md
    assert "Reuse an existing mechanism" in skill_md
    assert "Emergency unblock" in skill_md
    assert "bug-report init" in skill_md.lower() or "`odcli bug-report init`" in skill_md
    assert "reviewer" in reviewer_md.lower()
    assert "approved" in reviewer_md
    assert "changes_requested" in reviewer_md
