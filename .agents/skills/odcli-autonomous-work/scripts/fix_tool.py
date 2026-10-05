#!/usr/bin/env python3
"""Install pinned, shared-state-compatible OdCLI hotfix tools and retire merged ones."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY = "maximchikAlexandr/odoo-instance-sdk"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
REPO_ROOT = Path(__file__).resolve().parents[4]


def _paths(issue: int) -> tuple[Path, Path, Path, Path]:
    home = Path.home()
    registry = home / ".odcli" / "fix-tools" / f"{issue}.json"
    tool_dir = home / ".local" / "share" / "odcli-fix-tools" / str(issue)
    tool_bin = tool_dir / "bin"
    shim = home / ".local" / "bin" / f"odcli-fix-{issue}"
    return registry, tool_dir, tool_bin, shim


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        argv, capture_output=True, text=True, check=False, env=env, cwd=REPO_ROOT
    )
    if result.returncode:
        raise RuntimeError(
            f"{' '.join(argv[:3])} failed ({result.returncode}): {result.stderr[-500:]}"
        )
    return result.stdout


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


def _install(issue: int, sha: str, pr: int, review_file: Path, branch: str | None) -> None:
    if issue <= 0 or pr <= 0:
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
        raise RuntimeError("exact-SHA shared ~/.odcli compatibility approval is required")
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
    registry, tool_dir, tool_bin, shim = _paths(issue)
    if registry.exists() or shim.exists():
        raise RuntimeError(f"odcli-fix-{issue} already exists; do not overwrite a running fix")
    tool_bin.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {**os.environ, "UV_TOOL_DIR": str(tool_dir), "UV_TOOL_BIN_DIR": str(tool_bin)}
    _run(
        [
            "uv",
            "tool",
            "install",
            f"odoo-instance-sdk @ git+https://github.com/{REPOSITORY}.git@{sha}",
        ],
        env=env,
    )
    binary = tool_bin / "odcli"
    if not binary.is_file():
        raise RuntimeError("uv completed but did not provide the odcli executable")
    shim.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shim_text = (
        "#!/usr/bin/env python3\n"
        "import fcntl, subprocess, sys\n"
        f"with open({str(registry.with_suffix('.lock'))!r}, 'a') as lock:\n"
        "    fcntl.flock(lock, fcntl.LOCK_SH)\n"
        f"    print('WARNING: odcli-fix-{issue} is experimental ({sha}); shares ~/.odcli', file=sys.stderr)\n"
        f"    raise SystemExit(subprocess.call([{str(binary)!r}, *sys.argv[1:]]))\n"
    )
    shim.write_text(shim_text, encoding="utf-8")
    shim.chmod(0o700)
    registry.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    registry.write_text(
        json.dumps({"issue": issue, "sha": sha, "pr": pr, "branch": branch, "shim": shim_text})
        + "\n",
        encoding="utf-8",
    )
    registry.chmod(0o600)
    print(shim)


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
    registry_dir = Path.home() / ".odcli" / "fix-tools"
    if not registry_dir.is_dir():
        return
    for manifest in sorted(registry_dir.glob("[0-9]*.json")):
        try:
            _retire_one(manifest, installed_sha)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
            print(f"kept {manifest.name}: {error}", file=sys.stderr)


def _retire_one(manifest: Path, installed_sha: str) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    issue, pr = data["issue"], data["pr"]
    if not isinstance(issue, int) or not isinstance(pr, int) or manifest.name != f"{issue}.json":
        raise ValueError("invalid fix-tool manifest")
    registry, tool_dir, tool_bin, shim = _paths(issue)
    if registry != manifest or not _merged_into_install(issue, pr, installed_sha):
        print(f"kept odcli-fix-{issue}: fix not proven present in installed main", file=sys.stderr)
        return
    if not shim.is_file() or shim.read_text(encoding="utf-8") != data.get("shim"):
        raise RuntimeError("fix shim changed; refusing cleanup")
    with manifest.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("fix tool is still running") from exc
        branch = data.get("branch")
        if branch:
            if not isinstance(branch, str) or not BRANCH_RE.fullmatch(branch):
                raise ValueError("invalid local branch")
            # A squash-merged PR may leave unique branch commits; keep that branch.
            _run(["git", "merge-base", "--is-ancestor", f"refs/heads/{branch}", "refs/heads/main"])
            # -d also refuses a branch checked out in another worktree.
            _run(["git", "branch", "-d", branch])
        env = {**os.environ, "UV_TOOL_DIR": str(tool_dir), "UV_TOOL_BIN_DIR": str(tool_bin)}
        _run(["uv", "tool", "uninstall", "odoo-instance-sdk"], env=env)
        shim.unlink()
        manifest.unlink()
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
    install.add_argument("issue", type=int)
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
