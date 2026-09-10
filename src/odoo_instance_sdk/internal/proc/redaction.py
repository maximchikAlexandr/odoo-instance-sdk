"""One field-aware projection used by plans, diagnostics, and fingerprints."""

from __future__ import annotations

import codecs
import re
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from shlex import join
from typing import TYPE_CHECKING, Literal, cast

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
    r"authorization|bearer|credential|private[-_ ]?key|access[-_ ]?key|auth|refresh|"
    r"client[-_ ]?secret)"
    r"[A-Za-z0-9_. -]*\s*[:=]\s*))"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE | re.DOTALL,
)
_ASSIGNMENT_KEY_MARKERS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "cookie",
    "jwt",
    "oauth",
    "api-key",
    "api_key",
    "api key",
    "dsn",
    "database-url",
    "database_url",
    "database url",
    "sentry-dsn",
    "sentry_dsn",
    "sentry dsn",
    "docker-auth-config",
    "docker_auth_config",
    "docker auth config",
    "authorization",
    "bearer",
    "credential",
    "private-key",
    "private_key",
    "private key",
    "access-key",
    "access_key",
    "access key",
    "auth",
)
_ASSIGNMENT_KEY_MARKER_ENDINGS = frozenset(marker[-1] for marker in _ASSIGNMENT_KEY_MARKERS)
_ASSIGNMENT_KEY_MARKER_CHARACTERS = frozenset("".join(_ASSIGNMENT_KEY_MARKERS))
_URI_USERINFO = re.compile(r"(?P<prefix>[A-Za-z][A-Za-z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@")
_SENSITIVE_ARG = re.compile(
    r"^-*(?:(?:[A-Za-z0-9]+[-_])*(?:password|passwd|pwd|secret|token|cookie|jwt|oauth|"
    r"api[-_]?key|dsn|authorization|bearer|credential|private[-_]?key|access[-_]?key|"
    r"auth|refresh|client[-_]?secret)(?:[-_][A-Za-z0-9]+)*)$",
    re.IGNORECASE,
)
_HEADER_OPTION = re.compile(r"^(?:-H|--headers?)$", re.IGNORECASE)
_SENSITIVE_HEADER = re.compile(
    r"^\s*(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]+$",
    re.IGNORECASE | re.MULTILINE,
)
_BEARER_VALUE = re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_JWT_VALUE = re.compile(
    r"(?:^|\s)eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:$|\s)",
)
_SAFE_ENV_KEY = re.compile(r"^(?:LANG|LC_[A-Z0-9_]+|TERM|TZ|PYTHONUNBUFFERED)$")
_ASSIGNMENT_TOKENS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "cookie",
    "jwt",
    "oauth",
    "api",
    "dsn",
    "authorization",
    "bearer",
    "credential",
    "private",
    "access",
    "auth",
    "refresh",
    "client",
)
_ASSIGNMENT_KEY_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_. -"
)
_ASSIGNMENT_KEY_TAIL_LIMIT = 128
_STRUCTURAL_PENDING_LIMIT = 128
_BEARER_PREFIX = re.compile(r"(?<![A-Za-z0-9_])(?:bearer|basic)[ \t]+$", re.IGNORECASE)
_URI_SCHEME_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-"
)
_JWT_PREFIX = re.compile(r"(?:^|\s)eyJ$")
_HEADER_ASSIGNMENT_KEY = re.compile(
    r"(?:authorization|proxy-authorization|cookie|set-cookie)\s*$", re.IGNORECASE
)


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
    lowered = text.casefold()
    if any(token in lowered for token in _ASSIGNMENT_TOKENS):
        text = _ASSIGNMENT.sub(r"\g<prefix>" + REDACTION_MARKER, text)
    if any(
        header in lowered
        for header in ("authorization:", "proxy-authorization:", "cookie:", "set-cookie:")
    ):
        text = _SENSITIVE_HEADER.sub(
            lambda match: match.group(0).split(":", 1)[0] + ": " + REDACTION_MARKER, text
        )
    if "bearer " in lowered or "basic " in lowered:
        text = _BEARER_VALUE.sub(REDACTION_MARKER, text)
    if "eyj" in lowered:
        text = _JWT_VALUE.sub(REDACTION_MARKER, text)
    if "://" in text:
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


