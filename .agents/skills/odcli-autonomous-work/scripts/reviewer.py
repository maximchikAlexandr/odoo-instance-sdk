#!/usr/bin/env python3
"""Small, optional Codex CLI adapter for OdCLI incident and report review."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
REPORT_SKILL = SKILL_DIR.parent / "odcli-bug-report"
REPO_ROOT = SKILL_DIR.parents[2]


def _schema(kind: str) -> dict[str, object]:
    if kind == "triage":
        properties: dict[str, object] = {
            "verdict": {
                "type": "string",
                "enum": ["no_issue", "report_only", "workaround", "blocked"],
            },
            "reason": {"type": "string"},
            "safe_workaround": {"type": "string"},
            "shared_state_compatible": {"type": "boolean"},
        }
    elif kind == "compatibility":
        properties = {
            "sha": {"type": "string"},
            "compatible": {"type": "boolean"},
            "findings": {"type": "string"},
        }
    else:
        properties = {
            "verdict": {"type": "string", "enum": ["approved", "changes_requested"]},
            "findings": {"type": "string"},
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _session_from_events(events: str, fallback: str | None) -> str:
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            return event["thread_id"]
    if fallback:
        return fallback
    raise RuntimeError("Codex did not report a session ID; no review was recorded")


def _run_codex(
    *,
    prompt: str,
    instructions: str | None,
    schema: dict[str, object] | None,
    session_id: str | None,
    model: str | None,
) -> tuple[str, dict[str, object] | str]:
    if shutil.which("codex") is None:
        raise RuntimeError("Codex CLI is unavailable; install/authenticate it before review")
    with tempfile.TemporaryDirectory(prefix="odcli-review-") as temporary:
        output = Path(temporary) / "response.json"
        argv = ["codex", "exec"]
        if session_id:
            argv += ["resume", session_id, "-c", 'sandbox_mode="read-only"']
        else:
            argv += ["-s", "read-only", "-C", str(REPO_ROOT)]
        if instructions:
            argv += ["-c", "developer_instructions=" + json.dumps(instructions)]
        if model:
            argv += ["-m", model]
        if schema:
            schema_path = Path(temporary) / "schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            argv += ["--output-schema", str(schema_path)]
        argv += ["--json", "--output-last-message", str(output), "-"]
        completed = subprocess.run(
            argv,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=REPO_ROOT,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Codex review failed ({completed.returncode}): {completed.stderr[-800:]}"
            )
        if not output.is_file():
            raise RuntimeError("Codex produced no final review")
        thread_id = _session_from_events(completed.stdout, session_id)
        response = output.read_text(encoding="utf-8").strip()
        if not response:
            raise RuntimeError("Codex returned an empty review")
        return thread_id, json.loads(response) if schema else response


def _emit(value: dict[str, object], output: Path | None) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if output:
        output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


def _triage(case_file: Path, model: str | None, output: Path | None) -> None:
    instructions = (SKILL_DIR / "triage-prompt.md").read_text(encoding="utf-8")
    case = case_file.read_text(encoding="utf-8")
    session, result = _run_codex(
        prompt=f"Evaluate this OdCLI case. Read the repository as needed.\n\n{case}",
        instructions=instructions,
        schema=_schema("triage"),
        session_id=None,
        model=model,
    )
    _emit({"session_id": session, "review": result}, output)


def _review(report_id: str, model: str | None, output: Path | None) -> None:
    from odoo_instance_sdk.bug_report import bug_report_submit_preview, write_review_entry

    root = Path.home() / ".odcli" / "bug-reports" / report_id
    report = (root / "report.md").read_text(encoding="utf-8")
    metadata = (root / "metadata.json").read_text(encoding="utf-8")
    preview = bug_report_submit_preview(report_id)
    reviews = root / "reviews"
    round_number = next((n for n in (1, 2, 3) if not (reviews / f"{n}.json").exists()), None)
    if round_number is None:
        raise RuntimeError("Review round limit reached; do not publish")
    instructions = (REPORT_SKILL / "reviewer-prompt.md").read_text(encoding="utf-8")
    session, result = _run_codex(
        prompt=f"Review round {round_number}. Payload SHA-256: {preview.payload_sha256}\n"
        f"Return only the requested verdict and findings.\n\nmetadata.json:\n{metadata}\n\nreport.md:\n{report}",
        instructions=instructions,
        schema=_schema("review"),
        session_id=None,
        model=model,
    )
    if not isinstance(result, dict) or result.get("verdict") not in (
        "approved",
        "changes_requested",
    ):
        raise RuntimeError("Reviewer returned no valid verdict")
    fresh = bug_report_submit_preview(report_id)
    if fresh.payload_sha256 != preview.payload_sha256:
        raise RuntimeError("Report changed during review; no verdict was recorded")
    write_review_entry(
        report_id,
        round_number=round_number,
        verdict=result["verdict"],
        reviewed_payload_sha256=preview.payload_sha256,
        reviewer_session_ref=session,
        findings=str(result.get("findings", "")),
    )
    _emit({"session_id": session, "round": round_number, "review": result}, output)


def _compatibility(sha: str, model: str | None, output: Path | None) -> None:
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError("compatibility review requires a full lowercase commit SHA")
    present = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if present.returncode:
        raise RuntimeError("fix commit is not available in the local repository")
    instructions = (SKILL_DIR / "compatibility-prompt.md").read_text(encoding="utf-8")
    session, result = _run_codex(
        prompt=f"Review exact OdCLI fix commit {sha} against installed canonical OdCLI and "
        "registered fix tools. Check all shared "
        "~/.odcli formats, migrations, locks, and concurrent-use effects. Return compatible=true "
        "only if this version can safely coexist with canonical OdCLI and other fix versions. "
        "Uncertainty means false. Do not modify anything.",
        instructions=instructions,
        schema=_schema("compatibility"),
        session_id=None,
        model=model,
    )
    if not isinstance(result, dict) or result.get("sha") != sha:
        raise RuntimeError("compatibility verdict did not bind to the requested SHA")
    _emit({"session_id": session, "review": result}, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", help="Optional installed Codex model; default is user configuration"
    )
    parser.add_argument("--output", type=Path, help="Write the structured response to this file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("triage").add_argument("case_file", type=Path)
    commands.add_parser("review").add_argument("report_id")
    commands.add_parser("compatibility").add_argument("sha")
    ask = commands.add_parser("ask")
    ask.add_argument("session_id")
    ask.add_argument("question_file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "triage":
            _triage(args.case_file, args.model, args.output)
        elif args.command == "review":
            _review(args.report_id, args.model, args.output)
        elif args.command == "compatibility":
            _compatibility(args.sha, args.model, args.output)
        else:
            session, answer = _run_codex(
                prompt=args.question_file.read_text(encoding="utf-8"),
                instructions=None,
                schema=None,
                session_id=args.session_id,
                model=args.model,
            )
            _emit({"session_id": session, "answer": answer}, args.output)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"reviewer: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
