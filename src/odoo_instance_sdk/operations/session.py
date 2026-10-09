"""Bounded JSONL approval sessions for one private command snapshot."""

from __future__ import annotations

import json
import select
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Literal, cast

import msgspec

from odoo_instance_sdk.commands.output import sanitize_diagnostic
from odoo_instance_sdk.execution import Command, JsonValue, fingerprint_plan
from odoo_instance_sdk.internal.proc import StepEvent


@dataclass(frozen=True, slots=True)
class SessionLimits:
    decision_timeout: float = 30.0
    max_input_bytes: int = 65_536
    max_records: int = 128
    max_output_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if (
            self.decision_timeout <= 0
            or self.max_input_bytes <= 0
            or self.max_records <= 0
            or self.max_output_bytes <= 0
        ):
            raise ValueError("session limits must be positive")


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    records: tuple[dict[str, JsonValue], ...]
    exit_code: int


class SessionRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Typed JSONL session ingress envelope."""

    operation_id: str
    request: dict[str, JsonValue] = {}


class SessionDecision(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """The one decision accepted after preview."""

    decision: Literal["approve", "cancel"]
    fingerprint: str | None = None


class SessionEvent(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Typed projection for every bounded session record."""

    event: str
    operation_id: str
    contract_version: int = 1
    fingerprint: str | None = None
    data: JsonValue | None = None


def _event(
    event_type: str,
    operation_id: str,
    *,
    fingerprint: str | None = None,
    data: JsonValue | None = None,
) -> dict[str, JsonValue]:
    record: dict[str, JsonValue] = {
        "contract_version": 1,
        "event": event_type,
        "operation_id": operation_id,
    }
    if fingerprint is not None:
        record["fingerprint"] = fingerprint
    if data is not None:
        record["data"] = data
    return record


def _step_data(event: StepEvent) -> dict[str, JsonValue]:
    return {
        "step_id": event.step_id,
        "kind": event.kind,
        "operation": event.operation,
        "target": event.target,
        "chunk": event.chunk,
        "returncode": event.returncode,
        "error": event.error,
        "elapsed": event.elapsed,
        "completed_units": event.completed_units,
        "total_units": event.total_units,
    }


def _write_record(
    stream: IO[str],
    record: dict[str, JsonValue],
    records: list[dict[str, JsonValue]],
    limits: SessionLimits,
    *,
    terminal: bool = False,
    terminal_reserve: int = 0,
) -> bool:
    available_records = limits.max_records if terminal else limits.max_records - 1
    if len(records) >= available_records:
        return False
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    existing = sum(
        len(json.dumps(item, ensure_ascii=False, separators=(",", ":"))) + 1 for item in records
    )
    if (
        not terminal
        and existing + len(line.encode("utf-8")) + terminal_reserve > limits.max_output_bytes
    ):
        return False
    stream.write(line)
    stream.flush()
    records.append(record)
    return True


def _terminal_error(
    operation_id: str,
    code: str,
    message: str,
    *,
    fingerprint: str | None = None,
) -> dict[str, JsonValue]:
    return _event(
        "error",
        operation_id,
        fingerprint=fingerprint,
        data={"code": code, "message": sanitize_diagnostic(message)},
    )


def _write_terminal_error(
    output_stream: IO[str],
    records: list[dict[str, JsonValue]],
    limits: SessionLimits,
    record: dict[str, JsonValue],
) -> None:
    # A control error is the only permitted exception to a byte budget: an
    # impossible budget must still produce a typed, non-zero terminal result.
    _write_record(output_stream, record, records, limits, terminal=True)


def session_error_record(operation_id: str, code: str, message: str) -> dict[str, JsonValue]:
    """Build the JSONL error record used before a session can be opened."""

    return _terminal_error(operation_id, code, message)


def _read_decision(
    stream: IO[str], timeout: float, max_bytes: int
) -> tuple[SessionDecision | None, str | None]:
    if timeout <= 0:
        return None, "timeout"
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        descriptor = None
    if descriptor is not None and stream is sys.stdin:
        ready, _, _ = select.select([descriptor], [], [], timeout)
        if not ready:
            return None, "timeout"
    started = time.monotonic()
    line = stream.readline(max_bytes + 1)
    if time.monotonic() - started > timeout:
        return None, "timeout"
    if not line:
        return None, "eof"
    if len(line.encode("utf-8")) > max_bytes:
        return None, "input_limit"
    try:
        return msgspec.json.decode(line.encode("utf-8"), type=SessionDecision), None
    except (msgspec.DecodeError, msgspec.ValidationError):
        return None, "invalid_sequence"


