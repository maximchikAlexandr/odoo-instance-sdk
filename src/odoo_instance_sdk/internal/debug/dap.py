"""Private debugpy DAP client for the headless session."""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from collections.abc import Mapping
from queue import Empty, Queue
from typing import cast

from odoo_instance_sdk.exceptions import DebugError
from odoo_instance_sdk.execution import JsonValue

_HEADER = b"\r\n\r\n"


def encode_frame(payload: Mapping[str, JsonValue]) -> bytes:
    """Encode one DAP message with a Content-Length header."""
    body = json.dumps(payload, separators=(",", ":")).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


class FrameParser:
    """Reassemble DAP frames from arbitrary socket chunks."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, JsonValue]]:
        """Return every complete message contained in ``data`` plus the buffer."""
        self._buffer.extend(data)
        messages: list[dict[str, JsonValue]] = []
        while True:
            marker = self._buffer.find(_HEADER)
            if marker < 0:
                return messages
            header = bytes(self._buffer[:marker]).decode()
            length = _content_length(header)
            start = marker + len(_HEADER)
            if len(self._buffer) < start + length:
                return messages
            body = bytes(self._buffer[start : start + length])
            del self._buffer[: start + length]
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                messages.append(cast("dict[str, JsonValue]", parsed))
        return messages


def _content_length(header: str) -> int:
    for line in header.split("\r\n"):
        if line.lower().startswith("content-length:"):
            return int(line.split(":", 1)[1].strip())
    raise DebugError("protocol-failure", "DAP frame is missing Content-Length")


class DapClient:
    """One loopback DAP connection with correlated responses and events."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._seq = 0
        self._parser = FrameParser()
        self._pending: dict[int, Queue[dict[str, JsonValue]]] = {}
        self._deferred: tuple[Queue[dict[str, JsonValue]], str] | None = None
        self.events: Queue[dict[str, JsonValue]] = Queue()
        self._error: DebugError | None = None
        self._reader = threading.Thread(target=self._read_loop, name="odcli-dap", daemon=True)
        self._reader.start()

    @classmethod
    def connect(cls, port: int, *, timeout: float = 10.0) -> DapClient:
        """Open a loopback TCP connection to debugpy."""
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        sock.settimeout(None)
        return cls(sock)

    def request(
        self,
        command: str,
        arguments: Mapping[str, JsonValue] | None = None,
        *,
        timeout: float = 10.0,
        defer: bool = False,
    ) -> dict[str, JsonValue]:
        """Send one request and return its correlated response body."""
        self._seq += 1
        seq = self._seq
        box: Queue[dict[str, JsonValue]] = Queue()
        self._pending[seq] = box
        payload: dict[str, JsonValue] = {"seq": seq, "type": "request", "command": command}
        if arguments is not None:
            payload["arguments"] = dict(arguments)
        try:
            self._sock.sendall(encode_frame(payload))
        except OSError as exc:
            raise DebugError("protocol-failure", "DAP connection closed") from exc
        if defer:
            self._deferred = (box, command)
            return {}
        return self._receive(box, command, timeout)

    def finish_deferred(self, timeout: float = 10.0) -> dict[str, JsonValue]:
        """Wait for the response of the one request sent with ``defer=True``."""
        if self._deferred is None:
            return {}
        box, command = self._deferred
        self._deferred = None
        return self._receive(box, command, timeout)

    def _receive(
        self, box: Queue[dict[str, JsonValue]], command: str, timeout: float
    ) -> dict[str, JsonValue]:
        try:
            message = box.get(timeout=timeout)
        except Empty as exc:
            raise DebugError("protocol-failure", f"DAP {command} timed out") from exc
        if message.get("success") is False:
            detail = message.get("message")
            text = detail if isinstance(detail, str) else command
            raise DebugError("protocol-failure", f"DAP {command} failed: {text}")
        body = message.get("body")
        if isinstance(body, dict):
            return body
        return {}

    def close(self) -> None:
        """Close the socket and stop the reader."""
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_RDWR)
        self._sock.close()

    def _read_loop(self) -> None:
        try:
            while True:
                chunk = self._sock.recv(65536)
                if not chunk:
                    closed = DebugError("protocol-failure", "DAP connection closed")
                    self._error = closed
                    self._fail_pending(closed)
                    return
                for message in self._parser.feed(chunk):
                    self._route(message)
        except DebugError as exc:
            self._error = exc
            self._fail_pending(exc)
        except OSError as exc:
            failure = DebugError("protocol-failure", "DAP connection closed")
            self._error = failure
            self._fail_pending(failure)
            del exc

    def _route(self, message: dict[str, JsonValue]) -> None:
        if message.get("type") == "response":
            request_seq = message.get("request_seq")
            if isinstance(request_seq, int):
                box = self._pending.pop(request_seq, None)
                if box is not None:
                    box.put(message)
                    return
        self.events.put(message)

    def _fail_pending(self, exc: DebugError) -> None:
        for box in self._pending.values():
            box.put({"success": False, "message": str(exc)})
        self._pending.clear()
