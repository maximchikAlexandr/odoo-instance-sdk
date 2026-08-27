from __future__ import annotations

import re

_MAX_LAST_ERROR = 2000

_SECRET_PATTERNS = [
    re.compile(
        r"\b(password|passwd|secret|token|api[_-]?key|admin_passwd|db_password|master_pwd)"
        r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|\S+)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:postgres(?:ql)?|mysql)://[^\s]+", re.IGNORECASE),
    re.compile(r"://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
]

_ENV_VAR_RE = re.compile(r"\$\{[^}]*\}")
_PATH_LIKE_RE = re.compile(r"(?<![A-Za-z0-9_-])(/[^,\s]{4,}|[A-Za-z]:[/\\][^,\s]{4,})")

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize_terminal_text(value: str, *, preserve_newlines: bool = False) -> str:
    """Replace terminal control characters with visible, inert text.

    The escaped representation keeps diagnostics and Rich renderables
    single-line without allowing C0/C1 controls, including ESC/CSI, BEL, or
    DEL, to reach a terminal stream.  A generated document may opt into
    preserving line-feed separators while all other controls remain escaped.
    """

    def replace(match: re.Match[str]) -> str:
        if preserve_newlines and match.group() == "\n":
            return "\n"
        return f"\\x{ord(match.group()):02x}"

    return _CONTROL_CHAR_RE.sub(replace, value)


def sanitize_last_error(value: str | None) -> str | None:
    if value is None:
        return None
    text = value
    for pat in _SECRET_PATTERNS:
        text = pat.sub("<redacted>", text)
    text = _ENV_VAR_RE.sub("<env>", text)
    text = _PATH_LIKE_RE.sub("<path>", text)
    text = sanitize_terminal_text(text)
    text = " ".join(text.split())
    if len(text) > _MAX_LAST_ERROR:
        text = text[:_MAX_LAST_ERROR]
    return text


def sanitize_event_message(value: str | None) -> str | None:
    if value is None:
        return None
    return sanitize_last_error(value)
