"""Private Git askpass entrypoint used only by a credential-scoped child."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from urllib.parse import urlsplit


def _prompt_authority(prompt: str) -> tuple[str, int | None] | None:
    """Return the HTTPS authority Git is asking credentials for."""
    match = re.search(r"https://[^'\"\s]+", prompt, re.IGNORECASE)
    if match is None:
        return None
    try:
        parsed = urlsplit(match.group(0))
        if parsed.scheme.casefold() != "https" or parsed.hostname is None:
            return None
        return parsed.hostname.casefold(), parsed.port
    except ValueError:
        return None


def _configured_authority(value: str | None) -> tuple[str, int | None] | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value if "://" in value else f"//{value}")
        if parsed.hostname is None or parsed.path not in ("", "/"):
            return None
        return parsed.hostname.casefold(), parsed.port
    except ValueError:
        return None


def _read_payload(channel_path: str) -> Mapping[str, str] | None:
    """Narrow untyped JSON at the file adapter before policy validation."""
    try:
        with open(channel_path, encoding="utf-8") as channel:
            raw = json.load(channel)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    payload: dict[str, str] = {}
    for key in ("host", "login", "token"):
        value = raw.get(key)
        if not isinstance(value, str):
            return None
        payload[key] = value
    return payload


def main() -> int:
    prompt = " ".join(sys.argv[1:]).casefold()
    channel_path = os.environ.get("ODCLI_GITLAB_TOKEN_FILE")
    value: str | None = None
    if channel_path:
        payload = _read_payload(channel_path)
        if payload is None:
            return 1
        prompt_authority = _prompt_authority(prompt)
        configured_authority = _configured_authority(os.environ.get("ODCLI_GITLAB_HOST"))
        payload_authority = _configured_authority(payload["host"])
        if (
            prompt_authority is None
            or configured_authority is None
            or payload_authority is None
            or prompt_authority != configured_authority
            or payload_authority != configured_authority
        ):
            return 1
        key = "login" if "username" in prompt or "user" in prompt else "token"
        value = payload.get(key)
        if not isinstance(value, str):
            return 1
    else:
        # Keep the standalone helper backwards-compatible for package users;
        # credentialed Git execution uses the owner-only channel above.
        key = "GITLAB_LOGIN" if "username" in prompt or "user" in prompt else "GITLAB_TOKEN"
        value = os.environ.get(key)
    if not value:
        return 1
    sys.stdout.write(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
