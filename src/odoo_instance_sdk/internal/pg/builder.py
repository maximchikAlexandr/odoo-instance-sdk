"""Immutable, redacted process specifications for native ``psql``."""

from __future__ import annotations

import math
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.process_env import sanitized_child_environment

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import ProcessStep
    from odoo_instance_sdk.internal.proc import PreparedStep

_PROTECTED = {
    "-d",
    "--dbname",
    "-h",
    "--host",
    "-p",
    "--port",
    "-U",
    "--username",
}
_VALUE_OPTIONS = {
    "-c",
    "--command",
    "-f",
    "--file",
    "-F",
    "--field-separator",
    "-L",
    "--log-file",
    "-o",
    "--output",
    "-P",
    "--pset",
    "-R",
    "--record-separator",
    "-T",
    "--table-attr",
    "-v",
    "--set",
    "--variable",
}
_ZERO_OPTIONS = {
    "-a",
    "--echo-all",
    "-b",
    "--echo-errors",
    "-e",
    "--echo-queries",
    "-E",
    "--echo-hidden",
    "-H",
    "--html",
    "-l",
    "--list",
    "-n",
    "--no-readline",
    "-q",
    "--quiet",
    "-s",
    "--single-step",
    "-S",
    "--single-line",
    "-t",
    "--tuples-only",
    "-x",
    "--expanded",
    "-X",
    "--no-psqlrc",
    "-w",
    "--no-password",
    "-W",
    "--password",
    "-z",
    "--field-separator-zero",
    "-0",
    "--record-separator-zero",
    "-1",
    "--single-transaction",
    "--csv",
}


def _invalid(message: str) -> ConfigError:
    return ConfigError(f"invalid native psql arguments: {message}")


def validate_native_psql_args(  # noqa: C901
    args: Sequence[str],
) -> tuple[str, ...]:
    """Validate and freeze the deliberately closed native-option grammar."""
    tokens = tuple(args)
    result: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not isinstance(token, str) or not token:
            raise _invalid("arguments must be non-empty strings")
        if token == "--":
            if index != len(tokens) - 1:
                raise _invalid("positional operands after '--' are not supported")
            result.append(token)
            index += 1
            continue
        if token.startswith("--"):
            name, separator, value = token.partition("=")
            if name in _PROTECTED:
                raise _invalid(f"connection identity option {name!r} is not allowed")
            if name in _VALUE_OPTIONS:
                if separator:
                    if not value:
                        raise _invalid(f"missing value for {name}")
                    result.append(token)
                else:
                    if index + 1 >= len(tokens):
                        raise _invalid(f"missing value for {name}")
                    result.extend((token, tokens[index + 1]))
                    index += 1
                index += 1
                continue
            if name in _ZERO_OPTIONS and not separator:
                result.append(token)
                index += 1
                continue
            raise _invalid(f"unsupported option {token!r}")
        if token.startswith("-"):
            option = token[:2]
            if option in _PROTECTED:
                raise _invalid(f"connection identity option {option!r} is not allowed")
            if option in _VALUE_OPTIONS:
                if len(token) > 2:
                    result.append(token)
                else:
                    if index + 1 >= len(tokens):
                        raise _invalid(f"missing value for {option}")
                    result.extend((token, tokens[index + 1]))
                    index += 1
                index += 1
                continue
            if token in _ZERO_OPTIONS:
                result.append(token)
                index += 1
                continue
            raise _invalid(f"unsupported option {token!r}")
        raise _invalid(f"positional operand {token!r} is not allowed")
    return tuple(result)


def _statement_timeout(timeout: float | None) -> str | None:
    if timeout is None:
        return None
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ConfigError("psql timeout must be finite and greater than zero")
    milliseconds = max(1, math.ceil(float(timeout) * 1000))
    return f"-c statement_timeout={milliseconds}"


@dataclass(frozen=True, slots=True, repr=False)
class PsqlSpecification:
    """Private executable step paired with its safe public projection."""

    prepared_step: PreparedStep
    public_step: ProcessStep

    @property
    def step(self) -> PreparedStep:
        """Compatibility alias for consumers referring to the private step."""
        return self.prepared_step

    @property
    def process_step(self) -> ProcessStep:
        return self.public_step

    def __repr__(self) -> str:
        return f"PsqlSpecification(public_step={self.public_step!r})"


def build_psql_specification(  # noqa: C901
    *,
    host: str | None,
    port: int,
    user: str | None,
    database: str,
    password: str | None = None,
    args: Sequence[str] = (),
    stdin: bytes | None = None,
    timeout: float | None = None,
    mode: str = "captured",
    step_id: str = "psql",
    _trusted_args: Sequence[str] = (),
    _inject_timeout: bool = True,
    _allow_missing_user: bool = False,
    _require_binary: bool = True,
) -> PsqlSpecification:
    """Build one exact psql launch for both captured and foreground modes."""
    if (user is None or not user) and not _allow_missing_user:
        raise ConfigError("psql requires a bound database user")
    if not isinstance(port, int) or isinstance(port, bool) or port <= 0 or port > 65535:
        raise ConfigError("psql port must be an integer from 1 through 65535")
    if not database:
        raise ConfigError("psql requires a bound database name")
    if mode not in {"captured", "foreground"}:
        raise ConfigError("psql mode must be 'captured' or 'foreground'")
    if _require_binary and shutil.which("psql") is None:
        raise FileNotFoundError("psql is not available on PATH")
    native = validate_native_psql_args(args)
    statement_timeout = _statement_timeout(timeout) if _inject_timeout else None

    command = ["psql", "-X"]
    if host is not None:
        command.extend(("-h", host))
    command.extend(("-p", str(port)))
    if user:
        command.extend(("-U", user))
    command.extend(("-d", database))
    command.extend(_trusted_args)
    command.extend(native)

    environment = sanitized_child_environment()
    for key in (
        "PGHOST",
        "PGHOSTADDR",
        "PGPORT",
        "PGUSER",
        "PGDATABASE",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PSQLRC",
        "PGPASSWORD",
        "PGOPTIONS",
    ):
        environment.pop(key, None)
    if password is not None:
        environment["PGPASSWORD"] = password
    if statement_timeout is not None:
        environment["PGOPTIONS"] = statement_timeout

    from odoo_instance_sdk.internal.proc import PreparedStep

    prepared = PreparedStep(
        step_id=step_id,
        argv=tuple(command),
        # The complete environment is private execution state.  Keeping the
        # result metadata empty prevents inherited credentials from appearing
        # in a ProcessResult representation; the executor uses the snapshot.
        environment=(),
        environment_snapshot=tuple(sorted(environment.items())),
        environment_overrides=(("PGPASSWORD", password),) if password is not None else (),
        environment_policy="sanitized-inherit",
        stdin=stdin,
        public_input_preview=None if stdin is None else "<redacted>",
        timeout=timeout,
        mode=mode,
        read_only=True,
        interactive=mode == "foreground",
        inherit_stdio=mode == "foreground",
        start_new_session=mode == "foreground",
        text=True,
        secret_values=(password,) if password else (),
    )
    public = prepared.public_projection()
    return PsqlSpecification(prepared_step=prepared, public_step=public)


__all__ = [
    "PsqlSpecification",
    "build_psql_specification",
    "validate_native_psql_args",
]
