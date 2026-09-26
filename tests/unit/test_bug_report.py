from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk import bug_report as bug_report_module
from odoo_instance_sdk.bug_report import (
    bug_report_init_command,
    bug_report_submit_command,
    bug_report_submit_preview,
    write_review_entry,
)
from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.exceptions import (
    BugReportInvalidError,
    BugReportNotFoundError,
    BugReportOutcomeUnknownError,
    BugReportReviewLimitError,
    BugReportReviewRequiredError,
    BugReportStaleHashError,
    LockConflictError,
)
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.bug_report import (
    _DEFAULT_REPOSITORY,
    _gh_recheck_search_argv,
    bug_report_labels,
    bug_report_lock_path,
    bug_reports_root,
    read_bug_report_repository,
    report_max_bytes,
)
from odoo_instance_sdk.internal.locks import exclusive_lock
from odoo_instance_sdk.internal.paths import get_config_root
from odoo_instance_sdk.internal.proc import (
    ProcessResult,
    RecordingExecutor,
)
from odoo_instance_sdk.models import BugReportInitResult


def _filled_report(title: str = "Stop does not stop foreground run", kind: str = "bug") -> str:
    return f"""\
# {title}

Kind: {kind}

## Context

- OdCLI version: 0.0.0
- VCS SHA: unknown
- OS: test
- Python: 3.12
- Odoo version: unknown
- PostgreSQL version: unknown

## Reproduction or scenario

Run `odcli run` then `odcli stop` in another terminal.

## Actual vs expected behaviour

Actual:

The foreground process keeps running.

Expected:

The foreground process stops cleanly because the CLI contract says stop targets the active session.

## Evidence

Facts:

Exit code 0 from stop with no running process afterward.

Hypotheses:

The lock file may not match the detached session id.

## Minimal sufficient solution direction

Teach stop to resolve the same runtime identity that run records, without adding a second registry.

## Acceptance criteria and regression scenario

- stop exits 0 and the foreground pid is gone
- Regression: a CLI test reproduces run then stop on a fake runtime

## Project constraints and change consequences

Must preserve the execution boundary in internal/proc and avoid Expression in lifecycle code.
"""


def _init_draft(
    title: str = "Stop does not stop foreground run",
    *,
    kind: str = "bug",
) -> BugReportInitResult:
    command = bug_report_init_command(title=title, kind=cast("Any", kind))
    return command.run()


def _write_filled_report(report_dir: Path, *, title: str | None = None) -> None:
    metadata_path = report_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    report_title = title or str(metadata["title"])
    kind = str(metadata["kind"])
    (report_dir / "report.md").write_text(
        _filled_report(report_title, kind),
        encoding="utf-8",
    )
    os.chmod(report_dir / "report.md", 0o600)


def _approve(report_id: str, report_dir: Path) -> None:
    preview = bug_report_submit_preview(report_id)
    write_review_entry(
        report_id,
        round_number=1,
        verdict="approved",
        reviewed_payload_sha256=preview.payload_sha256,
    )


@pytest.mark.parametrize(
    ("title_a", "title_b"),
    (
        ("first draft", "second draft"),
        ("same title", "same title"),
    ),
)
def test_init_creates_two_independent_drafts(title_a: str, title_b: str) -> None:
    first = _init_draft(title_a)
    second = _init_draft(title_b)
    assert first.report_id != second.report_id
    assert Path(first.directory).is_dir()
    assert Path(second.directory).is_dir()
    assert first.report_path != second.report_path
    first_text = Path(first.report_path).read_text(encoding="utf-8")
    assert Path(first.directory, "metadata.json").exists()
    assert first_text.startswith(f"# {title_a}")


