from __future__ import annotations

import re

_REDACT_RE = re.compile(
    r'(["\']?(?:master_pwd|password|admin_passwd)["\']?\s*[:=]\s*)(?:'
    r'"([^"]*)"|'
    r"'([^']*)'|"
    r'([^"\'\s,]+)'
    r")",
    re.IGNORECASE,
)


def _redact_replace(match: re.Match[str]) -> str:
    prefix = match.group(1)
    if match.group(2) is not None:
        return f'{prefix}"***"'
    if match.group(3) is not None:
        return f"{prefix}'***'"
    return f"{prefix}***"


def format_error(exc: str | BaseException) -> str:
    return _REDACT_RE.sub(_redact_replace, str(exc))