def run_approval_session(  # noqa: C901
    operation_id: str,
    build_command: Callable[[], Command[object]],
    input_stream: IO[str],
    output_stream: IO[str],
    *,
    limits: SessionLimits = SessionLimits(),
) -> SessionOutcome:
    """Preview one command and consume exactly one bounded decision."""

    records: list[dict[str, JsonValue]] = []
    command: Command[object] | None = None
    try:
        command = build_command()
        digest = command.plan.fingerprint or fingerprint_plan(command.plan)
        bounded_error = _terminal_error(
            operation_id,
            "bounded_output",
            "bounded JSONL session output limit exceeded",
            fingerprint=digest,
        )
        terminal_reserve = len(
            (json.dumps(bounded_error, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
        )
        for record in (
            _event("accepted", operation_id),
            _event(
                "preview",
                operation_id,
                fingerprint=digest,
                data=cast("JsonValue", msgspec.to_builtins(command.plan)),
            ),
            _event("approval_required", operation_id, fingerprint=digest),
        ):
            if not _write_record(
                output_stream,
                record,
                records,
                limits,
                terminal_reserve=terminal_reserve,
            ):
                _write_terminal_error(output_stream, records, limits, bounded_error)
                return SessionOutcome(tuple(records), 1)

        decision, error_code = _read_decision(
            input_stream, limits.decision_timeout, limits.max_input_bytes
        )
        if error_code is not None:
            _write_terminal_error(
                output_stream,
                records,
                limits,
                _terminal_error(
                    operation_id,
                    error_code,
                    f"session ended: {error_code}",
                    fingerprint=digest,
                ),
            )
            return SessionOutcome(tuple(records), 1)
        assert decision is not None
        if decision.decision == "cancel":
            _write_record(
                output_stream,
                _event("cancelled", operation_id, fingerprint=digest),
                records,
                limits,
                terminal=True,
            )
            return SessionOutcome(tuple(records), 0)
        supplied = decision.fingerprint
        if not isinstance(supplied, str) or supplied != digest:
            _write_terminal_error(
                output_stream,
                records,
                limits,
                _terminal_error(
                    operation_id,
                    "stale_approval",
                    "approval fingerprint does not match the captured command",
                    fingerprint=digest,
                ),
            )
            return SessionOutcome(tuple(records), 1)

        output_exhausted = False

        def observe(step: StepEvent) -> None:
            nonlocal output_exhausted
            output_exhausted = not _write_record(
                output_stream,
                _event("step", operation_id, fingerprint=digest, data=_step_data(step)),
                records,
                limits,
                terminal_reserve=terminal_reserve,
            )

        try:
            value = command.run(observer=observe)
        except KeyboardInterrupt:
            if output_exhausted:
                _write_terminal_error(output_stream, records, limits, bounded_error)
                return SessionOutcome(tuple(records), 1)
            _write_record(
                output_stream,
                _event("cancelled", operation_id, fingerprint=digest),
                records,
                limits,
                terminal=True,
            )
            return SessionOutcome(tuple(records), 130)
        except Exception as error:
            if output_exhausted:
                _write_terminal_error(output_stream, records, limits, bounded_error)
                return SessionOutcome(tuple(records), 1)
            _write_terminal_error(
                output_stream,
                records,
                limits,
                _terminal_error(
                    operation_id,
                    "operation_failed",
                    str(error),
                    fingerprint=digest,
                ),
            )
            return SessionOutcome(tuple(records), 1)
        if output_exhausted:
            _write_terminal_error(output_stream, records, limits, bounded_error)
            return SessionOutcome(tuple(records), 1)
        payload = msgspec.to_builtins(value) if isinstance(value, msgspec.Struct) else value
        if (
            not isinstance(payload, (dict, list, tuple, str, int, float, bool))
            and payload is not None
        ):
            payload = repr(value)
        if not _write_record(
            output_stream,
            _event("result", operation_id, fingerprint=digest, data=cast("JsonValue", payload)),
            records,
            limits,
            terminal=True,
        ):
            _write_terminal_error(output_stream, records, limits, bounded_error)
            return SessionOutcome(tuple(records), 1)
        return SessionOutcome(tuple(records), 0)
    finally:
        # Dropping the reference releases the private snapshot; no plan fields
        # are ever used to reconstruct an executable command.
        command = None


__all__ = [
    "SessionDecision",
    "SessionEvent",
    "SessionLimits",
    "SessionOutcome",
    "SessionRequest",
    "run_approval_session",
    "session_error_record",
]