def test_init_dry_run_creates_nothing() -> None:
    root = bug_reports_root(ensure_exists=False)
    before = set(root.iterdir()) if root.is_dir() else set()
    result = CliRunner().invoke(
        cli,
        [
            "bug-report",
            "init",
            "--title",
            "offline draft",
            "--kind",
            "bug",
            "--dry-run",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    after = set(root.iterdir()) if root.is_dir() else set()
    assert after == before


def test_submit_dry_run_returns_validation_without_side_effects(tmp_path: Path) -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    metadata_before = (report_dir / "metadata.json").read_text(encoding="utf-8")

    preview = bug_report_submit_preview(draft.report_id)
    assert preview.report_valid is True
    assert preview.submit_ready is False
    assert preview.repository == _DEFAULT_REPOSITORY
    assert preview.labels == bug_report_labels()
    assert preview.payload_sha256
    assert preview.submit_blockers

    result = CliRunner().invoke(
        cli,
        ["bug-report", "submit", draft.report_id, "--dry-run", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["result"]["report_valid"] is True
    assert payload["result"]["submit_ready"] is False
    assert payload["result"]["labels"] == ["bug"]
    assert (report_dir / "metadata.json").read_text(encoding="utf-8") == metadata_before


@pytest.mark.parametrize(
    ("report_id", "expected_type"),
    (
        ("not-a-uuid", BugReportNotFoundError),
        ("00000000-0000-0000-0000-000000000099", BugReportNotFoundError),
    ),
)
def test_submit_rejects_missing_or_invalid_report_id(
    report_id: str, expected_type: type[Exception]
) -> None:
    with pytest.raises(expected_type):
        bug_report_submit_preview(report_id)


def test_submit_without_review_is_blocked() -> None:
    draft = _init_draft()
    _write_filled_report(Path(draft.directory))
    preview = bug_report_submit_preview(draft.report_id)
    assert preview.submit_ready is False
    assert any("review" in item.lower() for item in preview.submit_blockers)

    command = bug_report_submit_command(draft.report_id, executor=RecordingExecutor())
    with pytest.raises(BugReportReviewRequiredError):
        command.run()


def test_stale_hash_after_report_edit_requires_new_review() -> None:
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
        _filled_report("Changed title after approval"),
        encoding="utf-8",
    )

    stale_preview = bug_report_submit_preview(draft.report_id)
    assert stale_preview.submit_ready is False
    assert any("hash" in item.lower() for item in stale_preview.submit_blockers)

    command = bug_report_submit_command(draft.report_id, executor=RecordingExecutor())
    with pytest.raises(BugReportStaleHashError):
        command.run()


def test_three_refusals_stop_publish() -> None:
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
            findings=f"round {round_number} needs more evidence",
        )

    blocked = bug_report_submit_preview(draft.report_id)
    assert blocked.submit_ready is False
    assert any("changes_requested" in item for item in blocked.submit_blockers)

    command = bug_report_submit_command(draft.report_id, executor=RecordingExecutor())
    with pytest.raises(BugReportReviewLimitError) as error:
        command.run()
    assert error.value.details["report_id"] == draft.report_id
    assert error.value.details["draft_path"] == str(report_dir)
    assert error.value.details["unresolved_questions"] == [
        "round 1 needs more evidence",
        "round 2 needs more evidence",
        "round 3 needs more evidence",
    ]


def test_successful_submit_records_issue_and_uses_proc_recorder() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    _approve(draft.report_id, report_dir)
    executor = RecordingExecutor()

    def factory(step: object) -> ProcessResult:
        step_id = getattr(step, "step_id")
        if step_id == "bug-report.submit.recheck-before":
            return ProcessResult(
                argv=getattr(step, "argv"),
                returncode=0,
                stdout="[]",
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=getattr(step, "environment"),
            )
        assert getattr(step, "argv") == (
            "gh",
            "issue",
            "create",
            "--repo",
            _DEFAULT_REPOSITORY,
            "--title",
            "Stop does not stop foreground run",
            "--label",
            "bug",
            "--body-file",
            "-",
        )
        body = getattr(step, "stdin")
        assert isinstance(body, bytes)
        assert b"REPORT_ID:" in body
        return ProcessResult(
            argv=getattr(step, "argv"),
            returncode=0,
            stdout="https://github.com/maximchikAlexandr/odoo-instance-sdk/issues/74\n",
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd"),
            environment=getattr(step, "environment"),
        )

    executor.result_factory = factory
    result = bug_report_submit_command(draft.report_id, executor=executor).run()
    assert result.outcome == "published"
    assert result.issue_url == "https://github.com/maximchikAlexandr/odoo-instance-sdk/issues/74"
    assert result.issue_number == 74
    metadata = json.loads((report_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["issue_url"] == result.issue_url
    assert metadata["submit_intent"] is not None
    assert [step.step_id for step in executor.executed] == [
        "bug-report.submit.recheck-before",
        "bug-report.submit.gh",
    ]


def test_submit_uses_real_process_executor_by_default() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    _approve(draft.report_id, report_dir)
    issue_url = "https://github.com/example/repo/issues/74"
    result = ProcessResult(
        argv=("gh",),
        returncode=0,
        stdout=json.dumps([{"number": 74, "url": issue_url}]),
        stderr="",
        duration=0.0,
        cwd=None,
        environment=(),
    )

    with patch(
        "odoo_instance_sdk.internal.proc.run.SubprocessExecutor.execute",
        return_value=result,
    ) as execute:
        submitted = bug_report_submit_command(draft.report_id).run()

    assert submitted.issue_url == issue_url
    execute.assert_called_once()


def test_repeat_submit_returns_stored_url_without_second_gh_call() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    _approve(draft.report_id, report_dir)
    executor = RecordingExecutor(
        results={
            "bug-report.submit.gh": ProcessResult(
                argv=("gh",),
                returncode=0,
                stdout="https://github.com/example/repo/issues/99\n",
                stderr="",
                duration=0.0,
                cwd=None,
                environment=(),
            )
        }
    )
    first = bug_report_submit_command(draft.report_id, executor=executor).run()
    second = bug_report_submit_command(draft.report_id, executor=RecordingExecutor()).run()
    assert second.outcome == "already_published"
    assert second.issue_url == first.issue_url
    assert [step.step_id for step in executor.executed] == [
        "bug-report.submit.recheck-before",
        "bug-report.submit.gh",
    ]


def test_concurrent_submit_blocks_on_local_lock() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    _approve(draft.report_id, report_dir)
    lock_path = bug_report_lock_path(draft.report_id)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=1.0)
            bug_report_submit_command(
                draft.report_id,
                executor=RecordingExecutor(
                    results={
                        "bug-report.submit.gh": ProcessResult(
                            argv=("gh",),
                            returncode=0,
                            stdout="https://github.com/example/repo/issues/101\n",
                            stderr="",
                            duration=0.0,
                            cwd=None,
                            environment=(),
                        )
                    }
                ),
            ).run()
        except BaseException as error:
            errors.append(error)

    with exclusive_lock(lock_path):
        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        second.start()
        first.join(timeout=2.0)
        second.join(timeout=2.0)

    assert any(isinstance(error, LockConflictError) for error in errors)


def test_uncertain_gh_recheck_recovers_existing_issue() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    _approve(draft.report_id, report_dir)
    search_argv = _gh_recheck_search_argv(_DEFAULT_REPOSITORY, draft.report_id)
    executor = RecordingExecutor()

    def factory(step: object) -> ProcessResult:
        step_id = getattr(step, "step_id")
        if step_id == "bug-report.submit.recheck-before":
            return ProcessResult(
                argv=getattr(step, "argv"),
                returncode=0,
                stdout="[]",
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=getattr(step, "environment"),
            )
        if step_id == "bug-report.submit.gh":
            return ProcessResult(
                argv=getattr(step, "argv"),
                returncode=1,
                stdout="",
                stderr="timed out",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=getattr(step, "environment"),
            )
        assert step_id == "bug-report.submit.recheck-after"
        assert getattr(step, "argv") == search_argv
        return ProcessResult(
            argv=getattr(step, "argv"),
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "number": 74,
                        "url": "https://github.com/maximchikAlexandr/odoo-instance-sdk/issues/74",
                    }
                ]
            ),
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd"),
            environment=getattr(step, "environment"),
        )

    executor.result_factory = factory
    result = bug_report_submit_command(draft.report_id, executor=executor).run()
    assert result.outcome == "published"
    assert result.issue_number == 74
    assert [step.step_id for step in executor.executed] == [
        "bug-report.submit.recheck-before",
        "bug-report.submit.gh",
        "bug-report.submit.recheck-after",
    ]


