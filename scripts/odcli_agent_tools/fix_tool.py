#!/usr/bin/env python3
"""Install pinned, isolated OdCLI hotfix tools and retire merged ones."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPOSITORY = "maximchikAlexandr/odoo-instance-sdk"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ISSUE_RE = re.compile(r"^[1-9][0-9]*$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
REPO_ROOT = Path(__file__).resolve().parents[2]
_PROVENANCE_PROBE = (
    "import importlib.metadata,json; "
    "d=importlib.metadata.distribution('odoo-instance-sdk'); "
    "print(d.read_text('direct_url.json') or '')"
)


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        argv, capture_output=True, text=True, check=False, env=env, cwd=REPO_ROOT
    )
    if result.returncode:
        raise RuntimeError(
            f"{' '.join(argv[:3])} failed ({result.returncode}): {result.stderr[-500:]}"
        )
    return result.stdout


def _canonical_interpreter() -> Path:
    """Return the absolute interpreter behind the installed canonical odcli."""
    executable = shutil.which("odcli")
    if executable is None:
        raise RuntimeError("canonical odcli is not available on PATH")
    canonical_executable = (Path.home() / ".local" / "bin" / "odcli").resolve()
    executable_path = Path(executable)
    try:
        if executable_path.is_symlink() or executable_path.resolve() != canonical_executable:
            raise RuntimeError("canonical odcli is not the trusted user installation")
        executable_stat = executable_path.stat()
        if not stat.S_ISREG(executable_stat.st_mode) or not executable_stat.st_mode & 0o111:
            raise RuntimeError("canonical odcli is not a regular executable")
        first_line = executable_path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError) as exc:
        raise RuntimeError("cannot inspect the canonical odcli executable") from exc
    if not first_line.startswith("#!"):
        raise RuntimeError("canonical odcli has no absolute interpreter shebang")
    interpreter = Path(first_line[2:].strip().split()[0])
    canonical_tool_root = Path.home() / ".local" / "share" / "uv" / "tools" / "odoo-instance-sdk"
    if (
        not interpreter.is_absolute()
        or interpreter.is_symlink()
        or not interpreter.is_file()
        or not interpreter.resolve().is_relative_to(canonical_tool_root.resolve())
        or not interpreter.stat().st_mode & 0o111
    ):
        raise RuntimeError("canonical odcli interpreter is not an absolute executable")
    return interpreter


def _canonical_source(interpreter: Path) -> None:
    raw = _run(
        [
            str(interpreter),
            "-I",
            "-c",
            _PROVENANCE_PROBE,
        ]
    )
    try:
        payload = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("canonical distribution has no readable VCS provenance") from exc
    source = payload.get("url") if isinstance(payload, dict) else None
    vcs_info = payload.get("vcs_info") if isinstance(payload, dict) else None
    commit = vcs_info.get("commit_id") if isinstance(vcs_info, dict) else None
    if (
        source != f"https://github.com/{REPOSITORY}.git"
        or not isinstance(commit, str)
        or not SHA_RE.fullmatch(commit)
        or commit != commit.lower()
    ):
        raise RuntimeError("canonical distribution provenance is not the trusted repository")


def _engine_json(arguments: list[str]) -> dict[str, object] | list[object]:
    interpreter = _canonical_interpreter()
    _canonical_source(interpreter)
    raw = _run(
        [
            str(interpreter),
            "-m",
            "odoo_instance_sdk.internal.alternate_tool",
            *arguments,
        ]
    )
    payload = json.loads(raw)
    if not isinstance(payload, (dict, list)):
        raise TypeError("alternate-tool returned a non-JSON response")
    return payload


def _gh_json(argv: list[str]) -> dict[str, object]:
    value = json.loads(_run(["gh", *argv]))
    if not isinstance(value, dict):
        raise TypeError("GitHub returned a non-object response")
    return value


def _links_issue(references: object, issue: int) -> bool:
    owner, name = REPOSITORY.split("/", 1)
    return isinstance(references, list) and any(
        isinstance(item, dict)
        and item.get("number") == issue
        and isinstance(item.get("repository"), dict)
        and item["repository"].get("name") == name
        and isinstance(item["repository"].get("owner"), dict)
        and item["repository"]["owner"].get("login") == owner
        for item in references
    )


def _install(issue: int | str, sha: str, pr: int, review_file: Path, branch: str | None) -> None:
    issue_text = str(issue)
    if not ISSUE_RE.fullmatch(issue_text):
        raise ValueError("GitHub issue number must be a positive decimal without leading zeroes")
    issue = int(issue_text)
    if pr <= 0:
        raise ValueError("GitHub issue and PR numbers must be positive")
    if not SHA_RE.fullmatch(sha):
        raise ValueError("fix revision must be a complete lowercase SHA")
    review = json.loads(review_file.read_text(encoding="utf-8"))
    verdict = review.get("review") if isinstance(review, dict) else None
    if (
        not isinstance(verdict, dict)
        or verdict.get("sha") != sha
        or verdict.get("compatible") is not True
        or not review.get("session_id")
    ):
        raise RuntimeError("exact-SHA isolated-root compatibility approval is required")
    if branch and (
        not BRANCH_RE.fullmatch(branch) or str(issue) not in branch or branch in ("main", "master")
    ):
        raise ValueError("unsafe local branch name")
    pull = _gh_json(
        [
            "pr",
            "view",
            str(pr),
            "--repo",
            REPOSITORY,
            "--json",
            "headRefOid,closingIssuesReferences",
        ]
    )
    if pull.get("headRefOid") != sha:
        raise RuntimeError("PR head moved; review its new exact SHA before installing")
    if not _links_issue(pull.get("closingIssuesReferences"), issue):
        raise RuntimeError("PR body must link the report with Fixes #<issue> before installation")
    session_id = review.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("compatibility review has no session provenance")
    result = _engine_json(
        [
            "install",
            "--kind",
            "hotfix",
            "--identifier",
            str(issue),
            "--sha",
            sha,
            "--pr",
            str(pr),
            "--review-sha",
            sha,
            "--review-session",
            session_id,
            *(("--branch", branch) if branch else ()),
        ]
    )
    if not isinstance(result, dict) or not isinstance(result.get("launcher"), str):
        raise TypeError("alternate-tool did not publish a launcher")
    print(result["launcher"])


def _merged_into_install(issue: int, pr: int, installed_sha: str) -> bool:
    pull = _gh_json(
        [
            "pr",
            "view",
            str(pr),
            "--repo",
            REPOSITORY,
            "--json",
            "state,mergedAt,baseRefName,mergeCommit,closingIssuesReferences",
        ]
    )
    issue_data = _gh_json(["issue", "view", str(issue), "--repo", REPOSITORY, "--json", "state"])
    links = pull.get("closingIssuesReferences")
    if (
        pull.get("state") != "MERGED"
        or not pull.get("mergedAt")
        or pull.get("baseRefName") != "main"
        or issue_data.get("state") != "CLOSED"
        or not _links_issue(links, issue)
    ):
        return False
    merge = pull.get("mergeCommit")
    merge_sha = merge.get("oid") if isinstance(merge, dict) else None
    if not isinstance(merge_sha, str) or not SHA_RE.fullmatch(merge_sha):
        return False
    compare = _gh_json(["api", f"repos/{REPOSITORY}/compare/{merge_sha}...{installed_sha}"])
    if compare.get("status") not in ("identical", "ahead"):
        return False
    main_compare = _gh_json(["api", f"repos/{REPOSITORY}/compare/{installed_sha}...main"])
    return main_compare.get("status") in ("identical", "ahead")


def _reconcile(installed_sha: str) -> None:
    if not SHA_RE.fullmatch(installed_sha):
        raise ValueError("installed revision must be a complete lowercase SHA")
    records = _engine_json(["list", "--kind", "hotfix"])
    if not isinstance(records, list):
        raise TypeError("alternate-tool returned an invalid hot-fix list")
    for record in records:
        try:
            if not isinstance(record, dict):
                raise TypeError("invalid hot-fix inspection record")  # noqa: TRY301
            identifier = record.get("identifier")
            if not isinstance(identifier, str):
                raise TypeError("hot-fix inspection has no issue identity")  # noqa: TRY301
            _retire_one(identifier, installed_sha)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
            print(f"kept hot-fix: {error}", file=sys.stderr)


def _retire_one(issue_text: str, installed_sha: str) -> None:
    issue = int(issue_text)
    inspection = _engine_json(["inspect", "--kind", "hotfix", "--identifier", issue_text])
    if not isinstance(inspection, dict) or inspection.get("healthy") is not True:
        raise RuntimeError("invalid or corrupt hot-fix manifest")
    pr = inspection.get("pr")
    if not isinstance(pr, int) or not _merged_into_install(issue, pr, installed_sha):
        print(f"kept odcli-fix-{issue}: fix not proven present in installed main", file=sys.stderr)
        return
    branch = inspection.get("branch")
    if branch:
        if not isinstance(branch, str) or not BRANCH_RE.fullmatch(branch):
            raise ValueError("invalid local branch")
        _run(["git", "merge-base", "--is-ancestor", f"refs/heads/{branch}", "refs/heads/main"])
        _run(["git", "branch", "-d", branch])
    _engine_json(["remove", "--kind", "hotfix", "--identifier", issue_text])
    print(f"retired odcli-fix-{issue}")


def _update(ref: str) -> None:
    raw = _run(["odcli", "update", "--yes", "--no-input", "--format", "json", "--ref", ref])
    document = json.loads(raw)
    result = document.get("result") if isinstance(document, dict) else None
    if not isinstance(result, dict) or result.get("outcome") not in ("updated", "already_current"):
        raise RuntimeError("canonical odcli update did not report success; no fix tools retired")
    print(raw.strip())
    sha = result.get("final_sha") or result.get("target_sha")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise RuntimeError("update succeeded but has no verified final SHA; fix tools retained")
    _reconcile(sha)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("issue")
    install.add_argument("sha")
    install.add_argument("--pr", type=int, required=True)
    install.add_argument("--review", type=Path, required=True)
    install.add_argument(
        "--branch", help="Optional clean local feature branch to delete after merge"
    )
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--installed-sha", required=True)
    update = commands.add_parser(
        "update", help="Run canonical odcli update, then reconcile fix tools"
    )
    update.add_argument("--ref", default="main")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            _install(args.issue, args.sha, args.pr, args.review, args.branch)
        elif args.command == "reconcile":
            _reconcile(args.installed_sha)
        else:
            _update(args.ref)
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        print(f"fix-tool: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
