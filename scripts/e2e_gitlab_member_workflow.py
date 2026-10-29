#!/usr/bin/env python3
"""Run the private GitLab member-auth workflow from a disposable checkout.

The script is intentionally an independent test harness, not a second CLI
implementation.  It passes member IDs to the public CLI, never reads token
values from process arguments, reads the selected token only from an owner-only
source for redaction, emits bounded JSON summaries, and removes only its own
local root.
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
from urllib.parse import urljoin, urlsplit

from odoo_instance_sdk.internal.gitlab_credentials import (
    normalize_member_id,
    read_gitlab_credential_mappings,
    read_gitlab_environment,
)

_PNG: Final[bytes] = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_SECRET_TEXT: Final[re.Pattern[str]] = re.compile(
    r"(?i)((?:private-token|password|token|authorization)\s*[:=]\s*)[^\s,}]+"
)
_SHA: Final[re.Pattern[str]] = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)
_URL: Final[re.Pattern[str]] = re.compile(r"(?:https?://|git@)[^\s,}]+", re.IGNORECASE)
_UUID: Final[re.Pattern[str]] = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("all", "clone", "commit", "sync", "mr", "multica-cache"),
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
        "--member-id",
        default=os.environ.get("TEST_GITLAB_MEMBER"),
        help="one selected member; defaults to TEST_GITLAB_MEMBER",
    )
    parser.add_argument(
        "--ticket-a",
        default=os.environ.get("TEST_GITLAB_TICKET_A", "MYL-481"),
        help="non-secret Ticket key for initialized A commit; defaults to MYL-481",
    )
    parser.add_argument("--repository-a", default=os.environ.get("TEST_REPOSITORY_URL"))
    parser.add_argument("--repository-b", default=os.environ.get("TEST_REPOSITORY_B_URL"))
    parser.add_argument("--repository-b-ours", default=os.environ.get("TEST_REPOSITORY_B_OURS_URL"))
    parser.add_argument("--repository-b-odoo", default=os.environ.get("TEST_REPOSITORY_B_ODOO_URL"))
    parser.add_argument(
        "--repository-independent",
        default=os.environ.get("TEST_REPOSITORY_INDEPENDENT_URL"),
    )
    parser.add_argument("--odcli-multica", default="odcli-multica")
    parser.add_argument("--multica", default="multica")
    parser.add_argument("--profile", default=os.environ.get("TEST_MULTICA_PROFILE"))
    parser.add_argument("--helper-dir", type=Path, default=os.environ.get("TEST_GIT_HELPER_DIR"))
    parser.add_argument("--core-project", type=Path, default=os.environ.get("TEST_CORE_PROJECT"))
    parser.add_argument("--sdk-sha", default=os.environ.get("TEST_SDK_SHA"))
    parser.add_argument("--multica-project", default=os.environ.get("TEST_MULTICA_PROJECT"))
    parser.add_argument("--workspace-id", default=os.environ.get("TEST_MULTICA_WORKSPACE"))
    parser.add_argument("--cache-member-id", default=os.environ.get("TEST_GITLAB_CACHE_MEMBER"))
    parser.add_argument(
        "--cache-owner-mismatch-member",
        default=os.environ.get("TEST_GITLAB_CACHE_OWNER_MISMATCH_MEMBER"),
    )
    parser.add_argument(
        "--cache-inactive-member", default=os.environ.get("TEST_GITLAB_CACHE_INACTIVE_MEMBER")
    )
    parser.add_argument(
        "--cache-broader-scope-member",
        default=os.environ.get("TEST_GITLAB_CACHE_BROADER_SCOPE_MEMBER"),
    )
    parser.add_argument(
        "--cache-granular-scope-member",
        default=os.environ.get("TEST_GITLAB_CACHE_GRANULAR_SCOPE_MEMBER"),
    )
    parser.add_argument(
        "--cache-incompatible-member",
        default=os.environ.get("TEST_GITLAB_CACHE_INCOMPATIBLE_MEMBER"),
    )
    parser.add_argument("--member-b", default=os.environ.get("TEST_GITLAB_MEMBER_B"))
    parser.add_argument("--member-missing", default=os.environ.get("TEST_GITLAB_MEMBER_MISSING"))
    parser.add_argument("--member-invalid", default=os.environ.get("TEST_MULTICA_MEMBER_INVALID"))
    parser.add_argument("--member-nonhuman", default=os.environ.get("TEST_MULTICA_MEMBER_NONHUMAN"))
    parser.add_argument(
        "--member-cross-workspace", default=os.environ.get("TEST_MULTICA_MEMBER_CROSS_WORKSPACE")
    )
    parser.add_argument("--member-stale", default=os.environ.get("TEST_MULTICA_MEMBER_STALE"))
    parser.add_argument("--cache-host", default=os.environ.get("TEST_GITLAB_CACHE_HOST"))
    parser.add_argument("--issue-a", default=os.environ.get("TEST_MULTICA_ISSUE_A"))
    parser.add_argument("--run-a", default=os.environ.get("TEST_MULTICA_RUN_A"))
    parser.add_argument("--issue-b", default=os.environ.get("TEST_MULTICA_ISSUE_B"))
    parser.add_argument("--run-b", default=os.environ.get("TEST_MULTICA_RUN_B"))
    parser.add_argument(
        "--checkout-a", type=Path, default=os.environ.get("TEST_MULTICA_CHECKOUT_A")
    )
    parser.add_argument(
        "--checkout-b", type=Path, default=os.environ.get("TEST_MULTICA_CHECKOUT_B")
    )
    return parser


def _repository_roles(args: argparse.Namespace) -> dict[str, str]:
    roles = {
        "A": args.repository_a,
        "B": args.repository_b,
        "B/ours": args.repository_b_ours,
        "B/odoo": args.repository_b_odoo,
        "Independent": args.repository_independent,
    }
    missing = tuple(role for role, url in roles.items() if not isinstance(url, str) or not url)
    if missing:
        raise RuntimeError("missing private repository roles: " + ", ".join(missing))
    return roles


def _repository_identity(value: str, *, base: str | None = None) -> str:
    candidate = value.strip()
    if "://" not in candidate:
        if base is None:
            raise RuntimeError("repository role URL must be an HTTP(S) URL")
        # Git resolves relative submodule URLs from the repository URL treated
        # as a directory, preserving the superproject's namespace.
        candidate = urljoin(f"{base.rstrip('/')}/", candidate)
    parsed = urlsplit(candidate)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("repository role URL must be an HTTP(S) URL")
    path = parsed.path.rstrip("/").removesuffix(".git").casefold()
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    return f"{parsed.scheme.casefold()}://{parsed.hostname.casefold()}:{parsed.port or default_port}{path}"


def _script_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _secret_corpus(member_id: str) -> tuple[str, ...]:
    """Read the selected member's token values from the owner-only source."""
    selected_member = normalize_member_id(member_id).casefold()
    mappings = read_gitlab_credential_mappings()
    token_keys = {
        mapping.token_key for mapping in mappings if mapping.user_id.casefold() == selected_member
    }
    environment = read_gitlab_environment()
    values = tuple(dict.fromkeys(environment[key] for key in token_keys if environment.get(key)))
    if not values:
        raise RuntimeError("selected member credential is unavailable from owner-only source")
    return values


