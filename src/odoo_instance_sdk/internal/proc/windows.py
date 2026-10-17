"""Windows-specific captured process support."""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from collections.abc import Callable
from typing import IO, Literal, cast

from . import PreparedStep, StepEvent, StepObserver, event_for_step
from .redaction import IncrementalStreamRedactor, captured_secret_values, redacted_projection

_TIMEOUT_TAIL_BYTES = 8192
_READ_CHUNK_BYTES = 64 * 1024


def _notify_observer(observer: StepObserver | None, event: StepEvent) -> None:
    """Use the shared non-semantic observer failure boundary."""
    if observer is None:
        return
    from .run import _notify

    _notify(observer, event)


def _output_limit_error(
    prepared: PreparedStep,
    *,
    duration: float,
    stdout_tail: bytes,
    stderr_tail: bytes,
    stdout_truncated: bool,
    stderr_truncated: bool,
) -> BaseException:
    from .run import ProcessOutputLimitError

    secrets = captured_secret_values(prepared)
    error = ProcessOutputLimitError(
        prepared.argv,
        "output exceeded configured limit",
        duration=duration,
        secrets=secrets,
        sensitive_indices=prepared.sensitive_argv_indices,
    )
    setattr(
        error,
        "stdout_tail",
        cast("str", redacted_projection(stdout_tail, secrets=secrets, field="stdout")),
    )
    setattr(
        error,
        "stderr_tail",
        cast("str", redacted_projection(stderr_tail, secrets=secrets, field="stderr")),
    )
    setattr(error, "stdout_truncated", stdout_truncated)
    setattr(error, "stderr_truncated", stderr_truncated)
    return error


