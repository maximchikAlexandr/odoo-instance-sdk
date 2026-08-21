from __future__ import annotations

import re

_MAX_LAST_ERROR = 2000

_SECRET_PATTERNS = [
    re.compile(r"(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"(admin_passwd|db_password|master_pwd)\s*=\s*\S+", re.IGNORECASE),
]

_ENV_VAR_RE = re.compile(r"\$\{[^}]*\}")
_PATH_LIKE_RE = re.compile(r"(/[^,\s]{4,}|[A-Za-z]:[/\\][^,\s]{4,})")


def sanitize_last_error(value: str | None) -> str | None:
    if value is None:
        return None
    text = value
    for pat in _SECRET_PATTERNS:
        text = pat.sub("<redacted>", text)
    text = _ENV_VAR_RE.sub("<env>", text)
    text = _PATH_LIKE_RE.sub("<path>", text)
    text = text.replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    if len(text) > _MAX_LAST_ERROR:
        text = text[:_MAX_LAST_ERROR]
    return text


def sanitize_event_message(value: str | None) -> str | None:
    if value is None:
        return None
    return sanitize_last_error(value)