def _selected_member(args: argparse.Namespace) -> str:
    if not isinstance(args.member_id, str) or not args.member_id:
        raise RuntimeError("TEST_GITLAB_MEMBER is required")
    return args.member_id


def _sanitize(text: str, *, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        text = text.replace(secret, "<redacted>")
    text = _SECRET_TEXT.sub(r"\1<redacted>", text)
    text = _URL.sub("<repository-url>", text)
    text = _UUID.sub("<member-id>", text)
    return _SHA.sub("<sha>", text)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    secrets: tuple[str, ...] = (),
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
        detail = _sanitize(result.stderr or result.stdout, secrets=secrets)[-1200:]
        raise RuntimeError(f"command failed ({result.returncode}): {argv[0]} {detail}")
    return result


def _git(git: str, root: Path, *args: str, secrets: tuple[str, ...] = ()) -> str:
    return _run([git, "-C", str(root), *args], secrets=secrets).stdout.strip()


def _cli(
    executable: str,
    args: list[str],
    *,
    cwd: Path | None,
    evidence: list[dict[str, str | int | bool]],
    secrets: tuple[str, ...],
) -> dict[str, object]:
    result = _run([executable, *args], cwd=cwd, secrets=secrets)
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
    secrets: tuple[str, ...],
) -> dict[str, Path]:
    roles = _repository_roles(args)
    if not isinstance(args.member_id, str) or not args.member_id:
        raise RuntimeError("TEST_GITLAB_MEMBER is required")
    checkouts = {
        "A": root / "a",
        "B": root / "b",
        "B/ours": root / "b-ours",
        "B/odoo": root / "b-odoo",
        "Independent": root / "independent",
    }
    for role, checkout in checkouts.items():
        url = roles[role]
        clone_args = [
            "git",
            "clone",
            url,
            str(checkout),
            "--member-id",
            args.member_id,
            "--yes",
            "--format",
            "json",
        ]
        if role == "B":
            clone_args.extend(("--recurse-submodules",))
        _cli(args.odcli, clone_args, cwd=root, evidence=evidence, secrets=secrets)
        _run(
            [args.git, "-C", str(checkout), "checkout", "-qb", f"odcli-e2e-{checkout.name}"],
            secrets=secrets,
        )
    _assert_recursive_topology(args, checkouts["B"], roles, secrets=secrets)
    return checkouts


