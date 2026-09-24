"""Concurrent pipe drain for spawned long-running handles."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import IO, cast

from .redaction import redacted_projection

_TIMEOUT_TAIL_BYTES = 8192
_CLEANUP_TIMEOUT = 5.0


@dataclass(slots=True)
class StreamTail:
    """Bounded raw byte tail for one drained pipe."""

    buffer: bytearray = field(default_factory=bytearray)
    truncated: bool = False

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        if len(self.buffer) + len(chunk) > _TIMEOUT_TAIL_BYTES:
            self.truncated = True
            self.buffer[:] = (bytes(self.buffer) + chunk)[-_TIMEOUT_TAIL_BYTES:]
        else:
            self.buffer.extend(chunk)

    @property
    def value(self) -> bytes:
        return bytes(self.buffer)


class PipeDrain:
    """Concurrent, continuous drain of both pipes for a long-running handle.

    Two daemon threads read ``stdout`` and ``stderr`` continuously so a noisy
    auxiliary child cannot fill the OS pipe buffer and block.  Each stream
    keeps only the last ``_TIMEOUT_TAIL_BYTES`` raw bytes; redaction is applied
    when the tail is projected for diagnostics.  Drained bytes are never
    written to CLI stdout.  The readers terminate when the pipes hit EOF
    (process group killed) or :meth:`stop` closes them.
    """

    __slots__ = ("_lock", "_stopped", "_streams", "_tails", "_threads")

    def __init__(self, stdout: IO[bytes] | None, stderr: IO[bytes] | None) -> None:
        self._tails: dict[str, StreamTail] = {
            "stdout": StreamTail(),
            "stderr": StreamTail(),
        }
        self._streams: dict[str, IO[bytes] | None] = {"stdout": stdout, "stderr": stderr}
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._stopped = False

    def start(self) -> None:
        for name in ("stdout", "stderr"):
            stream = self._streams[name]
            if stream is None:
                continue
            thread = threading.Thread(
                target=self._pump,
                args=(name, stream),
                name=f"proc-drain-{name}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _pump(self, name: str, stream: IO[bytes]) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                with self._lock:
                    self._tails[name].feed(chunk)
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError, ValueError):
                stream.close()

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        for stream in self._streams.values():
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()
        for thread in self._threads:
            thread.join(timeout=_CLEANUP_TIMEOUT)

    def join(self, timeout: float | None = _CLEANUP_TIMEOUT) -> None:
        """Wait for readers to observe EOF without closing their streams."""
        for thread in self._threads:
            thread.join(timeout=timeout)

    def tails(self) -> dict[str, StreamTail]:
        with self._lock:
            return {
                name: StreamTail(buffer=bytearray(tail.buffer), truncated=tail.truncated)
                for name, tail in self._tails.items()
            }

    def redacted_tails(self, *, secrets: Sequence[str] = ()) -> dict[str, str]:
        """Return the bounded, redacted tail projection for diagnostics."""
        tails = self.tails()
        projected: dict[str, str] = {}
        for name in ("stdout", "stderr"):
            tail = tails[name]
            if not tail.value:
                projected[name] = ""
                continue
            projected[name] = cast(
                "str",
                redacted_projection(tail.value, secrets=secrets, field=name),
            )
        return projected


__all__ = ["PipeDrain", "StreamTail"]
