#!/usr/bin/env python3
"""Run the private GitLab member-auth workflow from a disposable checkout.

The script is intentionally an independent test harness, not a second CLI
implementation.  It passes member IDs to the public CLI, never reads token
values, emits bounded JSON summaries, and removes only its own local root.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Final

_PNG: Final[bytes] = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_SECRET_TEXT: Final[re.Pattern[str]] = re.compile(
    r"(?i)((?:private-token|password|token|authorization)\s*[:=]\s*)[^\s,}]+"
)
_SHA: Final[re.Pattern[str]] = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("all", "clone", "commit", "sync", "mr"),
        default="all",
        help="workflow phase to execute (default: all)",
    )
    parser.add_argument("--target", default="main", help="GitLab target branch")
    parser.add_argument("--odcli", default="odcli", help="public odcli executable")
    parser.add_argument("--git", default="git", help="git executable")
    parser.add_argument(
        "--work-root",
        type=Path,
        help="optional disposable root; it is always removed during cleanup",
    )
    parser.add_argument(
        "--keep-local",
        action="store_true",
        help="retain the disposable root for debugging (never retains credentials)",
    )
    parser.add_argument(
        "--member-a",
        default=os.environ.get("TEST_GITLAB_MEMBER_A"),
        help="selected member A; defaults to TEST_GITLAB_MEMBER_A",
    )
    parser.add_argument(
        "--member-b",
        default=os.environ.get("TEST_GITLAB_MEMBER_B"),
        help="selected member B; defaults to TEST_GITLAB_MEMBER_B",
    )
    parser.add_argument(
        "--token-key-a",
        default=os.environ.get("TEST_GITLAB_TOKEN_KEY_A", ""),
        help="token key name for evidence labels; the value is never read",
    )
    parser.add_argument(
        "--token-key-b",
        default=os.environ.get("TEST_GITLAB_TOKEN_KEY_B", ""),
        help="token key name for evidence labels; the value is never read",
    )
    return parser


def _required_url() -> str:
    value = os.environ.get("TEST_REPOSITORY_URL", "").strip()
    if not value:
        raise RuntimeError("TEST_REPOSITORY_URL is required")
    return value


def _script_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _sanitize(text: str) -> str:
    for key in ("TEST_GITLAB_TOKEN_KEY_A", "TEST_GITLAB_TOKEN_KEY_B"):
        secret = os.environ.get(key, "")
        if secret:
            text = text.replace(secret, "<redacted>")
    text = _SECRET_TEXT.sub(r"\1<redacted>", text)
    return _SHA.sub("<sha>", text)


def _run(
    argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = _sanitize(result.stderr or result.stdout)[-1200:]
        raise RuntimeError(f"command failed ({result.returncode}): {argv[0]} {detail}")
    return result


def _git(git: str, root: Path, *args: str) -> str:
    return _run([git, "-C", str(root), *args]).stdout.strip()


def _cli(
    executable: str,
    args: list[str],
    *,
    cwd: Path | None,
    evidence: list[dict[str, str | int | bool]],
) -> dict[str, object]:
    result = _run([executable, *args], cwd=cwd)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("odcli did not return a JSON envelope") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("odcli returned an unsuccessful envelope")
    evidence.append({"command": " ".join(args[:3]), "exit": result.returncode, "ok": True})
    return payload


def _clone(
    args: argparse.Namespace,
    root: Path,
    evidence: list[dict[str, str | int | bool]],
) -> tuple[Path, Path]:
    url = _required_url()
    members = (args.member_a, args.member_b)
    if not all(isinstance(member, str) and member for member in members):
        raise RuntimeError("TEST_GITLAB_MEMBER_A and TEST_GITLAB_MEMBER_B are required")
    checkouts = (root / "member-a", root / "member-b")
    for checkout, member in zip(checkouts, members, strict=True):
        clone_args = [
            "git",
            "clone",
            url,
            str(checkout),
            "--member-id",
            member,
            "--yes",
            "--format",
            "json",
        ]
        if args.mode in {"all", "clone"}:
            clone_args.extend(("--recurse-submodules",))
        _cli(args.odcli, clone_args, cwd=root, evidence=evidence)
        _run([args.git, "-C", str(checkout), "checkout", "-qb", f"odcli-e2e-{checkout.name}"])
    return checkouts


def _commit(
    args: argparse.Namespace,
    checkout: Path,
    member: str,
    *,
    label: str,
    evidence: list[dict[str, str | int | bool]],
) -> None:
    marker = checkout / "odcli-e2e-verification" / f"{label}.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"verification:{label}\n", encoding="utf-8")
    _git(args.git, checkout, "add", str(marker.relative_to(checkout)))
    _cli(
        args.odcli,
        [
            "git",
            "commit",
            f"verify {label}",
            "--repo",
            str(checkout),
            "--member-id",
            member,
            "--yes",
            "--format",
            "json",
        ],
        cwd=checkout,
        evidence=evidence,
    )


def _sync(
    args: argparse.Namespace,
    checkout: Path,
    member: str,
    evidence: list[dict[str, str | int | bool]],
) -> None:
    before = _git(args.git, checkout, "rev-parse", "HEAD")
    _cli(
        args.odcli,
        [
            "git",
            "sync",
            "--repo",
            str(checkout),
            "--member-id",
            member,
            "--base",
            args.target,
            "--push",
            "--yes",
            "--format",
            "json",
        ],
        cwd=checkout,
        evidence=evidence,
    )
    after = _git(args.git, checkout, "rev-parse", "HEAD")
    if not before or not after:
        raise RuntimeError("sync did not leave a verifiable HEAD")


def _mr(
    args: argparse.Namespace,
    checkout: Path,
    member: str,
    *,
    label: str,
    evidence: list[dict[str, str | int | bool]],
) -> None:
    description = checkout / f"description-{label}.md"
    description.write_text(f"GitLab member workflow {label}\n", encoding="utf-8")
    image = checkout / f"image-{label}.png"
    image.write_bytes(_PNG)
    command = [
        "git",
        "mr",
        "create",
        "--repo",
        str(checkout),
        "--member-id",
        member,
        "--target",
        args.target,
        "--title",
        f"Verify {label}",
        "--description-file",
        str(description),
        "--image",
        str(image),
        "--yes",
        "--format",
        "json",
    ]
    _cli(args.odcli, command, cwd=checkout, evidence=evidence)
    _cli(args.odcli, command, cwd=checkout, evidence=evidence)
    if _git(args.git, checkout, "status", "--porcelain"):
        raise RuntimeError("MR image was added to Git")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = (
        args.work_root.expanduser().resolve()
        if args.work_root
        else Path(tempfile.mkdtemp(prefix="odcli-gitlab-e2e-"))
    )
    root.mkdir(parents=True, exist_ok=True)
    evidence: list[dict[str, str | int | bool]] = []
    try:
        checkouts = _clone(args, root, evidence)
        if args.mode in {"all", "commit"}:
            _commit(args, checkouts[0], args.member_a, label="member-a", evidence=evidence)
            _commit(args, checkouts[1], args.member_b, label="member-b", evidence=evidence)
        if args.mode in {"all", "sync"}:
            _sync(args, checkouts[0], args.member_a, evidence)
            _sync(args, checkouts[1], args.member_b, evidence)
        if args.mode in {"all", "mr"}:
            _mr(args, checkouts[0], args.member_a, label="member-a", evidence=evidence)
            _mr(args, checkouts[1], args.member_b, label="member-b", evidence=evidence)
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "mode": args.mode, "error": _sanitize(str(exc))[:1200]},
                sort_keys=True,
            )
        )
        return 1
    else:
        product_shas = tuple(
            _git(args.git, checkout, "rev-parse", "HEAD") for checkout in checkouts
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": args.mode,
                    "product_shas": product_shas,
                    "script_sha": _script_sha(),
                    "steps": evidence,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        if not args.keep_local:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
