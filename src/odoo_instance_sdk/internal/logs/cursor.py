"""Opaque, identity-bound cursors for structured Odoo logs."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from odoo_instance_sdk.exceptions import LogQueryError

_TOKEN_LIMIT = 4096


@dataclass(frozen=True, slots=True)
class CursorPosition:
    """A decoded position between source records."""

    project_id: str
    environment_id: str
    run_id: str
    generation: int
    offset: int
    fingerprint: str


def encode_cursor(position: CursorPosition) -> str:
    """Return a URL-safe token that does not publish the byte offset."""
    body = {
        "e": position.environment_id,
        "f": position.fingerprint,
        "g": position.generation,
        "o": position.offset,
        "p": position.project_id,
        "r": position.run_id,
        "s": "odoo",
        "v": 1,
    }
    digest = hashlib.sha256(
        json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    payload = json.dumps({**body, "c": digest}, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(
    token: str,
    *,
    project_id: str,
    environment_id: str,
    run_id: str | None = None,
) -> CursorPosition:
    """Decode a cursor or raise a stable path-free error."""
    position = _decode_token(token)
    if position.project_id != project_id or position.environment_id != environment_id:
        raise LogQueryError("cursor_wrong_instance", "cursor belongs to another instance")
    if run_id is not None and position.run_id != run_id:
        raise LogQueryError("cursor_wrong_instance", "cursor belongs to another instance")
    return position


def fingerprint(data: bytes, offset: int) -> str:
    """Hash the bytes immediately before a record boundary."""
    if offset <= 0:
        return ""
    window = data[max(0, offset - 64) : offset]
    return hashlib.sha256(window).hexdigest()[:16]


def _decode_token(token: str) -> CursorPosition:
    if not isinstance(token, str) or not token or len(token) > _TOKEN_LIMIT:
        raise LogQueryError("cursor_invalid", "cursor is invalid")
    padded = token + ("=" * (-len(token) % 4))
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError) as exc:
        raise LogQueryError("cursor_invalid", "cursor is invalid") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("s") != "odoo":
        raise LogQueryError("cursor_invalid", "cursor is invalid")
    checksum = payload.get("c")
    body = {key: value for key, value in payload.items() if key != "c"}
    expected = hashlib.sha256(
        json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    if checksum != expected:
        raise LogQueryError("cursor_invalid", "cursor is invalid")
    try:
        position = CursorPosition(
            project_id=str(payload["p"]),
            environment_id=str(payload["e"]),
            run_id=str(payload["r"]),
            generation=payload["g"],
            offset=payload["o"],
            fingerprint=str(payload["f"]),
        )
    except (KeyError, TypeError) as exc:
        raise LogQueryError("cursor_invalid", "cursor is invalid") from exc
    if (
        type(position.generation) is not int
        or type(position.offset) is not int
        or position.generation < 1
        or position.offset < 0
    ):
        raise LogQueryError("cursor_invalid", "cursor is invalid")
    return position