def run_windows_communicate(  # noqa: C901
    process: subprocess.Popen[bytes],
    prepared: PreparedStep,
    *,
    timeout: float | None,
    observer: StepObserver | None,
    observe_output: bool,
    max_output_bytes: int | None,
) -> tuple[int, bytes, bytes, float]:
    """Capture Windows pipes with bounded concurrent readers.

    ``select`` cannot poll Windows pipe handles and ``communicate`` buffers
    them until the child exits. Dedicated readers keep both pipes moving and
    enforce limits while the child is still running.
    """
    from .run import ProcessTimeoutError

    started = time.perf_counter()
    streams: tuple[tuple[Literal["stdout", "stderr"], IO[bytes] | None], ...] = (
        ("stdout", process.stdout),
        ("stderr", process.stderr),
    )
    buffers = {name: bytearray() for name, _pipe in streams}
    tails = {name: bytearray() for name, _pipe in streams}
    tail_truncated = dict.fromkeys((name for name, _pipe in streams), False)
    totals = dict.fromkeys((name for name, _pipe in streams), 0)
    combined_total = 0
    state_lock = threading.Lock()
    observer_lock = threading.Lock()
    stop = threading.Event()
    overflow = threading.Event()
    timed_out = threading.Event()
    redactors: dict[str, IncrementalStreamRedactor] = {}
    if observer is not None and observe_output:
        secrets = captured_secret_values(prepared)
        redactors = {
            stream: IncrementalStreamRedactor(secrets=secrets, field=stream)
            for stream, _pipe in streams
        }

    def notify_chunk(stream: Literal["stdout", "stderr"], chunk: bytes) -> None:
        if observer is None or not observe_output:
            return
        with observer_lock:
            safe = redactors[stream].feed(chunk)
            if safe:
                _notify_observer(observer, event_for_step(prepared, stream, chunk=safe))

    def append_tail(stream: Literal["stdout", "stderr"], chunk: bytes) -> None:
        tail = tails[stream]
        if len(tail) + len(chunk) > _TIMEOUT_TAIL_BYTES:
            tail_truncated[stream] = True
            tail[:] = (tail + chunk)[-_TIMEOUT_TAIL_BYTES:]
        else:
            tail.extend(chunk)

    reap_lock = threading.Lock()

    def terminate_and_reap() -> None:
        with reap_lock:
            if process.poll() is None:
                with contextlib.suppress(OSError):
                    process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    process.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=1.0)

    def read_stream(stream: Literal["stdout", "stderr"], pipe: IO[bytes] | None) -> None:
        nonlocal combined_total
        if pipe is None:
            return
        reader = pipe
        read_chunk: Callable[[int], bytes] = getattr(reader, "read1", reader.read)
        try:
            while not stop.is_set():
                chunk = read_chunk(_READ_CHUNK_BYTES)
                if not chunk:
                    return
                with state_lock:
                    next_stream_total = totals[stream] + len(chunk)
                    next_combined_total = combined_total + len(chunk)
                    stream_exceeded = (
                        max_output_bytes is not None and next_stream_total > max_output_bytes
                    )
                    combined_exceeded = (
                        prepared.max_combined_output_bytes is not None
                        and next_combined_total > prepared.max_combined_output_bytes
                    )
                    totals[stream] = next_stream_total
                    combined_total = next_combined_total
                    append_tail(stream, chunk)
                    if stream_exceeded or combined_exceeded:
                        overflow.set()
                    else:
                        buffers[stream].extend(chunk)
                notify_chunk(stream, chunk)
                if overflow.is_set():
                    stop.set()
                    terminate_and_reap()
                    return
        except (OSError, ValueError):
            if not stop.is_set() and process.poll() is None:
                stop.set()
                terminate_and_reap()

    def write_input() -> None:
        if process.stdin is None or prepared.stdin is None:
            return
        try:
            process.stdin.write(prepared.stdin)
            process.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            with contextlib.suppress(OSError, ValueError):
                process.stdin.close()

    readers = [
        threading.Thread(target=read_stream, args=(name, pipe), daemon=True)
        for name, pipe in streams
    ]
    writer = threading.Thread(target=write_input, daemon=True)
    for reader in readers:
        reader.start()
    writer.start()

    try:
        while process.poll() is None and not overflow.is_set():
            remaining = None if timeout is None else timeout - (time.perf_counter() - started)
            if remaining is not None and remaining <= 0:
                timed_out.set()
                stop.set()
                terminate_and_reap()
                break
            stop.wait(0.02 if remaining is None else min(0.02, remaining))
        if overflow.is_set():
            stop.set()
            terminate_and_reap()
        elif not timed_out.is_set():
            process.wait()
    finally:
        if overflow.is_set() or timed_out.is_set():
            stop.set()
        with contextlib.suppress(OSError, ValueError):
            if process.stdin is not None:
                process.stdin.close()
        for reader in readers:
            reader.join(timeout=1.0)
        writer.join(timeout=1.0)
        stop.set()
        for _name, pipe in streams:
            with contextlib.suppress(OSError, ValueError):
                if pipe is not None:
                    pipe.close()

    if observer is not None and observe_output:
        with observer_lock:
            for stream, _pipe in streams:
                safe = redactors[stream].flush()
                if safe:
                    _notify_observer(observer, event_for_step(prepared, stream, chunk=safe))

    if timed_out.is_set():
        raise ProcessTimeoutError(
            prepared.argv,
            timeout if timeout is not None else 0.0,
            duration=time.perf_counter() - started,
            secrets=captured_secret_values(prepared),
            sensitive_indices=prepared.sensitive_argv_indices,
            stdout_tail=bytes(tails["stdout"]),
            stderr_tail=bytes(tails["stderr"]),
            stdout_truncated=tail_truncated["stdout"],
            stderr_truncated=tail_truncated["stderr"],
        )
    if overflow.is_set():
        raise _output_limit_error(
            prepared,
            duration=time.perf_counter() - started,
            stdout_tail=bytes(tails["stdout"]),
            stderr_tail=bytes(tails["stderr"]),
            stdout_truncated=tail_truncated["stdout"],
            stderr_truncated=tail_truncated["stderr"],
        )
    return (
        process.returncode,
        bytes(buffers["stdout"]),
        bytes(buffers["stderr"]),
        time.perf_counter() - started,
    )