def test_retry_after_uncertain_outcome_rechecks_without_blind_create() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    _approve(draft.report_id, report_dir)
    search_argv = _gh_recheck_search_argv(_DEFAULT_REPOSITORY, draft.report_id)
    first_executor = RecordingExecutor()

    def first_factory(step: object) -> ProcessResult:
        step_id = getattr(step, "step_id")
        if step_id == "bug-report.submit.recheck-before":
            return ProcessResult(
                argv=getattr(step, "argv"),
                returncode=0,
                stdout="[]",
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=getattr(step, "environment"),
            )
        if step_id == "bug-report.submit.gh":
            return ProcessResult(
                argv=getattr(step, "argv"),
                returncode=1,
                stdout="",
                stderr="timed out",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=getattr(step, "environment"),
            )
        return ProcessResult(
            argv=getattr(step, "argv"),
            returncode=0,
            stdout="[]",
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd"),
            environment=getattr(step, "environment"),
        )

    first_executor.result_factory = first_factory
    with pytest.raises(BugReportOutcomeUnknownError):
        bug_report_submit_command(draft.report_id, executor=first_executor).run()

    second_executor = RecordingExecutor()

    def second_factory(step: object) -> ProcessResult:
        assert getattr(step, "step_id") != "bug-report.submit.gh"
        assert getattr(step, "argv") == search_argv
        return ProcessResult(
            argv=getattr(step, "argv"),
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "number": 88,
                        "url": "https://github.com/example/repo/issues/88",
                    }
                ]
            ),
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd"),
            environment=getattr(step, "environment"),
        )

    second_executor.result_factory = second_factory
    result = bug_report_submit_command(draft.report_id, executor=second_executor).run()
    assert result.issue_number == 88
    assert [step.step_id for step in second_executor.executed] == [
        "bug-report.submit.recheck-before",
    ]


