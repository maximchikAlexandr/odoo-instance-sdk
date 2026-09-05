"""One field-aware projection used by plans, diagnostics, and fingerprints."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from shlex import join
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.internal.sanitize import sanitize_terminal_text

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import ProcessStep
    from odoo_instance_sdk.internal.proc import PreparedStep

REDACTION_MARKER = "<redacted>"
type RedactionValue = (
    None
    | bool
    | int
    | float
    | str
    | bytes
    | BaseException
    | Mapping[str, RedactionValue]
    | Sequence[RedactionValue]
)
type RedactedValue = (
    None | bool | int | float | str | list[RedactedValue] | dict[str, RedactedValue]
)
_SECRET_KEY = re.compile(
    r"(?:^|[-_ .])(?:password|passwd|pwd|secret|token|cookie|jwt|oauth|api[-_ ]?key|"
    r"master[-_ ]?pwd|admin[-_ ]?passwd|db[-_ ]?password|database[-_ ]?url|sentry[-_ ]?dsn|"
    r"docker[-_ ]?auth[-_ ]?config|authorization|bearer|credential|private[-_ ]?key|"
    r"access[-_ ]?key|auth|refresh|client[-_ ]?secret)(?:$|[-_ .])",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"(?P<prefix>(?:[A-Za-z0-9_. -]*?(?:password|passwd|pwd|secret|token|cookie|jwt|oauth|"
    r"api[-_ ]?key|dsn|database[-_ ]?url|sentry[-_ ]?dsn|docker[-_ ]?auth[-_ ]?config|"
    r"authorization|bearer|credential|private[-_ ]?key|access[-_ ]?key|auth)"
    r"[A-Za-z0-9_. -]*\s*[:=]\s*))"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE | re.DOTALL,
)
_URI_USERINFO = re.compile(r"(?P<prefix>[A-Za-z][A-Za-z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@")
_SENSITIVE_ARG = re.compile(
    r"^-*(?:(?:[A-Za-z0-9]+[-_])*(?:password|passwd|pwd|secret|token|cookie|jwt|oauth|"
    r"api[-_]?key|dsn|authorization|bearer|credential|private[-_]?key|access[-_]?key|"
    r"auth|refresh|client[-_]?secret)(?:[-_][A-Za-z0-9]+)*)$",
    re.IGNORECASE,
)
_HEADER_OPTION = re.compile(r"^(?:-H|--headers?)$", re.IGNORECASE)
_SENSITIVE_HEADER = re.compile(
    r"^\s*(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*.+$",
    re.IGNORECASE | re.DOTALL,
)
_BEARER_VALUE = re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_JWT_VALUE = re.compile(
    r"(?:^|\s)eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:$|\s)",
)
_SAFE_ENV_KEY = re.compile(r"^(?:LANG|LC_[A-Z0-9_]+|TERM|TZ|PYTHONUNBUFFERED)$")


def capture_sensitive_argv_indices(
    argv: Sequence[str], *, secrets: Iterable[str] = ()
) -> tuple[int, ...]:
    """Capture credential-bearing argv positions before a plan is exposed."""
    known_secrets = tuple(secret for secret in secrets if secret)
    indices: set[int] = set()
    for index, argument in enumerate(argv):
        name, separator, _value = argument.partition("=")
        if separator and name.startswith("-") and _SENSITIVE_ARG.fullmatch(name):
            indices.add(index)
        if argument.startswith("-") and _SENSITIVE_ARG.fullmatch(argument):
            indices.add(index)
            if index + 1 < len(argv):
                indices.add(index + 1)
        if (
            _HEADER_OPTION.fullmatch(argument)
            and index + 1 < len(argv)
            and _SENSITIVE_HEADER.fullmatch(argv[index + 1])
        ):
            indices.add(index + 1)
        if (
            _URI_USERINFO.search(argument)
            or _BEARER_VALUE.search(argument)
            or _JWT_VALUE.search(argument)
            or any(secret in argument for secret in known_secrets)
        ):
            indices.add(index)
    return tuple(sorted(indices))


def _redact_argv(
    argv: Sequence[str],
    secrets: tuple[str, ...],
    sensitive_indices: tuple[int, ...] | None = None,
) -> tuple[str, ...]:
    """Redact captured sensitive positions while preserving argv boundaries."""
    projected: list[str] = []
    indices = set(
        capture_sensitive_argv_indices(argv, secrets=secrets)
        if sensitive_indices is None
        else sensitive_indices
    )
    for index, argument in enumerate(argv):
        if index in indices:
            if _URI_USERINFO.search(argument):
                projected.append(
                    cast("str", redacted_projection(argument, secrets=secrets, field="argv"))
                )
                continue
            name, separator, _value = argument.partition("=")
            if not separator and argument.startswith("-") and _SENSITIVE_ARG.fullmatch(argument):
                projected.append(argument)
            elif separator:
                projected.append(f"{name}={REDACTION_MARKER}")
            else:
                projected.append(REDACTION_MARKER)
            continue
        projected.append(cast("str", redacted_projection(argument, secrets=secrets, field="argv")))
    return tuple(projected)


def redacted_argv(
    argv: Sequence[str],
    *,
    secrets: Iterable[str] = (),
    sensitive_indices: Iterable[int] | None = None,
) -> tuple[str, ...]:
    """Return the canonical safe argv projection used by plans and errors."""
    return _redact_argv(
        tuple(argv), tuple(secrets), None if sensitive_indices is None else tuple(sensitive_indices)
    )


def redacted_environment(
    environment: Sequence[tuple[str, str]], *, secrets: Iterable[str] = ()
) -> tuple[tuple[str, str], ...]:
    """Return explicit environment metadata without inherited values."""
    known_secrets = tuple(secrets)
    return tuple(
        (
            str(key),
            str(redacted_projection(value, secrets=known_secrets, field=str(key)))
            if _SAFE_ENV_KEY.fullmatch(str(key)) and not _SECRET_KEY.search(str(key))
            else REDACTION_MARKER,
        )
        for key, value in environment
    )


def captured_secret_values(step: PreparedStep) -> tuple[str, ...]:
    """Collect private values that must be scrubbed from result text too."""
    values = list(step.secret_values)
    # Inherited environment values stay in the private snapshot and are not
    # copied into diagnostics: doing so would both widen the secret surface
    # and make harmless output depend on ambient process state.  Explicit
    # overrides are captured below and are always treated as private values.
    values.extend(value for _key, value in step.environment_overrides if value)
    return _argv_secret_values(step.argv, step.sensitive_argv_indices, initial=values)


def captured_argv_secret_values(
    argv: Sequence[str], *, secrets: Iterable[str] = ()
) -> tuple[str, ...]:
    """Return private argv values captured for redacting child output.

    The process boundary records sensitive positions before any public result
    exists.  Reusing that capture here prevents a child that echoes its argv
    from reintroducing a credential into a generic ``CommandResult``.
    """
    return _argv_secret_values(
        argv,
        capture_sensitive_argv_indices(argv, secrets=secrets),
        initial=[secret for secret in secrets if secret],
    )


def _argv_secret_values(
    argv: Sequence[str], indices: Iterable[int], *, initial: Iterable[str] = ()
) -> tuple[str, ...]:
    values = list(initial)
    for index in indices:
        if not 0 <= index < len(argv):
            continue
        argument = argv[index]
        if "=" in argument:
            value = argument.split("=", 1)[1]
            if value:
                values.append(value)
        elif not argument.startswith("-") and argument:
            values.append(argument)
    return tuple(dict.fromkeys(value for value in values if value))


def _redact_text(value: str, secrets: tuple[str, ...], *, field: str) -> str:
    text = value
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTION_MARKER)
    if _SECRET_KEY.search(field) or field in {
        "argv",
        "environment",
        "stdin",
        "script",
        "error",
        "result",
        "user_stdout",
    }:
        text = _ASSIGNMENT.sub(r"\g<prefix>" + REDACTION_MARKER, text)
    text = _SENSITIVE_HEADER.sub(
        lambda match: match.group(0).split(":", 1)[0] + ": " + REDACTION_MARKER, text
    )
    text = _BEARER_VALUE.sub(REDACTION_MARKER, text)
    text = _JWT_VALUE.sub(REDACTION_MARKER, text)
    text = _URI_USERINFO.sub(r"\g<prefix>" + REDACTION_MARKER + "@", text)
    return sanitize_terminal_text(
        text,
        preserve_newlines=field
        in {
            "stdin",
            "script",
            "stdout",
            "stderr",
            "result",
            "user_stdout",
        },
    )


def redacted_projection(
    value: RedactionValue,
    *,
    secrets: Iterable[str] = (),
    field: str = "",
) -> RedactedValue:
    """Return a JSON-safe, secret-free projection without joining argv fields.

    ``argv`` values are projected one list element at a time.  Consequently a
    secret containing spaces cannot alter argument boundaries in a preview.
    """

    known_secrets = tuple(secrets)
    if isinstance(value, str):
        return _redact_text(value, known_secrets, field=field)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return _redact_text(value.decode("utf-8", errors="replace"), known_secrets, field=field)
    if isinstance(value, BaseException):
        return _redact_text(repr(value), known_secrets, field=field)
    if isinstance(value, Mapping):
        projected: dict[str, RedactedValue] = {}
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


def project_process_step(step: PreparedStep) -> ProcessStep:
    """Project a private prepared process while preserving argv boundaries."""

    from odoo_instance_sdk.execution import ProcessStep

    argv = step.argv
    # The public boundary must also scrub values captured in the exact child
    # environment.  A command may legitimately pass one of those private
    # values through argv or echo it in a diagnostic even when its name does
    # not match a heuristic secret-key pattern.
    secrets = captured_secret_values(step)
    projected_argv = _redact_argv(argv, secrets, step.sensitive_argv_indices)
    # ``environment`` is the exact private child snapshot.  Inherited values
    # are intentionally absent from the public projection; callers may opt in
    # to explicitly captured overrides, which still pass through redaction.
    # A legacy ``environment`` tuple is private execution state, not a public
    # projection.  Explicit public overrides must be supplied through the
    # dedicated field; inherited snapshots are never serialized here.
    environment = step.environment_overrides
    # Safe allowlisted metadata keeps its value when it was explicitly
    # captured as safe.  The broader private set above is for argv/text
    # scrubbing only; feeding ambient values into this field would turn a
    # harmless ``LANG=C`` override into a misleading redaction marker.
    projected_environment = redacted_environment(environment, secrets=step.secret_values)
    stdin = step.stdin
    raw_preview = step.public_input_preview
    if raw_preview is None and stdin is not None:
        raw_preview = REDACTION_MARKER
    input_preview = (
        cast(
            "str",
            redacted_projection(
                raw_preview,
                secrets=secrets,
                field="script" if step.public_input_preview is not None else "stdin",
            ),
        )
        if raw_preview is not None
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


__all__ = [
    "REDACTION_MARKER",
    "captured_argv_secret_values",
    "captured_secret_values",
    "project_process_step",
    "redacted_argv",
    "redacted_environment",
    "redacted_projection",
]