class IncrementalStreamRedactor:
    """Redact one output stream with bounded, single-pass detector state."""

    def __init__(self, *, secrets: Iterable[str] = (), field: str) -> None:
        self._secrets = tuple(secret for secret in secrets if secret)
        self._field = field
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._secret_transitions: list[dict[str, int]] = [{}]
        self._secret_failures: list[int] = [0]
        self._secret_outputs: list[int] = [0]
        self._secret_depths: list[int] = [0]
        self._build_secret_automaton()
        self._secret_state = 0
        self._secret_candidate: deque[str] = deque()
        self._structural_pending = ""
        self._structural_state: Literal["bearer", "uri", "jwt"] | None = None
        self._uri_scheme_state: Literal["scheme", "colon", "slash"] | None = None
        self._assignment_key_tail = ""
        self._assignment_key_sensitive = False
        self._assignment_header = False
        self._assignment_state: Literal["awaiting", "quoted", "unquoted", "header"] | None = None
        self._assignment_quote: str | None = None

    def feed(self, value: str | bytes) -> str:
        text = self._decoder.decode(value, final=False) if isinstance(value, bytes) else value
        return self._consume(self._redact_configured_secrets(text))

    def flush(self) -> str:
        text = self._decoder.decode(b"", final=True)
        projected = self._redact_configured_secrets(text)
        if self._secret_candidate:
            projected += REDACTION_MARKER
            self._secret_state = 0
            self._secret_candidate.clear()
        return self._consume(projected, terminal=True)

    def _build_secret_automaton(self) -> None:
        for secret in self._secrets:
            node = 0
            for character in secret:
                child = self._secret_transitions[node].get(character)
                if child is None:
                    child = len(self._secret_transitions)
                    self._secret_transitions[node][character] = child
                    self._secret_transitions.append({})
                    self._secret_failures.append(0)
                    self._secret_outputs.append(0)
                    self._secret_depths.append(self._secret_depths[node] + 1)
                node = child
            self._secret_outputs[node] = max(self._secret_outputs[node], len(secret))

        pending = deque(self._secret_transitions[0].values())
        while pending:
            node = pending.popleft()
            for character, child in self._secret_transitions[node].items():
                failure = self._secret_failures[node]
                while failure and character not in self._secret_transitions[failure]:
                    failure = self._secret_failures[failure]
                self._secret_failures[child] = self._secret_transitions[failure].get(character, 0)
                self._secret_outputs[child] = max(
                    self._secret_outputs[child],
                    self._secret_outputs[self._secret_failures[child]],
                )
                pending.append(child)

    def _redact_configured_secrets(self, text: str) -> str:
        """Replace configured values with a bounded Aho-Corasick stream matcher."""
        if not self._secrets:
            return text
        emitted: list[str] = []
        for character in text:
            state = self._secret_state
            while state and character not in self._secret_transitions[state]:
                state = self._secret_failures[state]
            state = self._secret_transitions[state].get(character, 0)
            self._secret_state = state
            self._secret_candidate.append(character)
            matched_length = self._secret_outputs[state]
            if matched_length:
                emitted.append(
                    self._take_secret_candidate(len(self._secret_candidate) - matched_length)
                )
                emitted.append(REDACTION_MARKER)
                self._secret_state = 0
                self._secret_candidate.clear()
                continue
            keep_length = self._secret_depths[state]
            if len(self._secret_candidate) > keep_length:
                split = len(self._secret_candidate) - keep_length
                emitted.append(self._take_secret_candidate(split))
        return "".join(emitted)

    def _take_secret_candidate(self, count: int) -> str:
        return "".join(self._secret_candidate.popleft() for _ in range(count))

    def _consume(self, text: str, *, terminal: bool = False) -> str:
        if (
            text
            and not terminal
            and self._assignment_state == "awaiting"
            and all(character.isspace() for character in text)
        ):
            return self._project_stream_text(text)
        if (
            text
            and not terminal
            and self._assignment_state is None
            and self._structural_state is None
            and not self._structural_pending
            and all(
                " " <= character <= "~"
                and character.casefold() not in _ASSIGNMENT_KEY_MARKER_CHARACTERS
                and character not in " :=/J"
                for character in text
            )
        ):
            self._update_uri_scheme_fast_path(text)
            return text
        emitted: list[str] = []
        index = 0
        while index < len(text):
            if self._assignment_state == "awaiting" and text[index].isspace():
                start = index
                while index < len(text) and text[index].isspace():
                    index += 1
                emitted.append(self._project_stream_text(text[start:index]))
                continue
            character = text[index]
            index += 1
            chunk = self._consume_character(character)
            if chunk:
                emitted.append(chunk)
        if terminal:
            if self._structural_state == "uri":
                emitted.append(REDACTION_MARKER)
            elif self._structural_state is None:
                emitted.append(self._flush_structural_pending())
            self._assignment_state = None
            self._assignment_quote = None
            self._structural_state = None
            self._reset_assignment_key()
        return "".join(emitted)

    def _consume_character(self, character: str) -> str:
        assignment = self._consume_assignment_character(character)
        if assignment is not None:
            return assignment
        if self._track_assignment_key(character):
            header = self._assignment_header
            if self._structural_state is not None:
                # Bearer/URI/JWT detection and assignment detection run over
                # the same input.  Once an assignment marker appears inside a
                # structural candidate, the assignment owns the remainder;
                # the structural detector has already emitted its marker.
                self._structural_state = None
                self._reset_assignment_key()
                self._assignment_header = header
                return ""
            pending = self._flush_structural_pending()
            projected = self._project_stream_character(character)
            self._reset_assignment_key()
            self._assignment_header = header
            return pending + projected
        structural = self._consume_structural_character(character)
        if structural is not None:
            return structural
        return self._append_structural_pending(character)

    def _consume_assignment_character(self, character: str) -> str | None:
        state = self._assignment_state
        if state is None:
            return None
        if state == "quoted":
            return self._consume_quoted_assignment_character(character)
        if state == "unquoted":
            return self._consume_unquoted_assignment_character(character)
        if state == "header":
            return self._consume_header_assignment_character(character)
        return self._consume_awaiting_assignment_character(character)

    def _consume_quoted_assignment_character(self, character: str) -> str:
        if character == self._assignment_quote:
            self._assignment_state = None
            self._assignment_quote = None
        return ""

    def _consume_unquoted_assignment_character(self, character: str) -> str:
        if character.isspace() or character in ",;":
            self._assignment_state = None
            return self._project_stream_character(character)
        return ""

    def _consume_header_assignment_character(self, character: str) -> str:
        if character == "\n":
            self._assignment_state = None
            return character
        if character == "\r":
            return self._project_stream_character(character)
        return ""

    def _consume_awaiting_assignment_character(self, character: str) -> str:
        if character.isspace():
            return self._project_stream_character(character)
        if character in ",;":
            self._assignment_state = None
            return character
        if character in "'\"":
            self._assignment_state = "header" if self._assignment_header else "quoted"
            self._assignment_quote = character
            return REDACTION_MARKER
        self._assignment_state = "header" if self._assignment_header else "unquoted"
        return REDACTION_MARKER

    def _consume_structural_character(self, character: str) -> str | None:
        if self._structural_state == "bearer":
            if character.isspace() or character in ",;":
                self._structural_state = None
                return self._project_stream_character(character)
            return ""
        if self._structural_state == "uri":
            if character == "@":
                self._structural_state = None
                return REDACTION_MARKER + character
            if character.isspace():
                self._structural_state = None
                return self._project_stream_character(character)
            return ""
        if self._structural_state == "jwt":
            if character in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-+/=":
                return ""
            self._structural_state = None
            return self._project_stream_character(character)

        return None

    def _track_assignment_key(self, character: str) -> bool:
        if character in ":=":
            if not self._assignment_key_sensitive:
                self._reset_assignment_key()
                return False
            self._assignment_header = (
                _HEADER_ASSIGNMENT_KEY.search(self._assignment_key_tail) is not None
            )
            self._assignment_state = "awaiting"
            return True
        if character == "\n" or character not in _ASSIGNMENT_KEY_CHARACTERS:
            self._reset_assignment_key()
        else:
            self._assignment_key_tail = (self._assignment_key_tail + character)[
                -_ASSIGNMENT_KEY_TAIL_LIMIT:
            ]
        if character.casefold() in _ASSIGNMENT_KEY_MARKER_ENDINGS:
            key_tail = self._assignment_key_tail.casefold()
            if any(marker in key_tail for marker in _ASSIGNMENT_KEY_MARKERS):
                self._assignment_key_sensitive = True
        return False

    @staticmethod
    def _project_stream_character(character: str) -> str:
        if character in " \t\n":
            return character
        if character == "\r":
            return r"\x0d"
        if " " <= character <= "~":
            return character
        return sanitize_terminal_text(character, preserve_newlines=True)

    def _reset_assignment_key(self) -> None:
        self._assignment_key_tail = ""
        self._assignment_key_sensitive = False
        self._assignment_header = False

    def _append_structural_pending(self, character: str) -> str:
        self._structural_pending += character
        if character == "\n":
            self._uri_scheme_state = None
            return self._flush_structural_pending()
        if character in " \t":
            match = _BEARER_PREFIX.search(self._structural_pending)
            if match is not None and match.end() == len(self._structural_pending):
                prefix = self._structural_pending[: match.start()]
                self._structural_pending = ""
                self._structural_state = "bearer"
                return self._project_stream_text(prefix) + REDACTION_MARKER
        if self._advance_uri_scheme(character):
            pending = self._structural_pending
            self._structural_pending = ""
            return self._project_stream_text(pending)
        if character == "J":
            match = _JWT_PREFIX.search(self._structural_pending)
            if match is not None and match.end() == len(self._structural_pending):
                prefix = self._structural_pending[: match.start()]
                self._structural_pending = ""
                self._structural_state = "jwt"
                return self._project_stream_text(prefix) + REDACTION_MARKER
        if len(self._structural_pending) <= _STRUCTURAL_PENDING_LIMIT:
            return ""
        split = len(self._structural_pending) - _STRUCTURAL_PENDING_LIMIT
        safe = self._structural_pending[:split]
        self._structural_pending = self._structural_pending[split:]
        return (
            self._project_stream_character(safe)
            if len(safe) == 1
            else self._project_stream_text(safe)
        )

    def _flush_structural_pending(self) -> str:
        self._uri_scheme_state = None
        pending = self._structural_pending
        self._structural_pending = ""
        return self._project_stream_text(pending)

    def _advance_uri_scheme(self, character: str) -> bool:
        state = self._uri_scheme_state
        if state == "scheme":
            if character in _URI_SCHEME_CHARACTERS:
                return False
            if character == ":":
                self._uri_scheme_state = "colon"
                return False
            self._uri_scheme_state = None
        elif state == "colon":
            if character == "/":
                self._uri_scheme_state = "slash"
                return False
            self._uri_scheme_state = None
        elif state == "slash":
            if character == "/":
                self._uri_scheme_state = None
                self._structural_state = "uri"
                return True
            self._uri_scheme_state = None
        if "A" <= character <= "Z" or "a" <= character <= "z":
            self._uri_scheme_state = "scheme"
        return False

    def _update_uri_scheme_fast_path(self, text: str) -> None:
        """Retain only the DFA state that can cross this fast-path boundary."""
        suffix_start = len(text)
        while suffix_start and text[suffix_start - 1] in _URI_SCHEME_CHARACTERS:
            suffix_start -= 1
        if suffix_start == len(text):
            self._uri_scheme_state = None
            return
        suffix = text[suffix_start:]
        if any("A" <= character <= "Z" or "a" <= character <= "z" for character in suffix) or (
            suffix_start == 0 and self._uri_scheme_state == "scheme"
        ):
            self._uri_scheme_state = "scheme"
        else:
            self._uri_scheme_state = None

    def _project_stream_text(self, value: str) -> str:
        return cast(
            "str",
            redacted_projection(value, secrets=(), field=self._field),
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