def _assert_recursive_topology(
    args: argparse.Namespace,
    checkout: Path,
    roles: dict[str, str],
    *,
    secrets: tuple[str, ...],
) -> None:
    configured = _git(
        args.git,
        checkout,
        "config",
        "--file",
        ".gitmodules",
        "--get-regexp",
        r"^submodule\..*\.url$",
        secrets=secrets,
    )
    base_origin = roles["B"]
    configured_identities = {
        _repository_identity(line.split(maxsplit=1)[1], base=base_origin)
        for line in configured.splitlines()
        if len(line.split(maxsplit=1)) == 2
    }
    expected_identities = {
        _repository_identity(roles["B/ours"]),
        _repository_identity(roles["B/odoo"]),
    }
    if not expected_identities <= configured_identities:
        raise RuntimeError("B does not point to both supplied submodule roles")
    status = _git(args.git, checkout, "submodule", "status", "--recursive", secrets=secrets)
    if len(status.splitlines()) < 2:
        raise RuntimeError("B recursive clone did not initialize both submodules")


def _commit(
    args: argparse.Namespace,
    checkout: Path,
    member: str,
    *,
    label: str,
    ticket: str | None,
    evidence: list[dict[str, str | int | bool]],
    secrets: tuple[str, ...],
) -> None:
    marker = checkout / "odcli-e2e-verification" / f"{label}.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"verification:{label}\n", encoding="utf-8")
    _git(args.git, checkout, "add", str(marker.relative_to(checkout)), secrets=secrets)
    command = [
        "git",
        "commit",
        f"verify {label}",
        "--repo",
        str(checkout),
        "--member-id",
        member,
    ]
    if ticket is not None:
        command.extend(("--ticket", ticket))
    command.extend(("--yes", "--format", "json"))
    payload = _cli(
        args.odcli,
        command,
        cwd=checkout,
        evidence=evidence,
        secrets=secrets,
    )
    if ticket is not None:
        result = payload.get("result")
        output_args = result.get("args") if isinstance(result, dict) else None
        if not isinstance(output_args, list) or not any(
            isinstance(value, str) and ticket in value for value in output_args
        ):
            raise RuntimeError("A commit output did not include the requested Ticket")


def _sync(
    args: argparse.Namespace,
    checkout: Path,
    member: str,
    evidence: list[dict[str, str | int | bool]],
    secrets: tuple[str, ...],
    *,
    publish: bool,
) -> None:
    before = _git(args.git, checkout, "rev-parse", "HEAD", secrets=secrets)
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
            *(("--push",) if publish else ()),
            "--yes",
            "--format",
            "json",
        ],
        cwd=checkout,
        evidence=evidence,
        secrets=secrets,
    )
    after = _git(args.git, checkout, "rev-parse", "HEAD", secrets=secrets)
    if not before or not after:
        raise RuntimeError("sync did not leave a verifiable HEAD")