def test_local_write_failure_recovers_via_recheck() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    _write_filled_report(report_dir)
    _approve(draft.report_id, report_dir)
    search_argv = _gh_recheck_search_argv(_DEFAULT_REPOSITORY, draft.report_id)
    executor = RecordingExecutor()

    def factory(step: object) -> ProcessResult:
        step_id = getattr(step, "step_id")
        if step_id == "bug-report.submit.recheck-before":
            return ProcessResult(
                argv=getattr(step, "argv"),
                returncode=0,
                stdout="[]",
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=getattr(step, "environment"),
            )
        if step_id == "bug-report.submit.gh":
            return ProcessResult(
                argv=getattr(step, "argv"),
                returncode=0,
                stdout="https://github.com/example/repo/issues/55\n",
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=getattr(step, "environment"),
            )
        assert step_id == "bug-report.submit.recheck-after"
        assert getattr(step, "argv") == search_argv
        return ProcessResult(
            argv=getattr(step, "argv"),
            returncode=0,
            stdout=json.dumps([{"number": 55, "url": "https://github.com/example/repo/issues/55"}]),
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd"),
            environment=getattr(step, "environment"),
        )

    executor.result_factory = factory
    real_persist = bug_report_module._persist_issue_outcome
    calls = {"count": 0}

    def flaky_persist(*args: object, **kwargs: object) -> dict[str, JsonValue]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("disk full")
        return real_persist(*cast("Any", args), **cast("Any", kwargs))

    with patch("odoo_instance_sdk.bug_report._persist_issue_outcome", side_effect=flaky_persist):
        result = bug_report_submit_command(draft.report_id, executor=executor).run()
    assert result.issue_number == 55
    assert "bug-report.submit.recheck-after" in [step.step_id for step in executor.executed]


