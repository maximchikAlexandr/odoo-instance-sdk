"""Headless session state machine owned by the debug daemon."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import msgspec

from odoo_instance_sdk.exceptions import DebugError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.debug.adapter import DebugAdapter
from odoo_instance_sdk.internal.debug.launch import TargetLauncher
from odoo_instance_sdk.internal.debug.limits import STARTUP_TIMEOUT_SECONDS
from odoo_instance_sdk.internal.debug.session import (
    current_pid_metadata,
    release_session,
    write_metadata,
    write_session_file,
)
from odoo_instance_sdk.internal.debug.snapshot import (
    bound_locals,
    bound_output,
    bound_stack,
    display_path,
    source_window,
)
from odoo_instance_sdk.internal.proc.run import ProcessHandle
from odoo_instance_sdk.internal.server import cleanup_secret_config
from odoo_instance_sdk.models.debug import (
    DebugBreakpoint,
    DebugFrame,
    DebugLocal,
    DebugLocation,
    DebugSnapshot,
    DebugState,
    DebugStopReason,
    DebugTrigger,
    DebugTruncation,
    DebugValue,
)

_STOP_REASONS: dict[str, DebugStopReason] = {
    "breakpoint": "breakpoint",
    "step": "step",
    "pause": "pause",
    "exception": "exception",
}


@dataclass(frozen=True, slots=True)
class _Breakpoint:
    path: str
    line: int
    condition: str | None = None
    temporary: bool = False
    verified: bool = False
    message: str | None = None


class DebugController:
    """One environment session: target, trigger, breakpoints, and snapshots."""

    def __init__(
        self,
        directory: Path,
        *,
        adapter: DebugAdapter | None = None,
        launcher: TargetLauncher | None = None,
    ) -> None:
        self._directory = directory
        self._adapter = adapter or DebugAdapter()
        self._launcher = launcher or TargetLauncher()
        self._lock = threading.Lock()
        self.stopped = threading.Event()
        self._target: ProcessHandle | None = None
        self._trigger: ProcessHandle | None = None
        self._breakpoints: list[_Breakpoint] = []
        self._known_paths: set[str] = set()
        self._state: DebugState = "running"
        self._stop_reason: DebugStopReason | None = None
        self._session_id = ""
        self._environment_id = ""
        self._cwd = directory
        self._argv: tuple[str, ...] = ()
        self._http_interface = "127.0.0.1"
        self._http_port = 0
        self._secret: str | None = None
        self._connected = False

    def bootstrap(self) -> DebugSnapshot:
        """Launch the captured target and return the first bounded snapshot."""
        self._load()
        write_metadata(
            self._directory,
            current_pid_metadata(
                self._environment_id, self._session_id, self._directory / "session.sock"
            ),
        )
        try:
            self._launch_target()
            return self._wait_for_event(STARTUP_TIMEOUT_SECONDS, threading.Event(), startup=True)
        except DebugError:
            self.stop_children()
            raise

    def handle(self, message: Mapping[str, JsonValue], cancel: threading.Event) -> JsonValue:
        """Serve one RPC method and return a JSON-safe snapshot or value."""
        with self._lock:
            method = message.get("method")
            params = message.get("params")
            if not isinstance(method, str) or not isinstance(params, dict):
                raise DebugError("protocol-failure", "debug RPC request is invalid")
            body = self._dispatch(method, params, cancel)
            return cast("JsonValue", msgspec.to_builtins(body))

    def stop_children(self) -> None:
        """Terminate the owned target and trigger without removing the session."""
        target, trigger = self._target, self._trigger
        self._target = None
        self._trigger = None
        self._adapter.close()
        self._connected = False
        self._launcher.stop(trigger)
        self._launcher.stop(target)
        if self._secret is not None:
            cleanup_secret_config(self._secret)
            self._secret = None
        self._state = "terminated"
        self._stop_reason = "exited"

    def cleanup(self) -> None:
        """Stop owned processes and delete only this session directory."""
        environment_id = self._environment_id
        self.stop_children()
        if environment_id:
            release_session(environment_id)

    def _dispatch(
        self,
        method: str,
        params: Mapping[str, JsonValue],
        cancel: threading.Event,
    ) -> DebugSnapshot | DebugValue:
        if method == "context":
            return self._context(params, cancel)
        if method == "continue":
            return self._continue(params, cancel)
        if method == "step":
            return self._step(params, cancel)
        if method == "pause":
            return self._pause(params, cancel)
        if method == "eval":
            return self._eval(params)
        if method == "restart":
            return self._restart(cancel)
        if method == "stop":
            self._state = "terminated"
            self._stop_reason = "exited"
            snapshot = self._drop_temporary(self.snapshot())
            self.stopped.set()
            self.stop_children()
            return snapshot
        raise DebugError("protocol-failure", f"unknown debug method {method}")

    def _context(self, params: Mapping[str, JsonValue], cancel: threading.Event) -> DebugSnapshot:
        self._observe_target()
        if params.get("wait") is True and self._state == "running":
            return self._wait_for_event(_timeout(params), cancel, startup=False)
        return self.snapshot()

    def _continue(self, params: Mapping[str, JsonValue], cancel: threading.Event) -> DebugSnapshot:
        self._edit_breakpoints(params)
        self._launch_trigger(params.get("trigger"))
        if self._state == "stopped":
            self._adapter.resume("continue")
            self._state = "running"
            self._stop_reason = None
        if params.get("wait") is True:
            return self._wait_for_event(_timeout(params), cancel, startup=False)
        return self.snapshot()

    def _step(self, params: Mapping[str, JsonValue], cancel: threading.Event) -> DebugSnapshot:
        self._require_stopped()
        kind = params.get("kind")
        if kind not in {"over", "in", "out"}:
            raise DebugError("protocol-failure", "step kind must be over, in, or out")
        self._adapter.resume(kind)
        self._state = "running"
        return self._wait_for_event(_timeout(params), cancel, startup=False)

    def _pause(self, params: Mapping[str, JsonValue], cancel: threading.Event) -> DebugSnapshot:
        if self._state != "running":
            return self.snapshot()
        self._adapter.pause()
        return self._wait_for_event(_timeout(params), cancel, startup=False)

    def _eval(self, params: Mapping[str, JsonValue]) -> DebugValue:
        self._require_stopped()
        expression = params.get("expression")
        frame = params.get("frame", 0)
        depth = params.get("depth", 0)
        if not isinstance(expression, str) or not expression:
            raise DebugError("protocol-failure", "eval requires an expression", state=self._state)
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            raise DebugError("protocol-failure", "eval frame must be a non-negative integer")
        if isinstance(depth, bool) or not isinstance(depth, int):
            raise DebugError("protocol-failure", "eval depth must be an integer")
        value, type_name, children, truncated = self._adapter.evaluate(expression, frame, depth)
        return DebugValue(
            expression=expression,
            value=value,
            type=type_name,
            children=children,
            truncated=truncated,
        )

    def _restart(self, cancel: threading.Event) -> DebugSnapshot:
        self._breakpoints = [item for item in self._breakpoints if not item.temporary]
        self._launcher.stop(self._trigger)
        self._trigger = None
        self._adapter.close()
        self._connected = False
        self._launcher.stop(self._target)
        self._target = None
        log_dir = self._directory / "debugpy-log"
        if log_dir.is_dir():
            for path in log_dir.glob("*.log"):
                path.unlink(missing_ok=True)
        self._launch_target()
        return self._wait_for_event(STARTUP_TIMEOUT_SECONDS, cancel, startup=True)

    def _launch_target(self) -> None:
        self._target = self._launcher.start(self._argv, self._cwd)
        port = self._launcher.listen_port(self._target, 20)
        self._adapter.connect(port)
        self._connected = True
        self._apply_breakpoints()
        self._adapter.configuration_done()

    def _launch_trigger(self, raw: JsonValue) -> None:
        if raw is None:
            return
        if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
            raise DebugError("trigger-failed", "trigger argv must be a list of strings")
        if self._trigger is not None and self._trigger.poll() is None:
            raise DebugError(
                "trigger-already-running",
                "a reproduction process is already running",
                state=self._state,
                snapshot=self.snapshot(),
            )
        try:
            self._trigger = self._launcher.start(tuple(cast("list[str]", raw)), self._cwd)
        except OSError as exc:
            raise DebugError("trigger-failed", "reproduction process failed to start") from exc

    def _edit_breakpoints(self, params: Mapping[str, JsonValue]) -> None:
        proposed = [] if params.get("clear") is True else list(self._breakpoints)
        for item in _objects(params.get("remove")):
            path, line = _point(item)
            proposed = [entry for entry in proposed if (entry.path, entry.line) != (path, line)]
        additions: list[_Breakpoint] = []
        for item in _objects(params.get("add")):
            additions.append(_new_breakpoint(item))
        proposed.extend(additions)
        previous = self._breakpoints
        self._breakpoints = proposed
        try:
            if self._connected:
                self._apply_breakpoints()
        except DebugError:
            self._breakpoints = previous
            if self._connected:
                self._apply_breakpoints()
            raise
        self._persist_breakpoints()

    def _apply_breakpoints(self) -> None:
        groups: dict[str, list[tuple[int, str | None]]] = {path: [] for path in self._known_paths}
        for item in self._breakpoints:
            groups.setdefault(item.path, []).append((item.line, item.condition))
        verified = self._adapter.set_breakpoints(
            {path: tuple(entries) for path, entries in groups.items()}
        )
        self._known_paths = set(groups)
        by_point = {(path, line): (ok, message) for path, line, ok, message in verified}
        self._breakpoints = [
            replace(
                item,
                verified=by_point.get((item.path, item.line), (item.verified, item.message))[0],
                message=by_point.get((item.path, item.line), (item.verified, item.message))[1],
            )
            for item in self._breakpoints
        ]

    def _wait_for_event(
        self, timeout: float, cancel: threading.Event, *, startup: bool
    ) -> DebugSnapshot:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel.is_set():
                return self.snapshot()
            self._observe_target()
            if self._state == "terminated":
                return self._drop_temporary(self.snapshot())
            event = self._adapter.wait_event(0.2) if self._connected else None
            if event is not None and event.get("event") == "stopped":
                return self._on_stop(event)
            if event is not None and event.get("event") in {"terminated", "exited"}:
                self._state = "terminated"
                self._stop_reason = "exited"
                return self._drop_temporary(self.snapshot())
            if startup and self._http_open():
                self._state = "running"
                self._stop_reason = None
                return self.snapshot()
        raise DebugError(
            "wait-timeout" if not startup else "protocol-failure",
            "timed out waiting for the debug target",
            state=self._state,
            snapshot=self.snapshot(),
        )

    def _on_stop(self, event: Mapping[str, JsonValue]) -> DebugSnapshot:
        body = event.get("body")
        reason = body.get("reason") if isinstance(body, dict) else None
        self._state = "stopped"
        mapped = (
            _STOP_REASONS.get(reason, "breakpoint") if isinstance(reason, str) else "breakpoint"
        )
        self._stop_reason = mapped
        return self._drop_temporary(self.snapshot())

    def _drop_temporary(self, snapshot: DebugSnapshot) -> DebugSnapshot:
        if any(item.temporary for item in self._breakpoints):
            self._breakpoints = [item for item in self._breakpoints if not item.temporary]
            if self._connected and self._state != "terminated":
                self._apply_breakpoints()
            self._persist_breakpoints()
        return snapshot

    def _observe_target(self) -> None:
        if self._target is not None and self._target.poll() is not None:
            self._state = "terminated"
            self._stop_reason = "exited"

    def _require_stopped(self) -> None:
        self._observe_target()
        if self._state != "stopped":
            raise DebugError(
                "not-stopped",
                "the debug target is not stopped",
                state=self._state,
                snapshot=self.snapshot(),
            )

    def snapshot(self) -> DebugSnapshot:
        """Return the current bounded snapshot without adapter identifiers."""
        self._observe_target()
        frames = self._adapter.frames() if self._state == "stopped" and self._connected else ()
        public_frames = tuple(
            DebugFrame(
                frame=index,
                path=display_path(path, self._cwd),
                line=line,
                function=function,
            )
            for index, path, line, function in frames
        )
        stack, stack_truncated = bound_stack(public_frames)
        location = None
        source = None
        locals_: tuple[DebugLocal, ...] = ()
        locals_truncated = False
        if frames:
            _index, path, line, function = frames[0]
            location = DebugLocation(
                path=display_path(path, self._cwd), line=line, function=function
            )
            source = source_window(Path(path), line) if path else None
            raw_locals = tuple(
                (name, value, type_name) for name, value, type_name, _ref in self._adapter.locals(0)
            )
            locals_, locals_truncated = bound_locals(raw_locals)
        stdout, stderr = "", ""
        if self._target is not None:
            tails = self._target.drain_tails()
            stdout, stderr = tails.get("stdout", ""), tails.get("stderr", "")
        output = bound_output(stdout, stderr)
        return DebugSnapshot(
            session_id=self._session_id,
            environment_id=self._environment_id,
            state=self._state,
            stop_reason=self._stop_reason,
            location=location,
            source=source,
            locals=locals_,
            stack=stack,
            breakpoints=tuple(_public_breakpoint(item, self._cwd) for item in self._breakpoints),
            output=output,
            trigger=self._trigger_snapshot(),
            truncated=DebugTruncation(
                locals=locals_truncated,
                stack=stack_truncated,
                source=bool(source and source.truncated),
                output=output.truncated,
            ),
        )

    def _trigger_snapshot(self) -> DebugTrigger | None:
        if self._trigger is None:
            return None
        code = self._trigger.poll()
        return DebugTrigger(
            state="running" if code is None else "exited",
            pid=self._trigger.pid,
            exit_code=code,
        )

    def _http_open(self) -> bool:
        if self._http_port <= 0:
            return False
        try:
            with socket.create_connection((self._http_interface, self._http_port), 0.2):
                return True
        except OSError:
            return False

    def _load(self) -> None:
        payload = json.loads((self._directory / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise DebugError("protocol-failure", "debug launch manifest is invalid")
        argv = payload.get("target_argv")
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise DebugError("protocol-failure", "debug launch manifest has no target argv")
        self._argv = tuple(cast("list[str]", argv))
        self._session_id = str(payload.get("session_id") or "")
        self._environment_id = str(payload.get("environment_id") or "")
        cwd = payload.get("cwd")
        self._cwd = Path(cwd) if isinstance(cwd, str) else self._directory
        interface = payload.get("http_interface")
        port = payload.get("http_port")
        self._http_interface = interface if isinstance(interface, str) else "127.0.0.1"
        self._http_port = port if isinstance(port, int) else 0
        secret = payload.get("secret_path")
        self._secret = secret if isinstance(secret, str) else None
        stored = _objects(payload.get("breakpoints"))
        self._breakpoints = [_stored_breakpoint(item) for item in stored]

    def _persist_breakpoints(self) -> None:
        path = self._directory / "manifest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        payload["breakpoints"] = [
            {
                "path": item.path,
                "line": item.line,
                "condition": item.condition,
                "temporary": item.temporary,
            }
            for item in self._breakpoints
        ]
        write_session_file(path, cast("Mapping[str, JsonValue]", payload))


def _timeout(params: Mapping[str, JsonValue]) -> float:
    raw = params.get("timeout", 30)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0 or raw > 300:
        raise DebugError("wait-timeout", "wait timeout must be from 0 through 300 seconds")
    return float(raw)


def _objects(value: JsonValue) -> tuple[Mapping[str, JsonValue], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _point(item: Mapping[str, JsonValue]) -> tuple[str, int]:
    path = item.get("path")
    line = item.get("line")
    if not isinstance(path, str) or isinstance(line, bool) or not isinstance(line, int):
        raise DebugError("breakpoint-invalid", "breakpoint path and line are required")
    return path, line


def _new_breakpoint(item: Mapping[str, JsonValue]) -> _Breakpoint:
    path, line = _point(item)
    if line <= 0 or not Path(path).is_file():
        raise DebugError(
            "breakpoint-invalid",
            f"breakpoint file does not exist: {path}",
            hint="Pass a path relative to the environment runtime directory",
        )
    condition = item.get("condition")
    return _Breakpoint(
        path=path,
        line=line,
        condition=condition if isinstance(condition, str) and condition else None,
        temporary=item.get("temporary") is True,
    )


def _stored_breakpoint(item: Mapping[str, JsonValue]) -> _Breakpoint:
    path, line = _point(item)
    condition = item.get("condition")
    return _Breakpoint(
        path=path,
        line=line,
        condition=condition if isinstance(condition, str) and condition else None,
        temporary=item.get("temporary") is True,
    )


def _public_breakpoint(item: _Breakpoint, root: Path) -> DebugBreakpoint:
    return DebugBreakpoint(
        path=display_path(item.path, root),
        line=item.line,
        condition=item.condition,
        verified=item.verified,
        message=item.message,
    )
