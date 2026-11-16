"""Translate the private DAP client into session operations."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from queue import Empty
from typing import cast

from odoo_instance_sdk.exceptions import DebugError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.debug.dap import DapClient
from odoo_instance_sdk.internal.debug.limits import MAX_CHILDREN, MAX_EVAL_DEPTH
from odoo_instance_sdk.internal.debug.snapshot import redact_text
from odoo_instance_sdk.models.debug import DebugChild

RawFrame = tuple[int, str, int, str]
RawVariable = tuple[str, str, str, int]


class DebugAdapter:
    """debugpy operations used by one headless session."""

    def __init__(self) -> None:
        self._client: DapClient | None = None
        self._thread_id: int | None = None
        self._frames: tuple[dict[str, JsonValue], ...] = ()

    def connect(self, port: int) -> None:
        """Attach to a loopback debugpy listener and finish configuration."""
        self._client = DapClient.connect(port)
        self._client.request(
            "initialize",
            {
                "adapterID": "debugpy",
                "clientID": "odcli",
                "clientName": "odcli",
                "linesStartAt1": True,
                "columnsStartAt1": True,
                "pathFormat": "path",
            },
        )
        self._client.request("attach", {"justMyCode": False}, defer=True)
        self._wait_for("initialized", 10)

    def set_breakpoints(
        self, groups: Mapping[str, tuple[tuple[int, str | None], ...]]
    ) -> tuple[tuple[str, int, bool, str | None], ...]:
        """Replace the breakpoint set for each source and return verification."""
        verified: list[tuple[str, int, bool, str | None]] = []
        client = self._require()
        for path, entries in groups.items():
            body = client.request(
                "setBreakpoints",
                {
                    "source": {"path": path},
                    "breakpoints": [
                        {"line": line, "condition": condition} if condition else {"line": line}
                        for line, condition in entries
                    ],
                },
            )
            reported = body.get("breakpoints")
            if not isinstance(reported, list):
                continue
            for item, (line, _condition) in zip(reported, entries, strict=False):
                if not isinstance(item, dict):
                    continue
                message = item.get("message")
                verified.append(
                    (
                        path,
                        line,
                        item.get("verified") is True,
                        message if isinstance(message, str) else None,
                    )
                )
        return tuple(verified)

    def configuration_done(self) -> None:
        """Allow a ``--wait-for-client`` target to run."""
        client = self._require()
        client.request("configurationDone", {})
        client.finish_deferred()

    def wait_event(self, timeout: float) -> dict[str, JsonValue] | None:
        """Return the next DAP event, or None when the timeout expires."""
        client = self._require()
        try:
            event = client.events.get(timeout=timeout)
        except Empty:
            return None
        if event.get("event") == "stopped":
            body = event.get("body")
            if isinstance(body, dict) and isinstance(body.get("threadId"), int):
                self._thread_id = cast("int", body["threadId"])
        return event

    def resume(self, kind: str) -> None:
        """Continue or step the current thread."""
        command = {"continue": "continue", "over": "next", "in": "stepIn", "out": "stepOut"}[kind]
        self._require().request(command, {"threadId": self._thread()})

    def pause(self) -> None:
        """Request a pause on the current thread."""
        self._require().request("pause", {"threadId": self._thread()})

    def frames(self) -> tuple[RawFrame, ...]:
        """Return stack frames as function, path, and line, dropping adapter ids from the result."""
        body = self._require().request(
            "stackTrace", {"threadId": self._thread(), "startFrame": 0, "levels": 20}
        )
        raw = body.get("stackFrames")
        if not isinstance(raw, list):
            return ()
        self._frames = tuple(item for item in raw if isinstance(item, dict))
        result: list[RawFrame] = []
        for index, item in enumerate(self._frames):
            source = item.get("source")
            path = ""
            if isinstance(source, dict) and isinstance(source.get("path"), str):
                path = cast("str", source["path"])
            line = item.get("line")
            name = item.get("name")
            result.append(
                (
                    index,
                    path,
                    line if isinstance(line, int) else 0,
                    name if isinstance(name, str) else "",
                )
            )
        return tuple(result)

    def locals(self, frame: int) -> tuple[RawVariable, ...]:
        """Return shallow variables for one frame index."""
        reference = self._scope_reference(frame, "Locals")
        if reference is None:
            return ()
        return self._variables(reference)

    def evaluate(
        self, expression: str, frame: int, depth: int
    ) -> tuple[str, str, tuple[DebugChild, ...], bool]:
        """Evaluate in one frame and expand children only up to ``depth``."""
        if depth < 0 or depth > MAX_EVAL_DEPTH:
            raise DebugError("protocol-failure", "eval depth must be from 0 through 3")
        frame_id = self._frame_id(frame)
        body = self._require().request(
            "evaluate",
            {"expression": expression, "frameId": frame_id, "context": "repl"},
        )
        value = body.get("result")
        type_name = body.get("type")
        reference = body.get("variablesReference")
        children, truncated = self._expand(
            reference if isinstance(reference, int) else 0,
            depth,
        )
        return (
            redact_text(expression, value if isinstance(value, str) else ""),
            type_name if isinstance(type_name, str) else "",
            children,
            truncated,
        )

    def close(self) -> None:
        """Disconnect without asking the adapter to signal an unrelated process."""
        client = self._client
        self._client = None
        if client is None:
            return
        with contextlib.suppress(DebugError):
            client.request("disconnect", {"terminateDebuggee": False}, timeout=2)
        client.close()

    def _expand(self, reference: int, depth: int) -> tuple[tuple[DebugChild, ...], bool]:
        if depth <= 0 or reference <= 0:
            return (), False
        rows = self._variables(reference)
        truncated = len(rows) > MAX_CHILDREN
        children: list[DebugChild] = []
        for name, value, type_name, child_reference in rows[:MAX_CHILDREN]:
            nested, nested_truncated = self._expand(child_reference, depth - 1)
            truncated = truncated or nested_truncated
            children.append(
                DebugChild(
                    name=name,
                    value=redact_text(name, value),
                    type=type_name,
                    children=nested,
                )
            )
        return tuple(children), truncated

    def _variables(self, reference: int) -> tuple[RawVariable, ...]:
        body = self._require().request("variables", {"variablesReference": reference})
        raw = body.get("variables")
        if not isinstance(raw, list):
            return ()
        rows: list[RawVariable] = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            value = item.get("value")
            type_name = item.get("type")
            child = item.get("variablesReference")
            rows.append(
                (
                    cast("str", item["name"]),
                    value if isinstance(value, str) else "",
                    type_name if isinstance(type_name, str) else "",
                    child if isinstance(child, int) else 0,
                )
            )
        return tuple(rows)

    def _scope_reference(self, frame: int, name: str) -> int | None:
        frame_id = self._frame_id(frame)
        body = self._require().request("scopes", {"frameId": frame_id})
        scopes = body.get("scopes")
        if not isinstance(scopes, list):
            return None
        chosen: dict[str, JsonValue] | None = None
        for scope in scopes:
            if isinstance(scope, dict) and scope.get("name") == name:
                chosen = scope
                break
        if chosen is None:
            for scope in scopes:
                if isinstance(scope, dict):
                    chosen = scope
                    break
        if chosen is None or not isinstance(chosen.get("variablesReference"), int):
            return None
        return cast("int", chosen["variablesReference"])

    def _frame_id(self, frame: int) -> int:
        if not self._frames:
            self.frames()
        if frame < 0 or frame >= len(self._frames):
            raise DebugError("not-stopped", "frame is outside the current stack", state=None)
        raw = self._frames[frame].get("id")
        if not isinstance(raw, int):
            raise DebugError("protocol-failure", "DAP frame has no id")
        return raw

    def _thread(self) -> int:
        if self._thread_id is not None:
            return self._thread_id
        body = self._require().request("threads", {})
        threads = body.get("threads")
        if isinstance(threads, list):
            for thread in threads:
                if isinstance(thread, dict) and isinstance(thread.get("id"), int):
                    self._thread_id = cast("int", thread["id"])
                    return self._thread_id
        raise DebugError("protocol-failure", "debugpy reported no thread")

    def _wait_for(self, name: str, timeout: float) -> None:
        deadline = timeout
        while deadline > 0:
            event = self.wait_event(min(0.2, deadline))
            deadline -= 0.2
            if event is not None and event.get("event") == name:
                return
        raise DebugError("protocol-failure", f"DAP {name} event did not arrive")

    def _require(self) -> DapClient:
        if self._client is None:
            raise DebugError("protocol-failure", "DAP client is not connected")
        return self._client