def test_multiline_body_is_delivered_exactly_via_stdin() -> None:
    draft = _init_draft(title='Title with "quotes" and $pecial')
    report_dir = Path(draft.directory)
    body = _filled_report('Title with "quotes" and $pecial') + "\nline\n\twith\ttabs\n"
    (report_dir / "report.md").write_text(body, encoding="utf-8")
    _approve(draft.report_id, report_dir)
    captured: list[bytes] = []

    def factory(step: object) -> ProcessResult:
        step_id = getattr(step, "step_id")
        if step_id == "bug-report.submit.recheck-before":
            return ProcessResult(
                argv=getattr(step, "argv"),
                returncode=0,
                stdout="[]",
                stderr="",
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=getattr(step, "environment"),
            )
        captured.append(getattr(step, "stdin"))
        return ProcessResult(
            argv=getattr(step, "argv"),
            returncode=0,
            stdout="https://github.com/example/repo/issues/42\n",
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd"),
            environment=getattr(step, "environment"),
        )

    executor = RecordingExecutor()
    executor.result_factory = factory
    bug_report_submit_command(draft.report_id, executor=executor).run()
    assert captured
    assert b"line\n\twith\ttabs" in captured[0]


def test_secret_like_content_blocks_validation() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    secret_report = _filled_report() + "\npassword: super-secret-value\n"
    (report_dir / "report.md").write_text(secret_report, encoding="utf-8")
    preview = bug_report_submit_preview(draft.report_id)
    assert preview.report_valid is False
    assert preview.report_errors


def test_path_confinement_rejects_invalid_report_id() -> None:
    with pytest.raises(BugReportNotFoundError):
        bug_report_submit_preview("../escape")


def test_oversized_report_is_rejected(tmp_path: Path) -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    oversized = "x" * (report_max_bytes() + 1)
    (report_dir / "report.md").write_text(oversized, encoding="utf-8")
    with pytest.raises(BugReportInvalidError):
        bug_report_submit_preview(draft.report_id)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are required")
def test_init_applies_posix_permission_bits() -> None:
    draft = _init_draft()
    report_dir = Path(draft.directory)
    assert stat.S_IMODE(report_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(Path(draft.reviews_dir).stat().st_mode) == 0o700
    assert stat.S_IMODE(Path(draft.report_path).stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(draft.metadata_path).stat().st_mode) == 0o600


def test_init_rejects_symlink_bug_reports_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    escape_root = tmp_path / "escape"
    escape_root.mkdir()
    symlink_root = tmp_path / "bug-reports"
    symlink_root.symlink_to(escape_root, target_is_directory=True)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.bug_report.bug_reports_root",
        lambda *, ensure_exists=False: symlink_root,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.bug_report.bug_reports_root",
        lambda *, ensure_exists=False: symlink_root,
    )

    with pytest.raises(BugReportInvalidError):
        bug_report_init_command(title="blocked by symlink", kind="bug").run()


def test_unfilled_template_blocks_submit_before_gh() -> None:
    draft = _init_draft()
    preview = bug_report_submit_preview(draft.report_id)
    assert preview.report_valid is False
    assert preview.report_errors
    assert any("placeholder" in item for item in preview.report_errors)

    executor = RecordingExecutor()
    command = bug_report_submit_command(draft.report_id, executor=executor)
    with pytest.raises(BugReportInvalidError):
        command.run()
    assert executor.executed == []


def test_config_repository_override_is_used() -> None:
    config_root = get_config_root(ensure_exists=True)
    config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (config_root / "user.toml").write_text(
        '[bug_report]\nrepository = "example/custom-repo"\n',
        encoding="utf-8",
    )
    assert read_bug_report_repository() == "example/custom-repo"
    draft = _init_draft()
    metadata = json.loads((Path(draft.directory) / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["repository"] == "example/custom-repo"
    preview = bug_report_submit_preview(draft.report_id)
    assert preview.repository == "example/custom-repo"
    assert preview.labels == ("bug",)
