"""Private session directory and newline JSON RPC."""

from __future__ import annotations

import json
import os
import socket
import threading
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import msgspec
import psutil

from odoo_instance_sdk.exceptions import DebugError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.debug.limits import PROTOCOL_VERSION
from odoo_instance_sdk.internal.paths import get_data_root
from odoo_instance_sdk.models.debug import DebugSnapshot

RpcHandler = Callable[[Mapping[str, JsonValue], threading.Event], JsonValue]


def session_directory(environment_id: str) -> Path:
    """Return the private directory for one environment debug session."""
    return get_data_root(ensure_exists=False) / "debug" / _safe_id(environment_id)


def claim_session(environment_id: str) -> Path:
    """Create the session directory or report that a live daemon already owns it."""
    path = session_directory(environment_id)
    if path.exists():
        if session_is_live(path):
            raise DebugError(
                "session-already-active",
                "a debug session is already running for this environment",
                hint="Use debug context, continue, or stop",
            )
        _remove_tree(path)
    path.mkdir(mode=0o700, parents=True)
    os.chmod(path, 0o700)
    parent = path.parent
    if parent.exists():
        os.chmod(parent, 0o700)
    return path


def release_session(environment_id: str) -> None:
    """Remove one session directory and leave every other directory in place."""
    path = session_directory(environment_id)
    _remove_tree(path)


def session_is_live(path: Path) -> bool:
    """Return whether metadata names a process that still has the recorded start time."""
    metadata = read_metadata(path)
    if metadata is None:
        return False
    pid = metadata.get("pid")
    started = metadata.get("pid_start")
    if not isinstance(pid, int) or not isinstance(started, int | float):
        return False
    return _pid_start(pid) == float(started)


def write_metadata(path: Path, payload: Mapping[str, JsonValue]) -> None:
    """Write session metadata with mode 0600."""
    _write_private(path / "metadata.json", payload)


def read_metadata(path: Path) -> dict[str, JsonValue] | None:
    """Return metadata, or None when the file is missing or unreadable."""
    file = path / "metadata.json"
    try:
        parsed = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return cast("dict[str, JsonValue]", parsed)


def current_pid_metadata(
    environment_id: str, session_id: str, socket_path: Path
) -> dict[str, JsonValue]:
    """Build metadata for the current daemon process."""
    pid = os.getpid()
    started = _pid_start(pid)
    return {
        "protocol": PROTOCOL_VERSION,
        "session_id": session_id,
        "environment_id": environment_id,
        "pid": pid,
        "pid_start": started if started is not None else 0,
        "socket": str(socket_path),
    }


