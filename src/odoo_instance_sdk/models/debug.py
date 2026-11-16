"""Public debug snapshot and value models."""

from __future__ import annotations

from typing import Literal

import msgspec

DebugState = Literal["running", "stopped", "terminated"]
DebugStopReason = Literal["breakpoint", "step", "pause", "exception", "exited"]
TriggerState = Literal["running", "exited"]


class DebugLocation(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One environment-relative source location."""

    path: str
    line: int
    function: str


class DebugSource(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """A short source window around the stopped line."""

    start_line: int
    lines: tuple[str, ...]
    truncated: bool = False


class DebugLocal(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One shallow local name and its redacted representation."""

    name: str
    value: str
    type: str


class DebugFrame(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One stack frame without adapter identifiers."""

    frame: int
    path: str
    line: int
    function: str


class DebugBreakpoint(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True, omit_defaults=True
):
    """One logical breakpoint and its latest verification."""

    path: str
    line: int
    condition: str | None = None
    verified: bool = False
    message: str | None = None


class DebugOutput(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Bounded stdout and stderr tails."""

    stdout: tuple[str, ...] = ()
    stderr: tuple[str, ...] = ()
    truncated: bool = False


class DebugTrigger(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True, omit_defaults=True
):
    """The single reproduction process owned by the session."""

    state: TriggerState
    pid: int | None = None
    exit_code: int | None = None


class DebugTruncation(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Which snapshot sections hit a fixed limit."""

    locals: bool = False
    stack: bool = False
    source: bool = False
    output: bool = False


class DebugSnapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Immutable debug state returned by every lifecycle verb."""

    schema_version: int = 1
    session_id: str
    environment_id: str
    state: DebugState
    stop_reason: DebugStopReason | None = None
    location: DebugLocation | None = None
    source: DebugSource | None = None
    locals: tuple[DebugLocal, ...] = ()
    stack: tuple[DebugFrame, ...] = ()
    breakpoints: tuple[DebugBreakpoint, ...] = ()
    output: DebugOutput = msgspec.field(default_factory=DebugOutput)
    trigger: DebugTrigger | None = None
    truncated: DebugTruncation = msgspec.field(default_factory=DebugTruncation)


class DebugChild(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One bounded child of an evaluated value."""

    name: str
    value: str
    type: str
    children: tuple[DebugChild, ...] = ()


class DebugValue(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One bounded evaluation result."""

    schema_version: int = 1
    expression: str
    value: str
    type: str
    children: tuple[DebugChild, ...] = ()
    truncated: bool = False
