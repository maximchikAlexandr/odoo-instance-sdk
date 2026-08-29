"""One field-aware projection used by plans, diagnostics, and fingerprints."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from shlex import join
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.internal.sanitize import sanitize_terminal_text

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import ProcessStep
    from odoo_instance_sdk.internal.proc import PreparedStep

REDACTION_MARKER = "<redacted>"
_SECRET_KEY = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|master_pwd|admin_passwd|db_password)",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"(?P<prefix>(?:password|passwd|secret|token|api[_-]?key|master_pwd|admin_passwd|db_password)"
    r"\s*[:=]\s*)(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)


def _redact_text(value: str, secrets: tuple[str, ...], *, field: str) -> str:
    text = value
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTION_MARKER)
    if _SECRET_KEY.search(field) or field in {"argv", "environment", "stdin", "script", "error"}:
        text = _ASSIGNMENT.sub(r"\g<prefix>" + REDACTION_MARKER, text)
    return sanitize_terminal_text(text, preserve_newlines=field in {"stdin", "script"})


def redacted_projection(
    value: object,
    *,
    secrets: Iterable[str] = (),
    field: str = "",
) -> object:
    """Return a JSON-safe, secret-free projection without joining argv fields.

    ``argv`` values are projected one list element at a time.  Consequently a
    secret containing spaces cannot alter argument boundaries in a preview.
    """

    known_secrets = tuple(secrets)
    if isinstance(value, str):
        return _redact_text(value, known_secrets, field=field)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        projected: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            projected[name] = (
                REDACTION_MARKER
                if _SECRET_KEY.search(name)
                else redacted_projection(item, secrets=known_secrets, field=name)
            )
        return projected
    if isinstance(value, (list, tuple)):
        return [redacted_projection(item, secrets=known_secrets, field=field) for item in value]
    return _redact_text(repr(value), known_secrets, field=field)


def redact(value: object, *, secrets: Iterable[str] = (), field: str = "") -> object:
    """Short compatibility alias for the canonical projection function."""

    return redacted_projection(value, secrets=secrets, field=field)


def project_process_step(step: PreparedStep) -> ProcessStep:
    """Project a private prepared process while preserving argv boundaries."""

    from odoo_instance_sdk.execution import ProcessStep

    argv = step.argv
    secrets = step.secret_values
    projected_argv = tuple(
        cast("str", redacted_projection(argument, secrets=secrets, field="argv"))
        for argument in argv
    )
    environment = step.environment
    projected_environment = tuple(
        (
            str(key),
            str(redacted_projection(value, secrets=secrets, field=str(key))),
        )
        for key, value in environment
    )
    stdin = step.stdin
    input_preview = (
        cast(
            "str",
            redacted_projection(
                stdin.decode("utf-8", errors="replace"), secrets=secrets, field="stdin"
            ),
        )
        if stdin is not None
        else None
    )
    return ProcessStep(
        step_id=step.step_id,
        argv=projected_argv,
        display=join(projected_argv),
        executable=str(projected_argv[0]) if projected_argv else "",
        cwd=step.cwd,
        environment_policy=step.environment_policy,
        environment_overrides=projected_environment,
        input_preview=input_preview,
        timeout=step.timeout,
        mode=step.mode,
        read_only=step.read_only,
        mutating=step.mutating,
        interactive=step.interactive,
        long_running=step.long_running,
    )


__all__ = ["REDACTION_MARKER", "project_process_step", "redact", "redacted_projection"]