def rpc_call(
    socket_path: Path,
    method: str,
    params: Mapping[str, JsonValue],
    *,
    timeout: float,
) -> JsonValue:
    """Send one versioned request and return the result or raise ``DebugError``."""
    message = {
        "protocol": PROTOCOL_VERSION,
        "id": uuid.uuid4().hex,
        "method": method,
        "params": dict(params),
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(str(socket_path))
            sock.sendall(json.dumps(message).encode() + b"\n")
            raw = _read_line(sock, timeout)
        except OSError as exc:
            raise DebugError("protocol-failure", "debug session socket is unavailable") from exc
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise DebugError("protocol-failure", "debug session returned a non-object")
    if parsed.get("ok") is True:
        return cast("JsonValue", parsed.get("result"))
    error = parsed.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        text = error.get("message")
        raw_snapshot = error.get("snapshot")
        snapshot = (
            msgspec.convert(raw_snapshot, DebugSnapshot) if isinstance(raw_snapshot, dict) else None
        )
        raise DebugError(
            code if isinstance(code, str) else "protocol-failure",
            text if isinstance(text, str) else "debug session failed",
            hint=error.get("hint") if isinstance(error.get("hint"), str) else None,
            state=error.get("state") if isinstance(error.get("state"), str) else None,
            snapshot=snapshot,
        )
    raise DebugError("protocol-failure", "debug session returned an invalid error")


def serve_rpc(socket_path: Path, handler: RpcHandler, stop: threading.Event) -> None:
    """Accept one JSON request per connection until ``stop`` is set."""
    if socket_path.exists():
        socket_path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    server.listen(8)
    server.settimeout(0.2)
    try:
        while not stop.is_set():
            try:
                connection, _address = server.accept()
            except TimeoutError:
                continue
            threading.Thread(
                target=_handle_connection,
                args=(connection, handler),
                name="odcli-debug-rpc",
                daemon=True,
            ).start()
    finally:
        server.close()
        if socket_path.exists():
            socket_path.unlink()


def dispatch_message(
    message: Mapping[str, JsonValue],
    handler: RpcHandler,
    cancel: threading.Event | None = None,
) -> dict[str, JsonValue]:
    """Validate protocol version, then let the handler build the result."""
    if message.get("protocol") != PROTOCOL_VERSION:
        return _error_body(
            "protocol-mismatch", "debug RPC protocol does not match", hint=None, state=None
        )
    method = message.get("method")
    params = message.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        return _error_body(
            "protocol-failure", "debug RPC request is invalid", hint=None, state=None
        )
    try:
        result = handler(
            cast("Mapping[str, JsonValue]", {"method": method, "params": params}),
            cancel or threading.Event(),
        )
    except DebugError as exc:
        body = _error_body(exc.code, str(exc), hint=exc.hint, state=exc.state)
        if exc.snapshot is not None:
            error = body["error"]
            if isinstance(error, dict):
                error["snapshot"] = cast("JsonValue", msgspec.to_builtins(exc.snapshot))
        return body
    return {"protocol": PROTOCOL_VERSION, "ok": True, "result": result}


def _handle_connection(connection: socket.socket, handler: RpcHandler) -> None:
    with connection:
        cancel = threading.Event()
        finished = threading.Event()
        try:
            raw = _read_line(connection, 30)
            message = json.loads(raw)
            watcher = threading.Thread(
                target=_watch_disconnect,
                args=(connection, cancel, finished),
                daemon=True,
            )
            watcher.start()
            if not isinstance(message, dict):
                body = _error_body("protocol-failure", "debug RPC request is invalid", None, None)
            else:
                body = dispatch_message(cast("Mapping[str, JsonValue]", message), handler, cancel)
            finished.set()
            watcher.join(timeout=1)
            if not cancel.is_set():
                connection.settimeout(30)
                connection.sendall(json.dumps(body).encode() + b"\n")
        except (OSError, json.JSONDecodeError, TimeoutError, DebugError) as exc:
            cancel.set()
            finished.set()
            try:
                connection.sendall(
                    json.dumps(
                        _error_body("protocol-failure", str(exc), hint=None, state=None)
                    ).encode()
                    + b"\n"
                )
            except OSError:
                return


def _watch_disconnect(
    connection: socket.socket, cancel: threading.Event, finished: threading.Event
) -> None:
    connection.settimeout(0.2)
    while not cancel.is_set() and not finished.is_set():
        try:
            peeked = connection.recv(1, socket.MSG_PEEK)
        except TimeoutError:
            continue
        except OSError:
            cancel.set()
            return
        if peeked == b"":
            cancel.set()
            return


def _error_body(
    code: str, message: str, hint: str | None, state: str | None
) -> dict[str, JsonValue]:
    error: dict[str, JsonValue] = {"code": code, "message": message}
    if hint is not None:
        error["hint"] = hint
    if state is not None:
        error["state"] = state
    return {"protocol": PROTOCOL_VERSION, "ok": False, "error": error}


def _read_line(sock: socket.socket, timeout: float) -> str:
    sock.settimeout(timeout)
    chunks = bytearray()
    while b"\n" not in chunks:
        chunk = sock.recv(65536)
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > 1_000_000:
            raise DebugError("protocol-failure", "debug RPC message is too large")
    line, _separator, _rest = bytes(chunks).partition(b"\n")
    return line.decode()


def write_session_file(path: Path, payload: Mapping[str, JsonValue]) -> None:
    """Atomically write one mode-0600 JSON file inside a session directory."""
    _write_private(path, payload)


def _write_private(path: Path, payload: Mapping[str, JsonValue]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            child.rmdir()
    path.rmdir()


def _safe_id(value: str) -> str:
    if value and all(char.isalnum() or char in "_-" for char in value):
        return value
    raise DebugError("session-not-found", "environment id is not a safe session key")


def _pid_start(pid: int) -> float | None:
    try:
        return float(psutil.Process(pid).create_time())
    except psutil.Error:
        return None
