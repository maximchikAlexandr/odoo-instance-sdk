#!/usr/bin/env python3
"""Notify a private webhook when open pull requests conflict with main."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

JsonObject = dict[str, object]
JsonGetter = Callable[[str], object]


def github_getter(repository: str, token: str) -> JsonGetter:
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    def get(path: str) -> object:
        request = Request(
            f"{api_url}/repos/{repository}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    return get


def open_pull_requests(get: JsonGetter, base_branch: str) -> list[JsonObject]:
    pull_requests: list[JsonObject] = []
    page = 1
    while True:
        query = urlencode({"state": "open", "base": base_branch, "per_page": 100, "page": page})
        batch = get(f"/pulls?{query}")
        if not isinstance(batch, list):
            raise TypeError("GitHub returned an invalid pull-request list")
        pull_requests.extend(cast("list[JsonObject]", batch))
        if len(batch) < 100:
            return pull_requests
        page += 1


def conflicting_pull_requests(
    get: JsonGetter,
    base_branch: str,
    *,
    attempts: int = 6,
    retry_delay: float = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> list[JsonObject]:
    conflicts: list[JsonObject] = []
    for listed in open_pull_requests(get, base_branch):
        number = listed.get("number")
        if not isinstance(number, int):
            raise TypeError("GitHub returned a pull request without a number")

        pull_request: JsonObject | None = None
        for attempt in range(attempts):
            candidate = get(f"/pulls/{number}")
            if not isinstance(candidate, dict):
                raise TypeError(f"GitHub returned invalid details for PR #{number}")
            pull_request = cast("JsonObject", candidate)
            if pull_request.get("mergeable") is not None:
                break
            if attempt + 1 < attempts:
                sleep(retry_delay)
        if pull_request is None or pull_request.get("mergeable") is None:
            raise RuntimeError(f"GitHub did not compute mergeability for PR #{number}")
        if pull_request.get("mergeable") is not False:
            continue

        head = pull_request.get("head")
        base = pull_request.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise TypeError(f"GitHub returned incomplete refs for PR #{number}")
        conflicts.append(
            {
                "number": number,
                "url": pull_request.get("html_url"),
                "title": pull_request.get("title"),
                "draft": pull_request.get("draft", False),
                "head_branch": head.get("ref"),
                "head_sha": head.get("sha"),
                "base_branch": base.get("ref"),
                "base_sha": base.get("sha"),
            }
        )
    return conflicts


def deliver(webhook_url: str, payload: JsonObject, idempotency_key: str) -> None:
    parsed = urlsplit(webhook_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("conflict webhook must be an absolute HTTPS URL")
    request = Request(
        webhook_url,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"conflict webhook returned HTTP {response.status}")
    except HTTPError as error:
        raise RuntimeError(f"conflict webhook returned HTTP {error.code}") from None
    except URLError:
        raise RuntimeError("conflict webhook delivery failed") from None


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def main() -> int:
    repository = required_environment("GITHUB_REPOSITORY")
    token = required_environment("GITHUB_TOKEN")
    webhook_url = required_environment("MULTICA_CONFLICT_WEBHOOK_URL")
    base_branch = os.environ.get("CONFLICT_BASE_BRANCH", "main")
    base_sha = required_environment("GITHUB_SHA")

    conflicts = conflicting_pull_requests(github_getter(repository, token), base_branch)
    if not conflicts:
        print(f"No open pull requests conflict with {base_branch}.")
        return 0

    payload: JsonObject = {
        "event": "pull_request_conflicts",
        "repository": repository,
        "base_branch": base_branch,
        "base_sha": base_sha,
        "detected_at": datetime.now(UTC).isoformat(),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "conflicting_pull_requests": conflicts,
    }
    deliver(webhook_url, payload, f"merge-conflicts:{repository}:{base_sha}")
    print(f"Reported {len(conflicts)} conflicting pull request(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
