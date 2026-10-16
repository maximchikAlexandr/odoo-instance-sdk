"""Core exact-host askpass helper for credentialed Git children."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import TypedDict, cast

from odoo_instance_sdk.execution import JsonValue

_HOST = re.compile(r"https://([^/'\s]+)", re.IGNORECASE)


class _AskpassEntry(TypedDict):
    host: str
    login: str
    token: str


def _read_entries(payload: JsonValue) -> tuple[_AskpassEntry, ...]:
    raw_entries: tuple[JsonValue, ...]
    if isinstance(payload, dict):
        raw_entries = (payload,)
    elif isinstance(payload, list):
        raw_entries = tuple(payload)
    else:
        return ()
    entries: list[_AskpassEntry] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            return ()
        host = raw_entry.get("host")
        login = raw_entry.get("login")
        token = raw_entry.get("token")
        if not isinstance(host, str) or not host:
            return ()
        if not isinstance(login, str) or not login:
            return ()
        if not isinstance(token, str) or not token:
            return ()
        entries.append({"host": host, "login": login, "token": token})
    return tuple(entries)


def _prompt_host(prompt: str) -> str | None:
    match = _HOST.search(prompt)
    return match.group(1).rsplit("@", 1)[-1].casefold() if match else None


def main() -> int:
    channel_path = os.environ.get("ODCLI_GITLAB_ASKPASS_FILE", "")
    prompt = " ".join(sys.argv[1:])
    requested_host = _prompt_host(prompt)
    if not channel_path or requested_host is None:
        return 1
    try:
        payload = cast("JsonValue", json.loads(Path(channel_path).read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError):
        return 1
    entries = _read_entries(payload)
    if not entries:
        return 1
    for entry in entries:
        if str(entry.get("host", "")).casefold() != requested_host:
            continue
        key = "login" if "username" in prompt.casefold() else "token"
        value = entry.get(key)
        if isinstance(value, str) and value:
            print(value)
            return 0
    return 1


if __name__ == "__main__":  # pragma: no cover - invoked by Git
    raise SystemExit(main())