def _mr(
    args: argparse.Namespace,
    checkout: Path,
    member: str,
    *,
    label: str,
    evidence: list[dict[str, str | int | bool]],
    secrets: tuple[str, ...],
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
    _cli(args.odcli, command, cwd=checkout, evidence=evidence, secrets=secrets)
    _cli(args.odcli, command, cwd=checkout, evidence=evidence, secrets=secrets)
    image.write_bytes(_PNG[:-1] + bytes(((_PNG[-1] + 1) % 256,)))
    _cli(args.odcli, command, cwd=checkout, evidence=evidence, secrets=secrets)
    status_lines = {
        line
        for line in _git(
            args.git,
            checkout,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            secrets=secrets,
        ).splitlines()
        if line
    }
    expected_untracked = {f"?? {description.name}", f"?? {image.name}"}
    if status_lines != expected_untracked:
        raise RuntimeError("MR changed tracked/index files")
    indexed_assets = _git(
        args.git,
        checkout,
        "ls-files",
        "--stage",
        "--",
        description.name,
        image.name,
        secrets=secrets,
    )
    if indexed_assets:
        raise RuntimeError("MR assets entered tracked/index files")


def _member_observation(value: object) -> tuple[str, str] | None:
    if isinstance(value, dict):
        member_id = value.get("member_id")
        source = value.get("member_source")
        if isinstance(member_id, str) and source in {"root_creator", "explicit"}:
            return member_id, source
        for child in value.values():
            found = _member_observation(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _member_observation(child)
            if found is not None:
                return found
    return None


def _multica_sync_command(
    args: argparse.Namespace,
    *,
    checkout: Path,
    issue: str,
    run_id: str,
    member_id: str | None,
    dry_run: bool,
) -> list[str]:
    command = [
        args.odcli_multica,
        "git",
        "sync",
        "--project",
        str(args.core_project),
        "--multica-project",
        args.multica_project,
        "--issue",
        issue,
        "--run",
        run_id,
        "--repository-url",
        args.repository_a,
        "--profile",
        args.profile,
        "--workspace-id",
        args.workspace_id,
    ]
    if member_id is not None:
        command.extend(("--member-id", member_id))
    command.extend((str(checkout), "--format", "json"))
    if dry_run:
        command.append("--dry-run")
    return command


def _integrated_multica_cache(
    args: argparse.Namespace,
    root: Path,
    evidence: list[dict[str, str | int | bool]],
) -> tuple[str, ...]:
    from scripts._gitlab_member_cache import integrated_multica_cache

    return integrated_multica_cache(args, root, evidence)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    secrets: tuple[str, ...] = ()
    root = (
        args.work_root.expanduser().resolve()
        if args.work_root
        else Path(
            tempfile.mkdtemp(
                prefix="odcli-gitlab-e2e-",
                dir=Path.cwd() if args.mode == "multica-cache" else None,
            )
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    evidence: list[dict[str, str | int | bool]] = []
    try:
        if args.mode == "multica-cache":
            secrets = _integrated_multica_cache(args, root, evidence)
        else:
            member = _selected_member(args)
            secrets = _secret_corpus(member)
            checkouts = _clone(args, root, evidence, secrets)
            if args.mode in {"all", "commit"}:
                for role in ("A", "B/ours", "Independent"):
                    _commit(
                        args,
                        checkouts[role],
                        member,
                        label=role.replace("/", "-"),
                        ticket=args.ticket_a if role == "A" else None,
                        evidence=evidence,
                        secrets=secrets,
                    )
            if args.mode in {"all", "sync"}:
                parent_gitlink_before = _git(
                    args.git, checkouts["B"], "ls-files", "-s", secrets=secrets
                )
                _sync(args, checkouts["A"], member, evidence, secrets, publish=True)
                _sync(args, checkouts["B/ours"], member, evidence, secrets, publish=True)
                _sync(args, checkouts["Independent"], member, evidence, secrets, publish=True)
                _sync(args, checkouts["B"], member, evidence, secrets, publish=False)
                _require(
                    _git(args.git, checkouts["B"], "ls-files", "-s", secrets=secrets)
                    == parent_gitlink_before,
                    "recursive sync changed the B superproject gitlink",
                )
            if args.mode in {"all", "mr"}:
                for role in ("A", "B/ours"):
                    _mr(
                        args,
                        checkouts[role],
                        member,
                        label=role.replace("/", "-"),
                        evidence=evidence,
                        secrets=secrets,
                    )
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": args.mode,
                    "error": _sanitize(str(exc), secrets=secrets)[:1200],
                },
                sort_keys=True,
            )
        )
        return 1
    else:
        product_shas = (
            {
                "task_a": _git(
                    args.git,
                    args.checkout_a.expanduser().resolve(),
                    "rev-parse",
                    "HEAD",
                    secrets=secrets,
                ),
                "task_b": _git(
                    args.git,
                    args.checkout_b.expanduser().resolve(),
                    "rev-parse",
                    "HEAD",
                    secrets=secrets,
                ),
            }
            if args.mode == "multica-cache"
            else {
                role: _git(args.git, checkout, "rev-parse", "HEAD", secrets=secrets)
                for role, checkout in checkouts.items()
            }
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
